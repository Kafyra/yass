"""
Preprocess pipeline
"""
import logging
import os.path
import os
import numpy as np
import parmap
import yaml

try:
    from pathlib2 import Path
except Exception:
    from pathlib import Path

from yass import read_config
from yass.geometry import make_channel_index
from yass.preprocess.filter import filter_standardize, merge_filtered_files
from yass.util import save_numpy_object
from yass.preprocess import whiten


def run(output_directory='tmp/', if_file_exists='skip'):
    """Preprocess pipeline: filtering, standarization and whitening filter

    This step (optionally) performs filtering on the data, standarizes it
    and computes a whitening filter. Filtering and standarized data are
    processed in chunks and written to disk.

    Parameters
    ----------
    output_directory: str, optional
        Location to store results, relative to CONFIG.data.root_folder,
        defaults to tmp/. See list of files in Notes section below.

    if_file_exists: str, optional
        One of 'overwrite', 'abort', 'skip'. Control de behavior for every
        generated file. If 'overwrite' it replaces the files if any exist,
        if 'abort' it raises a ValueError exception if any file exists,
        if 'skip' it skips the operation (and loads the files) if any of them
        exist

    Returns
    -------
    standarized_path: str
        Path to standarized data binary file

    standarized_params: str
        Path to standarized data parameters

    channel_index: numpy.ndarray
        Channel indexes

    whiten_filter: numpy.ndarray
        Whiten matrix

    Notes
    -----
    Running the preprocessor will generate the followiing files in
    CONFIG.data.root_folder/output_directory/:

    * ``filtered.bin`` - Filtered recordings
    * ``filtered.yaml`` - Filtered recordings metadata
    * ``standarized.bin`` - Standarized recordings
    * ``standarized.yaml`` - Standarized recordings metadata
    * ``whitening.npy`` - Whitening filter

    Everything is run on CPU.

    Examples
    --------

    .. literalinclude:: ../../examples/pipeline/preprocess.py
    """

    print ("********* firsbatch preprocessing")
    logger = logging.getLogger(__name__)

    CONFIG = read_config()
    OUTPUT_DTYPE = CONFIG.preprocess.dtype
    TMP = os.path.join(CONFIG.data.root_folder, output_directory)

    logger.info('Output dtype for transformed data will be {}'
                .format(OUTPUT_DTYPE))

    if not os.path.exists(TMP):
        logger.info('Creating temporary folder: {}'.format(TMP))
        os.makedirs(TMP)
    else:
        logger.info('Temporary folder {} already exists, output will be '
                    'stored there'.format(TMP))

    params = dict(
        dtype=CONFIG.recordings.dtype,
        n_channels=CONFIG.recordings.n_channels,
        data_order=CONFIG.recordings.order)

    # Generate params:
    standardized_path = TMP + "standardized_firstbatch.bin"
    standardized_params = params
    standardized_params['dtype'] = 'float32'

    ## Check if data already saved to disk and skip:
    #if if_file_exists == 'skip':
        #f_out = os.path.join(CONFIG.data.root_folder, output_directory,
                             #"standarized.bin")
        #if os.path.exists(f_out):

            #channel_index = make_channel_index(CONFIG.neigh_channels,
                                               #CONFIG.geom, 2)

            ## Cat: this is redundant, should save to disk/not recompute
            #whiten_filter = whiten.matrix(
                #standarized_path,
                #standarized_params['dtype'],
                #standarized_params['n_channels'],
                #standarized_params['data_order'],
                #channel_index,
                #CONFIG.spike_size,
                #CONFIG.resources.max_memory,
                #TMP,
                #output_filename='whitening.npy',
                #if_file_exists=if_file_exists)

            #path_to_channel_index = os.path.join(TMP, "channel_index.npy")

            #return (str(standarized_path), standarized_params, channel_index,
                    #whiten_filter)

    # read config params
    multi_processing = CONFIG.resources.multi_processing
    n_processors = CONFIG.resources.n_processors
    n_sec_chunk = CONFIG.resources.n_sec_chunk
    n_channels = CONFIG.recordings.n_channels
    sampling_rate = CONFIG.recordings.sampling_rate

    # Read filter params
    low_frequency = CONFIG.preprocess.filter.low_pass_freq
    high_factor = CONFIG.preprocess.filter.high_factor
    order = CONFIG.preprocess.filter.order
    buffer_size = 200

    # compute len of recording
    filename_dat = os.path.join(CONFIG.data.root_folder,
                                CONFIG.data.recordings)
    fp = np.memmap(filename_dat, dtype='int16', mode='r')
    fp_len = fp.shape[0]

    # compute batch indexes
    indexes = np.arange(0, fp_len / n_channels, sampling_rate * n_sec_chunk)
    if indexes[-1] != fp_len / n_channels:
        indexes = np.hstack((indexes, fp_len / n_channels))

    idx_list = []
    for k in range(len(indexes) - 1):
        idx_list.append([
            indexes[k], indexes[k + 1], buffer_size,
            indexes[k + 1] - indexes[k] + buffer_size
        ])

    idx_list = np.int64(np.vstack(idx_list))
    proc_indexes = np.arange(len(idx_list))

    print(" Processing first chunk of recording ")

    # Make directory to hold filtered batch files:
    filtered_location = os.path.join(CONFIG.data.root_folder, output_directory,
                                     "filtered_files")
    print(filtered_location)
    if not os.path.exists(filtered_location):
        os.makedirs(filtered_location)

    # filter and standardize in one step, return standardized chunk
    # Cat: TODO: re-parallelize this
    recording_standardized = filter_standardize([idx_list[0], 0], 
                                low_frequency, high_factor,
                                order, sampling_rate, buffer_size, 
                                filename_dat, n_channels, 
                                CONFIG.data.root_folder, output_directory)

    print (recording_standardized.shape)

    # save yaml file with params
    path_to_yaml = standardized_path.replace('.bin', '.yaml')

    params = dict(
        dtype=standardized_params['dtype'],
        n_channels=standardized_params['n_channels'],
        data_order=standardized_params['data_order'])

    with open(path_to_yaml, 'w') as f:
        logger.info('Saving params...')
        yaml.dump(params, f)

    # TODO: this shoulnd't be done here, it would be better to compute
    # this when initializing the config object and then access it from there
    channel_index = make_channel_index(CONFIG.neigh_channels, CONFIG.geom, 2)

    # Cat: TODO: need to make this much smaller in size, don't need such
    # large batches
    # OLD CODE: compute whiten filter using batch processor
    # TODO: remove whiten_filter out of output argument

    whiten_filter = whiten._matrix(recording_standardized, 
                                   channel_index, CONFIG.spike_size)

    # save whiten matrix
    path_to_whitening_matrix = Path(TMP, 'whitening.npy')
    save_numpy_object(whiten_filter, path_to_whitening_matrix,
                      if_file_exists='overwrite',
                      name='whitening filter')
    
    # save channel index
    path_to_channel_index = os.path.join(TMP, 'channel_index.npy')
    save_numpy_object(
        channel_index,
        path_to_channel_index,
        if_file_exists=if_file_exists,
        name='Channel index')

    np.save(standardized_path.replace('.bin','.npy'), recording_standardized)
    np.save(standardized_path.replace('.bin','_params.npy'), standardized_params)

    return (recording_standardized, str(standardized_path), 
            standardized_params, channel_index, whiten_filter)
