import numpy as np
import logging
import os
import parmap
from scipy import signal
from scipy.spatial import cKDTree

from yass import mfm
from scipy.sparse import lil_matrix
from statsmodels import robust


def run_cluster(scores, masks, groups, spike_index,
                min_spikes, CONFIG):
    """
    run clustering algorithm using MFM

    Parameters
    ----------
    scores: list (n_channels)
        A list such that scores[c] contains all scores whose main
        channel is c

    masks: list (n_channels)
        mask for each data in scores
        masks[c] is the mask of spikes in scores[c]

    groups: list (n_channels)
        coreset represented as group id.
        groups[c] is the group id of spikes in scores[c]

    spike_index: list (n_channels)
        A list such that spike_index[c] cointains all spike times
        whose channel is c

    CONFIG: class
       configuration class

    Returns
    -------
    spike_train: np.array (n_data, 2)
        spike_train such that spike_train[j, 0] and spike_train[j, 1]
        are the spike time and spike id of spike j
    """

    # FIXME: mutating parameter
    # this function is passing a config object and mutating it,
    # this is not a good idea as having a mutable object lying around the code
    # can break things and make it hard to debug
    # (09/27/17) Eduardo

    n_channels = np.max(spike_index[:, 1]) + 1
    global_score = None
    global_vbParam = None
    global_spike_index = None
    global_tmp_loc = None

    # run clustering algorithm per main channel
    for channel in range(n_channels):

        idx_data = np.where(spike_index[:, 1] == channel)[0]
        score_channel = scores[idx_data]
        mask_channel = masks[channel]
        group_channel = groups[channel]
        spike_index_channel = spike_index[idx_data]
        n_data = score_channel.shape[0]

        if n_data > 1:
            # run clustering
            vbParam = mfm.spikesort(np.copy(score_channel),
                                    mask_channel,
                                    group_channel, CONFIG)

            # make rhat more sparse
            vbParam.rhat[vbParam.rhat < 0.1] = 0
            vbParam.rhat = vbParam.rhat/np.sum(vbParam.rhat,
                                               1, keepdims=True)

            # clean clusters with nearly no spikes
            vbParam = clean_empty_cluster(vbParam, min_spikes)

            # add changes to global parameters
            (global_vbParam,
             global_tmp_loc,
             global_score,
             global_spike_index) = global_cluster_info(
                vbParam, channel, score_channel, spike_index_channel,
                global_vbParam, global_tmp_loc,
                global_score, global_spike_index)

    return global_vbParam, global_tmp_loc, global_score, global_spike_index


def run_cluster_location(scores, spike_index, min_spikes, CONFIG):
    """
    run clustering algorithm using MFM and location features

    Parameters
    ----------
    scores: list (n_channels)
        A list such that scores[c] contains all scores whose main
        channel is c

    spike_times: list (n_channels)
        A list such that spike_index[c] cointains all spike times
        whose channel is c

    CONFIG: class
        configuration class

    Returns
    -------
    spike_train: np.array (n_data, 2)
        spike_train such that spike_train[j, 0] and spike_train[j, 1]
        are the spike time and spike id of spike j
    """
    logger = logging.getLogger(__name__)

    n_channels = np.max(spike_index[:, 1]) + 1
    global_score = None
    global_vbParam = None
    global_spike_index = None
    global_tmp_loc = None

    # run clustering algorithm per main channel
    for channel in range(n_channels):

        logger.info('Processing channel {}'.format(channel))

        idx_data = np.where(spike_index[:, 1] == channel)[0]
        score_channel = scores[idx_data]
        spike_index_channel = spike_index[idx_data]
        n_data = score_channel.shape[0]

        if n_data > 1:

            # make a fake mask of ones to run clustering algorithm
            mask = np.ones((n_data, 1))
            group = np.arange(n_data)
            vbParam = mfm.spikesort(np.copy(score_channel),
                                    mask,
                                    group, CONFIG)

            # make rhat more sparse
            vbParam.rhat[vbParam.rhat < 0.1] = 0
            vbParam.rhat = vbParam.rhat/np.sum(vbParam.rhat,
                                               1, keepdims=True)

            # clean clusters with nearly no spikes
            vbParam = clean_empty_cluster(vbParam, min_spikes)
            if vbParam.rhat.shape[1] > 0:
                # add changes to global parameters
                (global_vbParam,
                 global_tmp_loc,
                 global_score,
                 global_spike_index) = global_cluster_info(
                    vbParam, channel, score_channel, spike_index_channel,
                    global_vbParam, global_tmp_loc,
                    global_score, global_spike_index)

    return global_vbParam, global_tmp_loc, global_score, global_spike_index


