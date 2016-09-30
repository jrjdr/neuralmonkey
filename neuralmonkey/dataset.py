""" Implementation of the dataset class. """
# tests: lint, mypy
import random
import re
import collections

from typing import List, Callable, Iterable, Dict

import numpy as np
import magic

from neuralmonkey.logging import log
from neuralmonkey.readers.plain_text_reader import PlainTextFileReader

SERIES_SOURCE = re.compile("s_([^_]*)$")
SERIES_OUTPUT = re.compile("s_(.*)_out")

def load_dataset_from_files(name: str=None, lazy: bool=False,
                            preprocessor: Callable[[str], str]=lambda x: x,
                            **kwargs: str) -> 'Dataset':
    """Load a dataset from the files specified by the provided arguments.
    Paths to the data are provided in a form of dictionary.

    Keyword arguments:
        name: The name of the dataset to use. If None (default), the name will
              be inferred from the file names.
        lazy: Boolean flag specifying whether to use lazy loading (useful for
              large files). Note that the lazy dataset cannot be shuffled.
              Defaults to False.
        preprocessor: A callable used for preprocessing of the input sentences.
        kwargs: Dataset keyword argument specs. These parameters should begin
                with 's_' prefix and may end with '_out' suffix.
                For example, a data series 'source' which specify the source
                sentences should be initialized with the 's_source' parameter,
                which specifies the path to the source file.
                If the decoder generate data of the 'target' series, the output
                file should be initialized with the 's_target_out' parameter.
                Series identifiers should not contain underscores.

    Returns:
        The newly created dataset.

    Raises:
        Exception when no input files are provided.
    """
    series_paths = _get_series_paths(kwargs)
    series_outputs = _get_series_outputs(kwargs)

    if len(series_paths) == 0:
        raise Exception("No input files are provided.")

    log("Initializing dataset with: {}".format(", ".join(series_paths)))

    if name is None:
        name = _get_name_from_paths(series_paths)

    if lazy:
        return LazyDataset(name, series_paths, series_outputs, preprocessor)

    series = {key: list(create_dataset_series(path, preprocessor))
              for key, path in series_paths.items()}

    dataset = Dataset(name, series, series_outputs)
    log("Dataset length: {}".format(len(dataset)))

    return dataset


def _get_name_from_paths(series_paths: Dict[str, str]) -> str:
    """Construct name for a dataset using the paths to its files.

    Arguments:
        series_paths: A dictionary which maps serie names to the paths
                      of their input files.

    Returns:
        The name for the dataset.
    """
    name = "dataset"
    for _, path in series_paths.items():
        name += "-{}".format(path)
    return name


def _get_series_paths(kwargs: Dict[str, str]) -> Dict[str, str]:
    """Get paths to files that contain data from the dataset keyword
    argument specs.

    Input file for a serie named 'xxx' is specified by parameter 's_xxx'

    Arguments:
        kwargs: A dictionary containing the dataset keyword argument specs.

    Returns:
        A dictionary which maps serie names to the paths of their input files.
    """
    keys = [k for k in list(kwargs.keys()) if SERIES_SOURCE.match(k)]
    names = [SERIES_SOURCE.match(k).group(1) for k in keys]

    return {name : kwargs[key] for name, key in zip(names, keys)}


def _get_series_outputs(kwargs: Dict[str, str]) -> Dict[str, str]:
    """Get paths to series outputs from the dataset keyword argument specs.
    Output file for a series named 'xxx' is specified by parameter 's_xxx_out'

    Arguments:
        kwargs: A dictionary containing the dataset keyword argument specs.

    Returns:
        A dictionary which maps serie names to the paths for their output files.
    """
    return {SERIES_OUTPUT.match(key).group(1): value
            for key, value in kwargs.items() if SERIES_OUTPUT.match(key)}


def create_dataset_series(path: str,
                          preprocess: Callable[[str], str]) -> Iterable:
    """Create dataset series.

    Arguments:
        path: The path of the file with the data
        preprocess: Preprocessor function

    Returns:
        The dataset series.
    """
    log("Loading {}".format(path))
    file_type = magic.from_file(path, mime=True)

    if file_type.startswith('text/'):
        reader = PlainTextFileReader(path)
        for line in reader.read():
            yield preprocess(line)
    elif file_type == 'application/octet-stream':
        return np.load(path)
    else:
        raise Exception("Unsupported data type: {}, file {}"
                        .format(file_type, path))



