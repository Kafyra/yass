import logging
import datetime

from yass import read_config
from yass.geometry import make_channel_index
from yass.util import file_loader, check_for_files, LoadFile
from yass.cluster.list import make_list
from yass.cluster.subsample import random_subsample
from yass.cluster.triage import triage
from yass.cluster.coreset import coreset
from yass.cluster.mask import getmask
from yass.cluster.util import run_cluster, run_cluster_location


@check_for_files(filenames=[LoadFile('spike_train_cluster.npy')],
                 mode='values', relative_to='output_directory',
                 auto_save=True, prepend_root_folder=True)
def run(scores, spike_index, output_directory='tmp/',
        if_file_exists='skip', save_results=False):
    """Spike clustering

    Parameters
    ----------
    scores: numpy.ndarray (n_spikes, n_features, n_channels), str or Path
        3D array with the scores for the clear spikes, first simension is
        the number of spikes, second is the nymber of features and third the
        number of channels. Or path to a npy file

    spike_index: numpy.ndarray (n_clear_spikes, 2), str or Path
        2D array with indexes for spikes, first column contains the
        spike location in the recording and the second the main channel
        (channel whose amplitude is maximum). Or path to an npy file

    output_directory: str, optional
        Location to store/look for the generate spike train, relative to
        CONFIG.data.root_folder

    if_file_exists: str, optional
      One of 'overwrite', 'abort', 'skip'. Control de behavior for the
      spike_train_cluster.npy. file If 'overwrite' it replaces the files if
      exists, if 'abort' it raises a ValueError exception if exists,
      if 'skip' it skips the operation if the file exists (and returns the
      stored file)

    save_results: bool, optional
        Whether to save spike train to disk
        (in CONFIG.data.root_folder/relative_to/spike_train_cluster.npy),
        defaults to False

    Returns
    -------
    spike_train: (TODO add documentation)

    Examples
    --------

    .. literalinclude:: ../../examples/pipeline/cluster.py

    """
    # load files in case they are strings or Path objects
    scores = file_loader(scores)
    spike_index = file_loader(spike_index)

    CONFIG = read_config()

    startTime = datetime.datetime.now()

    Time = {'t': 0, 'c': 0, 'm': 0, 's': 0, 'e': 0}

    logger = logging.getLogger(__name__)

    # transform data structure of scores and spike_index
    scores, spike_index = make_list(scores, spike_index,
                                    CONFIG.recordings.n_channels)

    ##########
    # Triage #
    ##########

    _b = datetime.datetime.now()
    logger.info("Triaging...")
    score, spike_index = triage(scores, spike_index,
                                CONFIG.cluster.triage.nearest_neighbors,
                                CONFIG.cluster.triage.percent)

    logger.info("Randomly subsampling...")
    scores, spike_index = random_subsample(scores, spike_index,
                                           CONFIG.cluster.max_n_spikes)
    Time['t'] += (datetime.datetime.now()-_b).total_seconds()

    if CONFIG.cluster.method == 'location':
        ##############
        # Clustering #
        ##############
        _b = datetime.datetime.now()
        logger.info("Clustering...")
        spike_train = run_cluster_location(scores,
                                           spike_index, CONFIG)
        Time['s'] += (datetime.datetime.now()-_b).total_seconds()

    else:
        ###########
        # Coreset #
        ###########
        _b = datetime.datetime.now()
        logger.info("Coresetting...")
        groups = coreset(scores,
                         CONFIG.cluster.coreset.clusters,
                         CONFIG.cluster.coreset.threshold)
        Time['c'] += (datetime.datetime.now() - _b).total_seconds()

        ###########
        # Masking #
        ###########
        _b = datetime.datetime.now()
        logger.info("Masking...")
        masks = getmask(scores, groups,
                        CONFIG.cluster.masking_threshold)
        Time['m'] += (datetime.datetime.now() - _b).total_seconds()

        ##############
        # Clustering #
        ##############
        _b = datetime.datetime.now()
        logger.info("Clustering...")
        channel_index = make_channel_index(CONFIG.neigh_channels,
                                           CONFIG.geom)
        spike_train = run_cluster(scores, masks, groups,
                                  spike_index, CONFIG.channel_groups,
                                  channel_index,
                                  CONFIG.detect.temporal_features,
                                  CONFIG)
        Time['s'] += (datetime.datetime.now()-_b).total_seconds()

    # report timing
    currentTime = datetime.datetime.now()
    logger.info("Mainprocess done in {0} seconds.".format(
        (currentTime - startTime).seconds))
    logger.info("\ttriage:\t{0} seconds".format(Time['t']))
    logger.info("\tcoreset:\t{0} seconds".format(Time['c']))
    logger.info("\tmasking:\t{0} seconds".format(Time['m']))
    logger.info("\tclustering:\t{0} seconds".format(Time['s']))

    return spike_train