def calculate_sparse_rhat(vbParam, tmp_loc, scores,
                          spike_index, neighbors):

    # vbParam.rhat calculation
    n_channels = np.max(spike_index[:, 1]) + 1
    n_templates = tmp_loc.shape[0]

    rhat = lil_matrix((scores.shape[0], n_templates))
    rhat = None
    for channel in range(n_channels):

        idx_data = np.where(spike_index[:, 1] == channel)[0]
        score = scores[idx_data]
        n_data = score.shape[0]

        ch_idx = np.where(neighbors[channel])[0]
        cluster_idx = np.zeros(n_templates, 'bool')
        for c in ch_idx:
            cluster_idx[tmp_loc == c] = 1
        cluster_idx = np.where(cluster_idx)[0]

        if n_data > 0 and cluster_idx.shape[0] > 0:

            local_vbParam = mfm.vbPar(None)
            local_vbParam.muhat = vbParam.muhat[:, cluster_idx]
            local_vbParam.Vhat = vbParam.Vhat[:, :, cluster_idx]
            local_vbParam.invVhat = vbParam.invVhat[:, :, cluster_idx]
            local_vbParam.nuhat = vbParam.nuhat[cluster_idx]
            local_vbParam.lambdahat = vbParam.lambdahat[cluster_idx]
            local_vbParam.ahat = vbParam.ahat[cluster_idx]

            mask = np.ones([n_data, 1])
            group = np.arange(n_data)
            masked_data = mfm.maskData(score, mask, group)

            local_vbParam.update_local(masked_data)
            local_vbParam.rhat[local_vbParam.rhat < 0.1] = 0
            local_vbParam.rhat = local_vbParam.rhat / \
                np.sum(local_vbParam.rhat, axis=1, keepdims=True)

            row_idx, col_idx = np.where(local_vbParam.rhat > 0)
            val = local_vbParam.rhat[row_idx, col_idx]
            row_idx = idx_data[row_idx]
            col_idx = cluster_idx[col_idx]
            rhat_local = np.hstack((row_idx[:, np.newaxis],
                                    col_idx[:, np.newaxis],
                                    val[:, np.newaxis]))
            if rhat is None:
                rhat = rhat_local
            else:
                rhat = np.vstack((rhat, rhat_local))

    return rhat


def calculate_maha_clusters(vbParam):
    diff = np.transpose(vbParam.muhat, [1, 2, 0]) - \
        vbParam.muhat[..., 0].T
    clustered_prec = np.transpose(vbParam.Vhat[:, :, :, 0] *
                                  vbParam.nuhat, [2, 0, 1])
    maha = np.squeeze(np.matmul(diff[:, :, np.newaxis],
                                np.matmul(clustered_prec[:, np.newaxis],
                                          diff[..., np.newaxis])),
                      axis=[2, 3])
    maha[np.diag_indices(maha.shape[0])] = np.inf

    return maha


