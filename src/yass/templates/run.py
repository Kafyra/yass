import os
import logging
import datetime

from yass import read_config
from yass.templates.util import get_and_merge_templates as gam_templates
from yass.templates.clean import clean_up_templates

def run(spike_train, output_directory='tmp/',
        recordings_filename='standarized.bin'):
    """(TODO add missing documentation)


    Parameters
    ----------
    spike_train: ?

    output_directory: str, optional
        Output directory (relative to CONFIG.data.root_folder) used to load
        the recordings to generate templates, defaults to tmp/

    recordings_filename: str, optional
        Recordings filename (relative to CONFIG.data.root_folder/
        output_directory) used to generate the templates, defaults to
        whitened.bin


    Returns
    -------
    templates ?

    Examples
    --------

    .. literalinclude:: ../../examples/pipeline/templates.py
    """
    CONFIG = read_config()

    startTime = datetime.datetime.now()

    Time = {'t': 0, 'c': 0, 'm': 0, 's': 0, 'e': 0}

    logger = logging.getLogger(__name__)

    _b = datetime.datetime.now()

    logger.info("Getting Templates...")

    path_to_recordings = os.path.join(CONFIG.data.root_folder,
                                      output_directory,
                                      recordings_filename)
    merge_threshold = CONFIG.templates.merge_threshold

    spike_train, templates = gam_templates(
        spike_train, path_to_recordings, CONFIG.resources.max_memory,
        CONFIG.spikeSize, CONFIG.templatesMaxShift,
        merge_threshold, CONFIG.neighChannels)

    snr_threshold = 2
    duplicate_threshold = 0.9
    spread_threshold = 70
    (spike_train,
     templates,
     spread_template) = clean_up_templates(spike_train, templates,
                                           CONFIG.geom, snr_threshold,
                                           duplicate_threshold,
                                           spread_threshold)

    Time['e'] += (datetime.datetime.now() - _b).total_seconds()

    # report timing
    currentTime = datetime.datetime.now()
    logger.info("Templates done in {0} seconds.".format(
        (currentTime - startTime).seconds))

    return templates, spike_train, spread_template
