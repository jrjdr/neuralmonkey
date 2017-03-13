# tests: lint, mypy

from typing import Optional, Any, Tuple

import numpy as np
import tensorflow as tf
from typeguard import check_argument_types

from neuralmonkey.encoders.attentive import Attentive
from neuralmonkey.model.model_part import ModelPart, FeedDict
from neuralmonkey.logging import log
from neuralmonkey.nn.noisy_gru_cell import NoisyGRUCell
from neuralmonkey.nn.ortho_gru_cell import OrthoGRUCell
from neuralmonkey.dataset import Dataset

# pylint: disable=invalid-name
AttType = Any  # Type[] or union of types do not work here
RNNCellTuple = Tuple[tf.nn.rnn_cell.RNNCell, tf.nn.rnn_cell.RNNCell]
# pylint: enable=invalid-name


# pylint: disable=too-many-instance-attributes
class RawRNNEncoder(ModelPart, Attentive):
    """A raw RNN encoder that gets input as a tensor."""

    # pylint: disable=too-many-arguments,too-many-locals
    def __init__(self,
                 name: str,
                 data_id: str,
                 rnn_size: int,
                 input_dimension: int,
                 max_input_len: Optional[int]=None,
                 dropout_keep_prob: float=1.0,
                 attention_type: Optional[AttType]=None,
                 attention_fertility: int=3,
                 use_noisy_activations: bool=False,
                 parent_encoder: Optional["RawRNNEncoder"]=None,
                 save_checkpoint: Optional[str]=None,
                 load_checkpoint: Optional[str]=None) -> None:
        """Creates a new instance of the encoder.

        Arguments:
            data_id: Identifier of the data series fed to this encoder
            name: An unique identifier for this encoder
            rnn_size: The size of the encoder's hidden state. Note
                that the actual encoder output state size will be
                twice as long because it is the result of
                concatenation of forward and backward hidden states.

        Keyword arguments:
            dropout_keep_prob: The dropout keep probability
                (default 1.0)
            attention_type: The class that is used for creating
                attention mechanism (default None)
            attention_fertility: Fertility parameter used with
                CoverageAttention (default 3).
        """
        ModelPart.__init__(self, name, save_checkpoint, load_checkpoint)
        Attentive.__init__(
            self, attention_type, attention_fertility=attention_fertility)

        assert check_argument_types()

        self.data_id = data_id

        self.rnn_size = rnn_size
        self.max_input_len = max_input_len
        self.input_dimension = input_dimension
        self.dropout_keep_p = dropout_keep_prob
        self.use_noisy_activations = use_noisy_activations
        self.parent_encoder = parent_encoder

        log("Initializing RNN encoder, name: '{}'"
            .format(self.name))

        with tf.variable_scope(self.name):
            self._create_input_placeholders()

            self._input_mask = tf.sequence_mask(self._input_lengths,
                                                dtype=tf.float32)

            fw_cell, bw_cell = self.rnn_cells()  # type: RNNCellTuple
            outputs_bidi_tup, encoded_tup = tf.nn.bidirectional_dynamic_rnn(
                fw_cell, bw_cell, self.inputs, self._input_lengths,
                dtype=tf.float32)

            self.hidden_states = tf.concat(axis=2, values=outputs_bidi_tup)

            with tf.variable_scope('attention_tensor'):
                self.__attention_tensor = self._dropout(
                    self.hidden_states)

            self.encoded = tf.concat(axis=1, values=encoded_tup)

        log("RNN encoder initialized")

    @property
    def _attention_tensor(self):
        return self.__attention_tensor

    @property
    def _attention_mask(self):
        return self._input_mask

    def _create_input_placeholders(self):
        """Creates input placeholder nodes in the computation graph"""
        self.train_mode = tf.placeholder(tf.bool, shape=[],
                                         name="mode_placeholder")

        self.inputs = tf.placeholder(tf.float32,
                                     shape=[None, None,
                                            self.input_dimension],
                                     name="encoder_input")

        self._input_lengths = tf.placeholder(
            tf.int32, shape=[None],
            name="encoder_padding_lengths")

    def _dropout(self, variable: tf.Tensor) -> tf.Tensor:
        """Perform dropout on a variable

        Arguments:
            variable: The variable to be dropped out

        Returns:
            The dropped value of the variable
        """
        if self.dropout_keep_p == 1.0:
            return variable

        # TODO as soon as TF.12 is out, remove this line and use train_mode
        # directly
        train_mode_batch = tf.fill(tf.shape(variable)[:1], self.train_mode)
        dropped_value = tf.nn.dropout(variable, self.dropout_keep_p)
        return tf.where(train_mode_batch, dropped_value, variable)

    def rnn_cells(self) -> RNNCellTuple:
        """Return the graph template to for creating RNN memory cells"""

        if self.parent_encoder is not None:
            return self.parent_encoder.rnn_cells()

        if self.use_noisy_activations:
            return(NoisyGRUCell(self.rnn_size, self.train_mode),
                   NoisyGRUCell(self.rnn_size, self.train_mode))

        return (OrthoGRUCell(self.rnn_size),
                OrthoGRUCell(self.rnn_size))

    def feed_dict(self, dataset: Dataset, train: bool=False) -> FeedDict:
        """Populate the feed dictionary with the encoder inputs.

        Arguments:
            dataset: The dataset to use
            train: Boolean flag telling whether it is training time
        """
        # pylint: disable=invalid-name
        fd = {}  # type: FeedDict
        fd[self.train_mode] = train

        series = list(dataset.get_series(self.data_id))
        lengths = []
        inputs = []

        max_len = max(x.shape[0] for x in series)
        if self.max_input_len is not None:
            max_len = min(self.max_input_len, max_len)

        for x in series:
            length = min(max_len, x.shape[0])
            x_padded = np.zeros(shape=(max_len,) + x.shape[1:],
                                dtype=x.dtype)
            x_padded[:length] = x[:length]

            lengths.append(length)
            inputs.append(x_padded)

        fd[self.inputs] = inputs
        fd[self._input_lengths] = lengths

        return fd