def merge_move_patches(cluster, neigh_clusters, scores, vbParam, maha, cfg):

    while len(neigh_clusters) > 0:
        i = neigh_clusters[-1]
        # indices = np.logical_or(clusterid == cluster, clusterid == i)
        indices, temp = vbParam.rhat[:, [cluster, i]].nonzero()
        indices = np.unique(indices)
        ka, kb = min(cluster, i), max(cluster, i)
        local_scores = scores[indices]
        local_vbParam = mfm.vbPar(
            vbParam.rhat[:, [cluster, i]].toarray()[indices])
        local_vbParam.muhat = vbParam.muhat[:, [cluster, i]]
        local_vbParam.Vhat = vbParam.Vhat[:, :, [cluster, i]]
        local_vbParam.invVhat = vbParam.invVhat[:, :, [cluster, i]]
        local_vbParam.nuhat = vbParam.nuhat[[cluster, i]]
        local_vbParam.lambdahat = vbParam.lambdahat[[cluster, i]]
        local_vbParam.ahat = vbParam.ahat[[cluster, i]]
        mask = np.ones([local_scores.shape[0], 1])
        group = np.arange(local_scores.shape[0])
        local_maskedData = mfm.maskData(local_scores, mask, group)
        # local_vbParam.update_local(local_maskedData)
        local_suffStat = mfm.suffStatistics(local_maskedData, local_vbParam)

        ELBO = mfm.ELBO_Class(local_maskedData, local_suffStat,
                              local_vbParam, cfg)
        L = np.ones(2)
        (local_vbParam, local_suffStat,
         merged, _, _) = mfm.check_merge(local_maskedData,
                                         local_vbParam,
                                         local_suffStat, 0, 1,
                                         cfg, L, ELBO)
        if merged:
            print("merging {}, {}".format(cluster, i))
            vbParam.muhat = np.delete(vbParam.muhat, kb, 1)
            vbParam.muhat[:, ka] = local_vbParam.muhat[:, 0]

            vbParam.Vhat = np.delete(vbParam.Vhat, kb, 2)
            vbParam.Vhat[:, :, ka] = local_vbParam.Vhat[:, :, 0]

            vbParam.invVhat = np.delete(vbParam.invVhat, kb, 2)
            vbParam.invVhat[:, :, ka] = local_vbParam.invVhat[:, :, 0]

            vbParam.nuhat = np.delete(vbParam.nuhat, kb, 0)
            vbParam.nuhat[ka] = local_vbParam.nuhat[0]

            vbParam.lambdahat = np.delete(vbParam.lambdahat, kb, 0)
            vbParam.lambdahat[ka] = local_vbParam.lambdahat[0]

            vbParam.ahat = np.delete(vbParam.ahat, kb, 0)
            vbParam.ahat[ka] = local_vbParam.ahat[0]

            vbParam.rhat[:, ka] = vbParam.rhat[:, ka] + vbParam.rhat[:, kb]
            n_data_all, n_templates_all = vbParam.rhat.shape
            to_keep = list(set(np.arange(n_templates_all))-set([kb]))
            vbParam.rhat = vbParam.rhat[:, to_keep]

            # clusterid[indices] = ka
            # clusterid[clusterid > kb] = clusterid[clusterid > kb] - 1
            neigh_clusters.pop()

            maha = np.delete(maha, kb, 1)
            maha = np.delete(maha, kb, 0)

            diff = vbParam.muhat[:, :, 0] - local_vbParam.muhat[:, :, 0]

            prec = local_vbParam.Vhat[..., 0] * local_vbParam.nuhat[0]
            maha[ka] = np.squeeze(
                np.matmul(
                    diff.T[:, np.newaxis, :],
                    np.matmul(prec[:, :, 0],
                              diff.T[..., np.newaxis])))

            prec = np.transpose(vbParam.Vhat[..., 0] * vbParam.nuhat,
                                [2, 0, 1])
            maha[:, ka] = np.squeeze(np.matmul(
                diff.T[:, np.newaxis, :],
                np.matmul(prec, diff.T[..., np.newaxis])))

            maha[ka, ka] = np.inf
            neigh_clusters = list(np.where(
                np.logical_or(maha[ka] < 15, maha.T[ka] < 15))[0])
            cluster = ka

        if not merged:
            maha[ka, kb] = maha[kb, ka] = np.inf
            neigh_clusters.pop()

    return vbParam, maha