class Dataset(collections.Sized):
    """ This class serves as collection for data series for particular
    encoders and decoders in the model. If it is not provided a parent
    dataset, it also manages the vocabularies inferred from the data.

    A data series is either a list of strings or a numpy array.
    """

    def __init__(self, name: str, series: Dict[str, List],
                 series_outputs: Dict[str, str]) -> None:
        """Creates a dataset from the provided already preprocessed
        series of data.

        Arguments:
            name: The name for the dataset
            series: Dictionary from the series name to the actual data.
            series_outputs: Output files for target series.
        """
        self.name = name
        self._series = series
        self.series_outputs = series_outputs

        self._check_series_lengths()


    def _check_series_lengths(self) -> None:
        """Check lenghts of series in the dataset.

        Raises:
            Exception when the lengths in the dataset do not match.
        """
        lengths = [len(list(v)) for v in self._series.values()
                   if isinstance(v, list) or isinstance(v, np.ndarray)]

        if len(set(lengths)) > 1:
            err_str = ["{}: {}".format(s, len(list(self._series[s])))
                       for s in self._series]
            raise Exception("Lengths of data series must be equal. Instead: {}"
                            .format(", ".join(err_str)))


    def __len__(self) -> int:
        """Get the length of the dataset.

        Returns:
            The length of the dataset.
        """
        if not list(self._series.values()):
            return 0
        else:
            first_series = next(iter(self._series.values()))
            return len(list(first_series))



    def has_series(self, name: str) -> bool:
        """Check if the dataset contains a series of a given name.

        Arguments:
            name: Series name

        Returns:
            True if the dataset contains the series, False otherwise.
        """
        return name in self._series


    def get_series(self, name: str, allow_none: bool=False) -> Iterable:
        """Get the data series with a given name.

        Arguments:
            name: The name of the series to fetch.
            allow_none: If True, return None if the series does not exist.

        Returns:
            The data series.

        Raises:
            KeyError if the series does not exists and allow_none is False
        """
        if allow_none:
            return self._series.get(name)
        else:
            return self._series[name]


    def shuffle(self) -> None:
        """Shuffle the dataset randomly """
        keys = list(self._series.keys())
        zipped = list(zip(*[self._series[k] for k in keys]))
        random.shuffle(zipped)
        for key, serie in zip(keys, list(zip(*zipped))):
            self._series[key] = serie


    def batch_serie(self, serie_name: str,
                    batch_size: int) -> Iterable[Iterable]:
        """Split a data serie into batches.

        Arguments:
            serie_name: The name of the series
            batch_size: The size of a batch

        Returns:
            Generator yielding batches of the data from the serie.
        """
        buf = []
        for item in self.get_series(serie_name):
            buf.append(item)
            if len(buf) >= batch_size:
                yield buf
                buf = []
        if buf:
            yield buf


    def batch_dataset(self, batch_size: int) -> Iterable['Dataset']:
        """Split the dataset into a list of batched datasets.

        Arguments:
            batch_size: The size of a batch.

        Returns:
            Generator yielding batched datasets.
        """
        keys = list(self._series.keys())
        batched_series = [self.batch_serie(key, batch_size) for key in keys]

        batch_index = 0
        for next_batches in zip(*batched_series):
            batch_dict = {key:data for key, data in zip(keys, next_batches)}
            dataset = Dataset(self.name + "-batch-{}".format(batch_index),
                              batch_dict, {})
            batch_index += 1
            yield dataset



class LazyDataset(Dataset):
    """Implements the lazy dataset.

    The main difference between this implementation and the default one is
    that the contents of the file are not fully loaded to the memory.
    Instead, everytime the function ``get_series`` is called, a new file handle
    is created and a generator which yields lines from the file is returned.
    """
    def __init__(self, name: str, series_paths: Dict[str, str],
                 series_outputs: Dict[str, str],
                 preprocess: Callable[[str], str]=lambda x: x) -> None:
        """Create a new instance of the lazy dataset.

        Arguments:
            name: The name of the dataset
            series_paths: The mapping of series name to its file
            series_outputs: Dictionary mapping series names to their output file
            preprocess: The preprocessor to apply to the read lines
        """
        super().__init__(name, {s: None for s in series_paths}, series_outputs)
        self.series_paths = series_paths
        self.preprocess = preprocess


    def __len__(self):
        """Length of the lazy dataset is unknown.

        TODO: reconsider the exception raising, maybe just write a warning
        log message instead and return None or zero. Make the decision
        consistent with the implementation of the ``shuffle`` function.

        Raises:
            Exception every time this function is called.
        """
        raise Exception("Lazy dataset does not know its size")


    def has_series(self, name: str) -> bool:
        """Check if the dataset contains a series of a given name.

        Arguments:
            name: Series name

        Returns:
            True if the dataset contains the series, False otherwise.
        """
        return name in self.series_paths


    def get_series(self, name: str, allow_none: bool=False) -> Iterable:
        """Get the data series with a given name.

        This function opens a new file handle and returns a generator which
        yields preprocessed lines from the file.

        Arguments:
            name: The name of the series to fetch.
            allow_none: If True, return None if the series does not exist.

        Returns:
            The data series.

        Raises:
            KeyError if the series does not exists and allow_none is False
        """
        if allow_none and name not in self.series_paths:
            return None

        path = self.series_paths[name]
        return create_dataset_series(path, self.preprocess)


    def shuffle(self):
        """Does nothing, not in-memory shuffle is impossible.

        TODO: this is related to the ``__len__`` method.
        """
        pass