def try_merge(k1, k2, scores, vbParam, maha, cfg):

    ka, kb = min(k1, k2), max(k1, k2)

    assignment = vbParam.rhat[:, :2].astype('int32')

    idx_ka = assignment[:, 1] == ka
    idx_kb = assignment[:, 1] == kb

    indices = np.unique(assignment[
        np.logical_or(idx_ka, idx_kb), 0])

    rhat = np.zeros((scores.shape[0], 2))
    rhat[assignment[idx_ka, 0], 0] = vbParam.rhat[idx_ka, 2]
    rhat[assignment[idx_kb, 0], 1] = vbParam.rhat[idx_kb, 2]
    rhat = rhat[indices]

    local_scores = scores[indices]
    local_vbParam = mfm.vbPar(rhat)
    local_vbParam.muhat = vbParam.muhat[:, [ka, kb]]
    local_vbParam.Vhat = vbParam.Vhat[:, :, [ka, kb]]
    local_vbParam.invVhat = vbParam.invVhat[:, :, [ka, kb]]
    local_vbParam.nuhat = vbParam.nuhat[[ka, kb]]
    local_vbParam.lambdahat = vbParam.lambdahat[[ka, kb]]
    local_vbParam.ahat = vbParam.ahat[[ka, kb]]

    mask = np.ones([local_scores.shape[0], 1])
    group = np.arange(local_scores.shape[0])
    local_maskedData = mfm.maskData(local_scores, mask, group)
    # local_vbParam.update_local(local_maskedData)
    local_suffStat = mfm.suffStatistics(local_maskedData, local_vbParam)

    ELBO = mfm.ELBO_Class(local_maskedData, local_suffStat, local_vbParam, cfg)
    L = np.ones(2)
    (local_vbParam, local_suffStat,
     merged, _, _) = mfm.check_merge(local_maskedData,
                                     local_vbParam,
                                     local_suffStat, 0, 1,
                                     cfg, L, ELBO)
    if merged:
        print("merging {}, {}".format(ka, kb))

        vbParam.muhat = np.delete(vbParam.muhat, kb, 1)
        vbParam.muhat[:, ka] = local_vbParam.muhat[:, 0]

        vbParam.Vhat = np.delete(vbParam.Vhat, kb, 2)
        vbParam.Vhat[:, :, ka] = local_vbParam.Vhat[:, :, 0]

        vbParam.invVhat = np.delete(vbParam.invVhat, kb, 2)
        vbParam.invVhat[:, :, ka] = local_vbParam.invVhat[:, :, 0]

        vbParam.nuhat = np.delete(vbParam.nuhat, kb, 0)
        vbParam.nuhat[ka] = local_vbParam.nuhat[0]

        vbParam.lambdahat = np.delete(vbParam.lambdahat, kb, 0)
        vbParam.lambdahat[ka] = local_vbParam.lambdahat[0]

        vbParam.ahat = np.delete(vbParam.ahat, kb, 0)
        vbParam.ahat[ka] = local_vbParam.ahat[0]

        idx_delete = np.where(np.logical_or(idx_ka, idx_kb))[0]
        vbParam.rhat = np.delete(vbParam.rhat, idx_delete, 0)
        vbParam.rhat[vbParam.rhat[:, 1] > kb, 1] -= 1

        rhat_temp = np.hstack((indices[:, np.newaxis],
                               np.ones((indices.size, 1))*ka,
                               np.sum(rhat, 1, keepdims=True)))
        vbParam.rhat = np.vstack((vbParam.rhat, rhat_temp))

        maha = np.delete(maha, kb, 1)
        maha = np.delete(maha, kb, 0)

        diff = vbParam.muhat[:, :, 0] - local_vbParam.muhat[:, :, 0]

        prec = local_vbParam.Vhat[..., 0] * local_vbParam.nuhat[0]
        maha[ka] = np.squeeze(
            np.matmul(
                diff.T[:, np.newaxis, :],
                np.matmul(prec[:, :, 0],
                          diff.T[..., np.newaxis])))

        prec = np.transpose(vbParam.Vhat[..., 0] * vbParam.nuhat,
                            [2, 0, 1])
        maha[:, ka] = np.squeeze(
            np.matmul(diff.T[:, np.newaxis, :],
                      np.matmul(prec, diff.T[..., np.newaxis])))

        maha[ka, ka] = np.inf

    if not merged:
        maha[ka, kb] = maha[kb, ka] = np.inf

    return vbParam, maha


def global_cluster_info(vbParam, main_channel,
                        score, spike_index,
                        global_vbParam, global_tmp_loc,
                        global_score, global_spike_index):
    """
    Gather clustering information from each run
    Parameters
    ----------
    vbParam, maskedData: class
        cluster information output from MFM
    score: np.array (n_data, n_features, 1)
        score used for each clustering
    spike_time: np.array (n_data, 1)
        spike time that matches with each score
    global_vbParam, global_maskedData: class
        a class that contains cluster information from all
        previous run,
    global_score: np.array (n_data_all, n_features, 1)
        all scores from previous runs
    global_spike_times: np.array (n_data_all, 1)
        spike times matched to global_score
    global_cluster_id: np.array (n_data_all, 1)
        cluster id matched to global_score
    Returns
    -------
    global_vbParam, global_maskedData: class
        a class that contains cluster information after
        adding the current one
    global_score: np.array (n_data_all, n_features, 1)
        all scores after adding the current one
    global_spike_times: np.array (n_data_all, 1)
        spike times matched to global_score
    global_cluster_id: np.array (n_data_all, 1)
        cluster id matched to global_score
    """

    n_idx, k_idx = np.where(vbParam.rhat > 0)
    prob_val = vbParam.rhat[n_idx, k_idx]
    vbParam.rhat = np.hstack((n_idx[:, np.newaxis],
                              k_idx[:, np.newaxis],
                              prob_val[:, np.newaxis]))

    if global_vbParam is None:
        global_vbParam = vbParam
        global_tmp_loc = np.ones(
            vbParam.muhat.shape[1], 'int16')*main_channel
        global_score = score
        global_spike_index = spike_index

    else:

        # append global_vbParam
        global_vbParam.muhat = np.concatenate(
            [global_vbParam.muhat, vbParam.muhat], axis=1)
        global_vbParam.Vhat = np.concatenate(
            [global_vbParam.Vhat, vbParam.Vhat], axis=2)
        global_vbParam.invVhat = np.concatenate(
            [global_vbParam.invVhat, vbParam.invVhat],
            axis=2)
        global_vbParam.lambdahat = np.concatenate(
            [global_vbParam.lambdahat, vbParam.lambdahat],
            axis=0)
        global_vbParam.nuhat = np.concatenate(
            [global_vbParam.nuhat, vbParam.nuhat],
            axis=0)
        global_vbParam.ahat = np.concatenate(
            [global_vbParam.ahat, vbParam.ahat],
            axis=0)

        n_max, k_max = np.max(global_vbParam.rhat[:, :2], axis=0)
        vbParam.rhat[:, 0] += n_max + 1
        vbParam.rhat[:, 1] += k_max + 1
        global_vbParam.rhat = np.concatenate(
            [global_vbParam.rhat, vbParam.rhat],
            axis=0)

        global_tmp_loc = np.hstack((global_tmp_loc,
                                    np.ones(vbParam.muhat.shape[1],
                                            'int16')*main_channel))

        # append score
        global_score = np.concatenate([global_score,
                                       score], axis=0)

        # append spike_index
        global_spike_index = np.concatenate([global_spike_index,
                                             spike_index], axis=0)

    return (global_vbParam, global_tmp_loc,
            global_score, global_spike_index)


def clean_empty_cluster(vbParam, min_spikes=20):

    n_hat = np.sum(vbParam.rhat, 0)
    Ks = n_hat > min_spikes

    vbParam.muhat = vbParam.muhat[:, Ks]
    vbParam.Vhat = vbParam.Vhat[:, :, Ks]
    vbParam.invVhat = vbParam.invVhat[:, :, Ks]
    vbParam.lambdahat = vbParam.lambdahat[Ks]
    vbParam.nuhat = vbParam.nuhat[Ks]
    vbParam.ahat = vbParam.ahat[Ks]
    vbParam.rhat = vbParam.rhat[:, Ks]

    return vbParam



# wf is an array of waveforms: chans x time_steps x n_spikes; e.g. 49,31,7263
def upsample_resample(wf, upsample_factor):
    waveform_len, n_spikes = wf.shape
    traces = np.zeros((n_spikes, waveform_len*upsample_factor),'float32')
    for j in range(n_spikes):
        traces[j] = signal.resample(wf[:,j],waveform_len*upsample_factor)
       
    return traces


def usample_resample2(wf, upsample_factor):
    n_spikes = wf.shape[1]
    traces=[]
    for j in range(n_spikes):
        traces.append(signal.resample(wf[:,j],91))
        
    return wfs_upsampled
    
    
def upsample_template(wf,upsample_factor,n_steps):

    # reduce waveform to 5 time_steps
    wf = wf[wf.shape[0]//2-n_steps: wf.shape[0]//2+n_steps+1]
    # get shapes
    waveform_size, n_spikes = wf.shape

    # upsample using cubic interpolation
    x = np.linspace(0, waveform_size - 1, num=waveform_size, endpoint=True)
    shifts = np.linspace(0, 1, upsample_factor, endpoint=False)
    xnew = np.sort(np.reshape(x[:, np.newaxis] + shifts, -1))
    wfs_upsampled = np.zeros((waveform_size * upsample_factor, n_spikes))
    
    # compute template and interpolate it
    template = np.mean(wf,axis=1)
    ff = interp1d(x, template, kind='cubic')
    idx_good = np.logical_and(xnew >= 0, xnew <= waveform_size - 1)
    template_upsampled = ff(xnew[idx_good])
        
    return template_upsampled


def shift_template(template_upsampled, n_shifts, window):

    temp_array = []
    for s in range(-n_shifts//2, n_shifts//2+1, 1):
        temp_array.append(template_upsampled[template_upsampled.shape[0]//2-window+s:
                                             template_upsampled.shape[0]//2+window+s])

    return np.array(temp_array)


def return_shifts(wfs_upsampled,template_shifted, window):
    
    shift_array = []
    out_array = []
    waveform_len = wfs_upsampled.shape[0]

    for k in range(wfs_upsampled.shape[1]):
        temp = np.matmul(wfs_upsampled[waveform_len//2-window:waveform_len//2+window,k],
                        template_shifted.T)
        
        shift_array.append(np.argmax(temp))
    
    return np.array(shift_array) #, out_array


def align_channelwise(wf, upsample_factor=20, n_steps=15):
    
    # upsample template and max channel data
    waveform_len = wf.shape[0]

    n_shifts=7*upsample_factor
    window=7*upsample_factor

    # upsample mad chans
    wf_upsampled = upsample_resample(wf, upsample_factor)
    
    template_upsampled = upsample_resample(np.mean(wf,axis=1)[:,np.newaxis], upsample_factor).reshape(upsample_factor*waveform_len)
    template_shifted = shift_template(template_upsampled, n_shifts, window)

    shift_array = return_shifts(wf_upsampled.T, template_shifted, window)

    aligned_chunks = np.zeros((len(shift_array), waveform_len))
    for ctr, shift in enumerate(shift_array):
        chunk = wf_upsampled[ctr,n_shifts-shift:][::upsample_factor][:waveform_len]

        # conditional required in case shift leads to short waveforms
        if len(chunk) < waveform_len: 
            chunk = np.concatenate((chunk, np.zeros(waveform_len-len(chunk))))
        aligned_chunks[ctr] = chunk

    return aligned_chunks


# PCA function return PCA and reconstructed data
def PCA(X, n_components):
    from sklearn import decomposition

    pca = decomposition.PCA(n_components)
    pca.fit(X)
    X = pca.transform(X)
    Y = pca.inverse_transform(X)
    return X,Y



def run_cluster_features(spike_index_clear, n_dim_pca, wf_start, wf_end, 
                         n_mad_chans, n_max_chans, CONFIG, out_dir):
    
    ''' New voltage feature based clustering
    ''' 
    
    # loop over channels 
    # Cat: TODO: Parallelize over channels
    cluster_ctr=0
    spike_list = []
    tmp_loc = []
    channels = np.arange(49)
    for channel in channels: 
        
        # **** grab spike waveforms ****
        indexes = np.where(spike_index_clear[:,1]==channel)[0]
        wf_data = load_waveforms_parallel(spike_index_clear[indexes], 
                                          CONFIG, out_dir)
        wf_data = np.swapaxes(wf_data,2,0)

        # **** find feature channels ****
        # find max amplitude chans 
        template = np.mean(wf_data,axis=2)
        rank_amp_chans = np.max(np.abs(template),axis=1)
        rank_indexes = np.argsort(rank_amp_chans,axis=0)[::-1] 
        max_chans = rank_indexes[:n_max_chans]      # select top chans
              
        # find top 3 mad chans out of chans with template > 2SU
        ptps = np.ptp(template,axis=1)
        chan_indexes_2SU = np.where(ptps>2)[0]
        rank_chans_max = np.max(robust.mad(wf_data[chan_indexes_2SU,:,:],axis=2),axis=1)

        # rank channels by max mad value
        rank_indexes = np.argsort(rank_chans_max,axis=0)[::-1]
        mad_chans = chan_indexes_2SU[rank_indexes][:n_mad_chans]      # select top chans

        # make feature chans from union 
        feat_chans = np.union1d(max_chans, mad_chans)


        # **** cluster ****
        wf_data = wf_data.T
        data_in = wf_data[:,:,feat_chans]
                
        data_aligned = []
        for k in range(data_in.shape[2]):
            #print ("aligning ch: ",k)
            data_aligned.append(align_channelwise(data_in[:,:,k].T, upsample_factor=20, n_steps=15))

        data_in = np.array(data_aligned)
        #print ("aligned data: ", data_in.shape)
        data_in = data_in[:,:,wf_start:wf_end]
        
        # reshape data for PCA
        data_in = data_in.swapaxes(0,1).reshape(data_in.shape[1],-1)
        #print ("reshaped aligned data_in: ", data_in.shape)

        # norm = np.max(pca_wf,axis=1,keepdims=True)
        pca_wf,pca_wf_reconstruct = PCA(data_in,n_dim_pca)
        #print pca_wf.shape
        
        # triage percentile
        th = 90
        # get distance to nearest neighbors
        tree = cKDTree(pca_wf)
        dist, ind = tree.query(pca_wf, k=11)
        dist = np.sum(dist, 1)
       
        # triage far ones
        idx_keep1 = dist < np.percentile(dist, th)
        pca_wf = pca_wf[idx_keep1]
        wf_data_original = wf_data[idx_keep1].copy()

        # save indexes for mapping back
        indexes=indexes[idx_keep1]

        # run pca second time
        pca_wf_original,pca_wf_reconstruct = PCA(data_in[idx_keep1],n_dim_pca)

        # run mfm iteratively 
        spike_train_clustered = run_mfm(wf_data_original, pca_wf_original, 
                                        feat_chans, idx_keep1, wf_start,
                                        wf_end, n_dim_pca, CONFIG)
        
        print ("chan: ", channel, "  feat chans: ", feat_chans, data_in.shape, 
                                ' # clusters: ', len(spike_train_clustered))

        # make 2 column list 
        for c in range(len(spike_train_clustered)):
            temp = np.zeros((spike_train_clustered[c].shape[0],2),'int32')
            temp[:,0]=spike_index_clear[:,0][indexes[spike_train_clustered[c]]]
            temp[:,1]=cluster_ctr
            spike_list.append(temp)
            cluster_ctr+=1
            tmp_loc.append(channel)
            
    # format output in time order
    print ("..formating spike trains ...")
    s = np.vstack(spike_list)
    indexes = np.argsort(s[:,0])
    spike_train_clustered = s[indexes]
    
    return spike_train_clustered, tmp_loc
        
def run_mfm(wf_data_original, pca_wf_original, feat_chans, idx_keep1, 
            wf_start, wf_end, n_dim_pca, CONFIG):
    
    assignment_array = []

    # ************ reset data *************
    kk = pca_wf_original
    wf_data = wf_data_original
    index_vals = np.arange(len(np.where(idx_keep1)[0]))

    # ************ inital cluster ************
    mask = np.ones((kk.shape[0], 1))
    group = np.arange(kk.shape[0])
    vbParam2 = mfm.spikesort(kk[:,:,np.newaxis],
                            mask,
                            group, CONFIG)
    vbParam2.rhat[vbParam2.rhat < 0.1] = 0
    vbParam2.rhat = vbParam2.rhat/np.sum(vbParam2.rhat,
                                         1, keepdims=True)

    assignment2 = np.argmax(vbParam2.rhat, axis=1)
    assignment_array.append(vbParam2.rhat)

    rolling_index_array=[]
    for s in range(1000):
        #print " **iteration ", s, "**"
        # remove most stable cluster
        stability = []
        indexes = []
        remove_indexes =[]
        title_string = []
        cluster_removed = []
        for k in range(vbParam2.rhat.shape[1]):
            index = np.where(vbParam2.rhat[:,k]>0)[0]
            indexes.append(index)
            stability.append(np.mean(vbParam2.rhat[index,k]))
        
            #most_stable = np.argmax(stability)
            if np.mean(vbParam2.rhat[index,k]) > 0.90: 
                remove_indexes.extend(index)
                rolling_index_array.append(index_vals[index])

                template = np.mean(wf_data_original.T[:,:,index_vals[index]],axis=2)
                ptps = np.max(np.abs(template),axis=1)
                #print ("cluster: ",k,  " stability: ", np.mean(vbParam2.rhat[index,k]), 
                #        "  # spikes: ", len(index_vals[index]), " ptp: ", np.max(ptps), " ch: ", np.argmax(ptps), " remove***")
                #ptp_temp_array.append([np.max(ptps), len(index_vals[index]), np.mean(vbParam2.rhat[index,k])])

                #title_string.append(clrs[k]+": "+str(np.round(np.max(ptps),1))+' '+str(len(index))+' '+str(np.round(np.mean(vbParam2.rhat[index,k]),2)))
                cluster_removed.append(k)
            #else:
            #    print "cluster ", k, ", # spikes: ", len(index), ", stability: ", np.mean(vbParam2.rhat[index,k]) 
                            
        if len(remove_indexes)>0: 
            index_vals = np.delete(index_vals, remove_indexes,axis=0)
            wf_data = np.delete(wf_data, remove_indexes,axis=0)

        # remove most stable cluster
        else:
            most_stable = np.argmax(stability)  #Find most stable index using above stability list
            index = indexes[most_stable]        # find indexes of most stable index using above list
            remove_indexes.append(index)
            #print index_vals
            #print index
            rolling_index_array.append(index_vals[index])

            template = np.mean(wf_data_original.T[:,:,index_vals[index]],axis=2)
            ptps = np.max(np.abs(template),axis=1)
            #print ("cluster: ",most_stable,  " stability: ", np.mean(vbParam2.rhat[index,most_stable]), 
            #        "  # spikes: ", len(index_vals[index]), " ptp: ", np.max(ptps), " ch: ", np.argmax(ptps), " remove***")
            #ptp_temp_array.append([np.max(ptps), len(index_vals[index]), np.mean(vbParam2.rhat[index,most_stable])])

            #title_string.append(clrs[most_stable]+': '+str(np.round(np.max(ptps),1))+' '+str(len(index))+' '+str(np.round(np.mean(vbParam2.rhat[index,most_stable]),2)))

            index_vals = np.delete(index_vals, index,axis=0)
            wf_data = np.delete(wf_data, index,axis=0)            

        # realign data on feat chans
        data_in = wf_data[:,:,feat_chans]
        
        #print ("aligned data: ", data_in.shape)
        data_in = data_in[:,:,wf_start:wf_end]

        # clip data  
        if data_in.shape[0]<=35:
            #print "< 35 spikes left ...discarding and exiting mfm"
            break
        
        # realign data after every iteration
        data_aligned = []
        for k in range(data_in.shape[2]):
            data_aligned.append(align_channelwise(data_in[:,:,k].T, upsample_factor=20, n_steps=15))

        data_in = np.array(data_aligned)
        data_in = data_in[:,:,wf_start:wf_end]
        data_in = data_in.swapaxes(0,1).reshape(data_in.shape[1],-1)

        pca_wf,pca_wf_reconstruct = PCA(data_in,n_dim_pca)
        #print pca_wf.shape

        kk = pca_wf
        mask = np.ones((kk.shape[0], 1))
        group = np.arange(kk.shape[0])

        
        vbParam2 = mfm.spikesort(kk[:,:,np.newaxis],
                                mask,
                                group, CONFIG)
        vbParam2.rhat[vbParam2.rhat < 0.1] = 0
        vbParam2.rhat = vbParam2.rhat/np.sum(vbParam2.rhat,
                                             1, keepdims=True)

        assignment2 = np.argmax(vbParam2.rhat, axis=1)
        assignment_array.append(vbParam2.rhat)

        # if only one cluster left
        if vbParam2.rhat.shape[1]==1:
            #Grab last cluster
            index = np.where(vbParam2.rhat[:,0]>0)[0]
            indexes.append(index)
            
            rolling_index_array.append(index_vals) # add the remaning absolute index_vals

            # add last templates
            template = np.mean(wf_data_original.T[:,:,index_vals],axis=2)
            ptps = np.max(np.abs(template),axis=1)
            #print "last cluster # spikes: ", len(index), " ptp: ", np.max(ptps), " ch: ", np.argmax(ptps)
            #ptp_temp_array.append([np.max(ptps), len(index_vals), np.mean(vbParam2.rhat[index,0])])

            break
    
    
    return rolling_index_array
            
def load_waveforms_parallel(spike_train, CONFIG, out_dir): 
    
    # Cat: TODO: link spike_size in CONFIG param
    spike_size = 15
    n_processors = CONFIG.resources.n_processors
    n_channels = CONFIG.recordings.n_channels
    sampling_rate = CONFIG.recordings.sampling_rate

    # select length of recording to read at once and grab data
    # currently fixed to 60 sec, but may wish to change
    # n_sec_chunk = CONFIG.resources.n_sec_chunk
    n_sec_chunk = 60

    # determine length of processing chunk based on lenght of rec
    standardized_filename = os.path.join(CONFIG.data.root_folder, out_dir,
                                         'standarized.bin')
    fp = np.memmap(standardized_filename, dtype='float32', mode='r')
    fp_len = fp.shape[0]

    # make index list for chunk/parallel processing
    buffer_size = 200
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

    if CONFIG.resources.multi_processing:
        res = parmap.map(
            load_waveforms_,
            zip(idx_list, proc_indexes),
            spike_train,
            spike_size,
            n_channels,
            buffer_size,
            standardized_filename,
            processes=n_processors,
            pm_pbar=False)
    else:
        res = []
        for k in range(len(idx_list)):
            temp = load_waveforms_(
                [idx_list[k], k], spike_train, spike_size, 
                n_channels, buffer_size, standardized_filename)
            res.append(temp)

    # Reconstruct templates from parallel proecessing
    wfs = np.vstack(res)
    #print wfs.shape
    
    return wfs

def load_waveforms_(data_in, spike_train, spike_size,
                    n_channels, buffer_size,
                    standardized_filename):

    idx_list = data_in[0]

    # New indexes
    idx_start = idx_list[0]
    idx_stop = idx_list[1]
    idx_local = idx_list[2]

    data_start = idx_start
    data_end = idx_stop
    offset = idx_local

    # ***** LOAD RAW RECORDING *****
    with open(standardized_filename, "rb") as fin:
        if data_start == 0:
            # Seek position and read N bytes
            recordings_1D = np.fromfile(
                fin,
                dtype='float32',
                count=(data_end + buffer_size) * n_channels)
            recordings_1D = np.hstack((np.zeros(
                buffer_size * n_channels, dtype='float32'), recordings_1D))
        else:
            fin.seek((data_start - buffer_size) * 4 * n_channels, os.SEEK_SET)
            recordings_1D = np.fromfile(
                fin,
                dtype='float32',
                count=((data_end - data_start + buffer_size * 2) * n_channels))

        if len(recordings_1D) != (
              (data_end - data_start + buffer_size * 2) * n_channels):
            recordings_1D = np.hstack((recordings_1D,
                                       np.zeros(
                                           buffer_size * n_channels,
                                           dtype='float32')))

    fin.close()

    # Convert to 2D array
    recording = recordings_1D.reshape(-1, n_channels)
                                    
    # convert spike train back to 0-offset values for indexeing into recordings
    indexes = np.where(np.logical_and(spike_train[:,0]>=data_start, 
                                      spike_train[:,0]<data_end))[0]
    spike_train = spike_train[indexes]-data_start 

    # read all waveforms at once
    waveforms = recording[spike_train[:, [0]].astype('int32')+offset
                  + np.arange(-spike_size, spike_size + 1)]

    return waveforms    
























