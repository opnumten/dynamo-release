from tqdm import tqdm
import inspect
import pandas as pd
from scipy.sparse import issparse, SparseEfficiencyWarning

from .moments import moments
from .velocity import velocity
from .velocity import ss_estimation
from .estimation_kinetic import *
from .utils_kinetic import *
from .utils import (
    update_dict,
    get_valid_inds,
    get_data_for_kin_params_estimation,
    get_U_S_for_velocity_estimation,
)
from .utils import set_velocity, set_param_ss, set_param_kinetic
from .moments import (
    prepare_data_no_splicing,
    prepare_data_has_splicing,
    prepare_data_deterministic,
    prepare_data_mix_has_splicing,
)

import warnings
warnings.simplefilter('ignore', SparseEfficiencyWarning)

# incorporate the model selection code soon
def dynamics(
    adata,
    tkey=None,
    t_label_keys=None,
    filter_gene_mode="final",
    use_moments=True,
    experiment_type='auto',
    assumption_mRNA='auto',
    assumption_protein="ss",
    model="auto",
    est_method="auto",
    NTR_vel=False,
    group=None,
    protein_names=None,
    concat_data=False,
    log_unnormalized=True,
    one_shot_method="combined",
    **est_kwargs
):
    """Inclusive model of expression dynamics considers splicing, metabolic labeling and protein translation. It supports
    learning high-dimensional velocity vector samples for droplet based (10x, inDrop, drop-seq, etc), scSLAM-seq, NASC-seq
    sci-fate, scNT-seq or cite-seq datasets.

    Parameters
    ----------
        adata: :class:`~anndata.AnnData`
            AnnData object.
        tkey: `str` or None (default: None)
            The column key for the time label of cells in .obs. Used for either "ss" or "kinetic" model.
            mode  with labeled data.
        t_label_keys: `str`, `list` or None (default: None)
            The column key(s) for the labeling time label of cells in .obs. Used for either "ss" or "kinetic" model.
            Not used for now and `tkey` is implicitly assumed as `t_label_key` (however, `tkey` should just be the time
            of the experiment).
        filter_gene_mode: `str` (default: `final`)
            The string for indicating which mode (one of, {'final', 'basic', 'no'}) of gene filter will be used.
        use_moments: `bool` (default: `True`)
            Whether to use the smoothed data when calculating velocity for each gene. `use_smoothed` is only relevant when
            model is `linear_regression` (and experiment_type and assumption_mRNA correspond to `conventional` and `ss` implicitly).
        experiment_type: `str` {`conventional`, `deg`, `kin`, `one-shot`, `auto`}, (default: `auto`)
            single cell RNA-seq experiment type. Available options are:
            (1) 'conventional': conventional single-cell RNA-seq experiment;
            (2) 'deg': chase/degradation experiment;
            (3) 'kin': pulse/synthesis/kinetics experiment;
            (4) 'one-shot': one-shot kinetic experiment;
            (5) 'auto': dynamo will detect the experimental type automatically.
        assumption_mRNA: `str` `str` {`ss`, `kinetic`, `auto`}, (default: `auto`)
            Parameter estimation assumption for mRNA. Available options are:
            (1) 'ss': pseudo steady state;
            (2) 'kinetic' or None: degradation and kinetic data without steady state assumption.
            If no labelling data exists, assumption_mRNA will automatically set to be 'ss'. For one-shot experiment, assumption_mRNA
            is set to be None. However we will use steady state assumption to estimate parameters alpha and gamma either by a deterministic
            linear regression or the first order decay approach in line of the sci-fate paper;
            (3) 'auto': dynamo will choose a reasonable assumption of the system under study automatically.
        assumption_protein: `str`, (default: `ss`)
            Parameter estimation assumption for protein. Available options are:
            (1) 'ss': pseudo steady state;
        model: `str` {`auto`, `deterministic`, `stochastic`} (default: `auto`)
            String indicates which estimation model will be used.
            (1) 'deterministic': The method based on `deterministic` ordinary differential equations;
            (2) 'stochastic' or `moment`: The new method from us that is based on `stochastic` master equations;
            Note that `kinetic` model doesn't need to assumes the `experiment_type` is not `conventional`. As other labeling
            experiments, if you specify the `tkey`, dynamo can also apply `kinetic` model on `conventional` scRNA-seq datasets.
            A "model_selection" model will be supported soon in which alpha, beta and gamma will be modeled as a function of time.
        est_method: `str` {`linear_regression`, `gmm`, `negbin`, `auto`} This parameter should be used in conjunction with `model` parameter.
            * Available options when the `model` is 'ss' include:
            (1) 'linear_regression': The canonical method from the seminar RNA velocity paper based on deterministic ordinary
            differential equations;
            (2) 'gmm': The new generalized methods of moments from us that is based on master equations, similar to the
            "moment" model in the excellent scVelo package;
            (3) 'negbin': The new method from us that models steady state RNA expression as a negative binomial distribution,
            also built upon on master equations.
            Note that all those methods require using extreme data points (except negbin, which use all data points) for
            estimation. Extreme data points are defined as the data from cells whose expression of unspliced / spliced
            or new / total RNA, etc. are in the top or bottom, 5%, for example. `linear_regression` only considers the mean of
            RNA species (based on the `deterministic` ordinary different equations) while moment based methods (`gmm`, `negbin`)
            considers both first moment (mean) and second moment (uncentered variance) of RNA species (based on the `stochastic`
            master equations).
            (4) 'auto': dynamo will choose the suitable estimation method based on the `assumption_mRNA`, `experiment_type`
            and `model` parameter.
            The above method are all (generalized) linear regression based method. In order to return estimated parameters
            (including RNA half-life), it additionally returns R-squared (either just for extreme data points or all data points)
            as well as the log-likelihood of the fitting, which will be used for transition matrix and velocity embedding.
            * Available options when the `assumption_mRNA` is 'kinetic' include:
            (1) 'auto': dynamo will choose the suitable estimation method based on the `assumption_mRNA`, `experiment_type`
            and `model` parameter.
            Under `kinetic` model, choosing estimation is `experiment_type` dependent. For `kinetics` experiments, dynamo
            supposes methods including RNA bursting or without RNA bursting. Dynamo also adaptively estimates parameters, based
            on whether the data has splicing or without splicing.
            Under `kinetic` assumption, the above method uses non-linear least square fitting. In order to return estimated parameters
            (including RNA half-life), it additionally returns the log-likelihood of the fittingwhich, which will be used for transition
            matrix and velocity embedding.
            All `est_method` uses least square to estimate optimal parameters with latin cubic sampler for initial sampling.
        NTR_vel: `bool` (default: `True`)
            Whether to use NTR (new/total ratio) velocity for labeling datasets.
        group: `str` or None (default: `None`)
            The column key/name that identifies the grouping information (for example, clusters that correspond to different cell types)
            of cells. This will be used to estimate group-specific (i.e cell-type specific) kinetic parameters.
        protein_names: `List`
            A list of gene names corresponds to the rows of the measured proteins in the `X_protein` of the `obsm` attribute.
            The names have to be included in the adata.var.index.
        concat_data: `bool` (default: `False`)
            Whether to concatenate data before estimation. If your data is a list of matrices for each time point, this need to be set as True.
        log_unnormalized: `bool` (default: `True`)
            Whether to log transform the unnormalized data.
        **est_kwargs
            Other arguments passed to the estimation methods. Not used for now.
    Returns
    -------
        adata: :class:`~anndata.AnnData`
            A updated AnnData object with estimated kinetic parameters and inferred velocity included.
    """

    filter_list, filter_gene_mode_list = ['use_for_dynamo', 'pass_basic_filter', 'no'], ['final', 'basic', 'no']
    filter_checker = [i in adata.var.columns for i in filter_list[:2]]
    filter_checker.append(True)
    filter_id = filter_gene_mode_list.index(filter_gene_mode)
    which_filter = np.where(filter_checker[filter_id:])[0][0] + filter_id

    filter_gene_mode = filter_gene_mode_list[which_filter]

    valid_ind = get_valid_inds(adata, filter_gene_mode)

    if model.lower() == "auto":
        model = "stochastic"
        model_was_auto = True
    else:
        model_was_auto = False

    if model.lower() == "stochastic" or use_moments:
        if len([i for i in adata.layers.keys() if i.startswith("M_")]) < 2:
            moments(adata)

    valid_adata = adata[:, valid_ind].copy()
    if group is not None and group in adata.obs.columns:
        _group = adata.obs[group].unique()
    else:
        _group = ["_all_cells"]

    for cur_grp in _group:
        if cur_grp == "_all_cells":
            kin_param_pre = ""
            cur_cells_bools = np.ones(valid_adata.shape[0], dtype=bool)
            subset_adata = valid_adata[cur_cells_bools]
        else:
            kin_param_pre = group + "_" + cur_grp + "_"
            cur_cells_bools = (valid_adata.obs[group] == cur_grp).values
            subset_adata = valid_adata[cur_cells_bools]

            if model.lower() == "stochastic" or use_moments:
                moments(subset_adata)
        (
            U,
            Ul,
            S,
            Sl,
            P,
            US,
            U2,
            S2,
            t,
            normalized,
            has_splicing,
            has_labeling,
            has_protein,
            ind_for_proteins,
            assump_mRNA,
            exp_type,
        ) = get_data_for_kin_params_estimation(
            subset_adata,
            model,
            use_moments,
            tkey,
            protein_names,
            log_unnormalized,
            NTR_vel,
        )

        if experiment_type.lower() == 'auto':
            experiment_type = exp_type
        else:
            if experiment_type != exp_type:
                warnings.warn(
                "dynamo detects the experiment type of your data as {}, but your input experiment_type "
                "is {}".format(exp_type, experiment_type)
                )

        if assumption_mRNA.lower() == 'auto': assumption_mRNA = assump_mRNA

        if model.lower() == "stochastic" and experiment_type.lower() not in ["conventional", "kinetics", "degradation", "kin", "deg", "one-shot"]:
            """
            # temporially convert to deterministic model as moment model for mix_std_stm
             and other types of labeling experiment is ongoing."""

            model = "deterministic"

        if assumption_mRNA.lower() == "ss" or (experiment_type.lower() in ["one-shot", "mix_std_stm"]):
            if est_method.lower() == "auto": est_method = "gmm"
            if experiment_type.lower() == "one_shot":
                beta = subset_adata.var.beta if "beta" in subset_adata.var.keys() else None
                gamma = subset_adata.var.gamma if "gamma" in subset_adata.var.keys() else None
                ss_estimation_kwargs = {"beta": beta, "gamma": gamma}

            else:
                ss_estimation_kwargs = {}

            est = ss_estimation(
                U=U,
                Ul=Ul,
                S=S,
                Sl=Sl,
                P=P,
                US=US,
                S2=S2,
                conn=subset_adata.uns['moments_con'],
                t=t,
                ind_for_proteins=ind_for_proteins,
                model=model,
                est_method=est_method,
                experiment_type=experiment_type,
                assumption_mRNA=assumption_mRNA,
                assumption_protein=assumption_protein,
                concat_data=concat_data,
                **ss_estimation_kwargs
            )

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")

                if experiment_type in ["one-shot", "one_shot"]:
                    est.fit(one_shot_method=one_shot_method)
                else:
                    est.fit()

            alpha, beta, gamma, eta, delta = est.parameters.values()

            U, S = get_U_S_for_velocity_estimation(
                subset_adata,
                use_moments,
                has_splicing,
                has_labeling,
                log_unnormalized,
                NTR_vel,
            )
            vel = velocity(estimation=est)
            vel_U = vel.vel_u(U)
            if exp_type == 'one-shot':
                vel_S = vel.vel_s(U, U + S)
            else:
                vel_S = vel.vel_s(U, S)
            vel_P = vel.vel_p(S, P)

            adata = set_velocity(
                adata,
                vel_U,
                vel_S,
                vel_P,
                _group,
                cur_grp,
                cur_cells_bools,
                valid_ind,
                ind_for_proteins,
            )

            adata = set_param_ss(
                adata,
                est,
                alpha,
                beta,
                gamma,
                eta,
                delta,
                experiment_type,
                _group,
                cur_grp,
                kin_param_pre,
                valid_ind,
                ind_for_proteins,
            )

        elif assumption_mRNA.lower() == "kinetic":
            if model_was_auto and experiment_type.lower() == "kin": model = "mixture"

            params, half_life, cost, logLL, param_ranges = kinetic_model(subset_adata, tkey, model, est_method, experiment_type, has_splicing,
                          has_switch=True, param_rngs={}, **est_kwargs)
            a, b, alpha_a, alpha_i, alpha, beta, gamma = (
                params.loc[:, 'a'].values if 'a' in params.columns else None,
                params.loc[:, 'b'].values if 'b' in params.columns else None,
                params.loc[:, 'alpha_a'].values if 'alpha_a' in params.columns else None,
                params.loc[:, 'alpha_i'].values if 'alpha_i' in params.columns else None,
                params.loc[:, 'alpha'].values if 'alpha' in params.columns else None,
                params.loc[:, 'beta'].values if 'beta' in params.columns else None,
                params.loc[:, 'gamma'].values if 'gamma' in params.columns else None,
            )
            if alpha is None:
                alpha = fbar(a, b, alpha_a, 0) if alpha_i is None else fbar(a, b, alpha_a, alpha_i)
            all_kinetic_params = ['a', 'b', 'alpha_a', 'alpha_i', 'alpha', 'beta', 'gamma']

            extra_params = params.loc[:, params.columns.difference(all_kinetic_params)]
            # if alpha = None, set alpha to be U; N - gamma R
            params = {"alpha": alpha, "beta": beta, "gamma": gamma, "t": t}
            vel = velocity(**params)

            U, S = get_U_S_for_velocity_estimation(
                subset_adata,
                use_moments,
                has_splicing,
                has_labeling,
                log_unnormalized,
                NTR_vel,
            )
            vel_U = vel.vel_u(U)
            vel_S = vel.vel_u(S)
            vel_P = vel.vel_p(S, P)

            adata = set_velocity(
                adata,
                vel_U,
                vel_S,
                vel_P,
                _group,
                cur_grp,
                cur_cells_bools,
                valid_ind,
                ind_for_proteins,
            )

            adata = set_param_kinetic(
                adata,
                alpha,
                a,
                b,
                alpha_a,
                alpha_i,
                beta,
                gamma,
                cost,
                logLL,
                kin_param_pre,
                extra_params,
                _group,
                cur_grp,
                valid_ind,
            )
            # add protein related parameters in the moment model below:
        elif model.lower() is "model_selection":
            warnings.warn("Not implemented yet.")

    if group is not None and group in adata.obs[group]:
        uns_key = group + "_dynamics"
    else:
        uns_key = "dynamics"

    adata.uns[uns_key] = {
        "t": t,
        "group": group,
        "asspt_mRNA": assumption_mRNA,
        "experiment_type": experiment_type,
        "normalized": normalized,
        "model": model,
        "has_splicing": has_splicing,
        "has_labeling": has_labeling,
        "has_protein": has_protein,
        "use_smoothed": use_moments,
        "NTR_vel": NTR_vel,
        "log_unnormalized": log_unnormalized,
    }

    return adata


def kinetic_model(subset_adata, tkey, model, est_method, experiment_type, has_splicing, has_switch, param_rngs, only_sfs=True, **est_kwargs):
    time = subset_adata.obs[tkey].astype('float')
    dispatcher = get_dispatcher()
    x0 = {}

    if experiment_type.lower() == 'kin':
        if has_splicing:
            if model in ['deterministic', 'stochastic']:
                layer_u = 'X_ul' if ('X_ul' in subset_adata.layers.keys() and not only_sfs) else 'ul'
                layer_s = 'X_sl' if ('X_sl' in subset_adata.layers.keys() and not only_sfs) else 'sl'

                X, X_raw = prepare_data_has_splicing(subset_adata, subset_adata.var.index, time, layer_u=layer_u, layer_s=layer_s)
            elif model.startswith('mixture'):
                layers = ['X_ul', 'X_sl', 'X_uu', 'X_su'] if ('X_ul' in subset_adata.layers.keys() and not only_sfs) \
                    else ['ul', 'sl', 'uu', 'su']

                X, _, X_raw = prepare_data_deterministic(subset_adata, subset_adata.var.index, time, layers=layers)

            if model == 'deterministic': # 0 - to 10 initial value
                X = [X[i][[0, 1], :] for i in range(len(X))]
                _param_ranges = {'alpha': [0, 1000], 'beta': [0, 1000], 'gamma': [0, 1000]}
                x0 = {'u0': [0, 1000], 's0': [0, 1000]}
                Est, simulator = Estimation_DeterministicKin, Deterministic
            elif model == 'stochastic':
                x0 = {'u0': [0, 1000], 's0': [0, 1000],
                      'uu0': [0, 1000], 'ss0': [0, 1000],
                      'us0': [0, 1000]}

                if has_switch:
                    _param_ranges = {'a': [0, 1000], 'b': [0, 1000],
                                    'alpha_a': [0, 1000], 'alpha_i': 0,
                                    'beta': [0, 1000], 'gamma': [0, 1000], }
                    Est, simulator = Estimation_MomentKin, Moments
                else:
                    _param_ranges = {'alpha': [0, 1000], 'beta': [0, 1000], 'gamma': [0, 1000], }

                    Est, simulator = Estimation_MomentKinNoSwitch, Moments_NoSwitching
            elif model == 'mixture':
                _param_ranges = {'alpha': [0, 1000], 'alpha_2': [0, 0], 'beta': [0, 1000], 'gamma': [0, 1000], }
                x0 = {'ul0': [0, 0], 'sl0': [0, 0], 'uu0': [0, 1000], 'su0': [0, 1000]}

                Est = Mixture_KinDeg_NoSwitching(Deterministic(), Deterministic())
            elif model == 'mixture_deterministic_stochastic':
                X, X_raw = prepare_data_mix_has_splicing(subset_adata, subset_adata.var.index, time, layer_u=layers[2], layer_s=layers[3],
                                                  layer_ul=layers[0], layer_sl=layers[1], use_total_layers=True,
                                                  mix_model_indices=[0, 1, 5, 6, 7, 8, 9])

                _param_ranges = {'alpha': [0, 1000], 'alpha_2': [0, 0], 'beta': [0, 1000], 'gamma': [0, 1000], }
                x0 = {'ul0': [0, 0], 'sl0': [0, 0],
                      'u0': [0, 1000], 's0': [0, 1000],
                      'uu0': [0, 1000], 'ss0': [0, 1000],
                      'us0': [0, 1000], }
                Est = Mixture_KinDeg_NoSwitching(Deterministic(), Moments_NoSwitching())
            elif model == 'mixture_stochastic_stochastic':
                _param_ranges = {'alpha': [0, 1000], 'alpha_2': [0, 0], 'beta': [0, 1000], 'gamma': [0, 1000], }
                X = prepare_data_mix_has_splicing(subset_adata, subset_adata.var.index, time, layer_u=layers[2], layer_s=layers[3],
                                                  layer_ul=layers[0], layer_sl=layers[1], use_total_layers=True,
                                                  mix_model_indices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
                x0 = {'ul0': [0, 1000], 'sl0': [0, 1000],
                      'ul_ul0': [0, 1000], 'sl_sl0': [0, 1000],
                      'ul_sl0': [0, 1000],
                      'u0': [0, 1000], 's0': [0, 1000],
                      'uu0': [0, 1000], 'ss0': [0, 1000],
                      'us0': [0, 1000], }
                Est = Mixture_KinDeg_NoSwitching(Moments_NoSwitching(), Moments_NoSwitching())
            else:
                raise NotImplementedError(f'model {model} with kinetic assumption is not implemented. '
                                f'current supported models for kinetics experiments include: stochastic, deterministic, mixture,'
                                f'mixture_deterministic_stochastic or mixture_stochastic_stochastic')
        else:
            if model in ['deterministic', 'stochastic']:
                layer = 'X_new' if  ('X_new' in subset_adata.layers.keys() and not only_sfs) else 'new'
                X, X_raw = prepare_data_no_splicing(subset_adata, subset_adata.var.index, time, layer=layer)
            elif model.startswith('mixture'):
                layers = ['X_new', 'X_total'] if ('X_new' in subset_adata.layers.keys() and not only_sfs) else ['new', 'total']

                X, _, X_raw = prepare_data_deterministic(subset_adata, subset_adata.var.index, time, layers=layers)

            if model == 'deterministic':
                X = [X[i][0, :] for i in range(len(X))]
                _param_ranges = {'alpha': [0, 1000], 'gamma': [0, 1000], }
                x0 = {'u0': [0, 1000]}
                Est, simulator = Estimation_DeterministicKinNosp, Deterministic_NoSplicing
            elif model == 'stochastic':
                x0 = {'u0': [0, 1000], 'uu0': [0, 1000], }
                if has_switch:
                    _param_ranges = {'a': [0, 1000], 'b': [0, 1000],
                                    'alpha_a': [0, 1000], 'alpha_i': 0,
                                    'gamma': [0, 1000], }
                    Est, simulator = Estimation_MomentKinNosp, Moments_Nosplicing
                else:
                    _param_ranges = {'alpha': [0, 1000], 'gamma': [0, 1000], }
                    Est, simulator = Estimation_MomentKinNoSwitchNoSplicing, Moments_NoSwitchingNoSplicing
            elif model == 'mixture':
                _param_ranges = {'alpha': [0, 1000], 'gamma': [0, 1000], }
                # x0 = {'u0': [0, 1000]}
                Est = Mixture_KinDeg_NoSwitching(Deterministic_NoSplicing(), Deterministic_NoSplicing())
            elif model == 'mixture_deterministic_stochastic':
                _param_ranges = {'alpha': [0, 1000], 'gamma': [0, 1000], }
                # x0 = {'u0': [0, 1000], 'uu0': [0, 1000]}
                Est = Mixture_KinDeg_NoSwitching(Deterministic_NoSplicing(), Moments_NoSwitchingNoSplicing())
            elif model == 'mixture_stochastic_stochastic':
                _param_ranges = {'alpha': [0, 1000], 'gamma': [0, 1000], }
                # x0 = {'u0': [0, 1000], 'uu0': [0, 1000]}
                Est = Mixture_KinDeg_NoSwitching(Moments_NoSwitchingNoSplicing(), Moments_NoSwitchingNoSplicing())
            raise Exception(f'model {model} with kinetic assumption is not implemented. '
                            f'current supported models for kinetics experiments include: stochastic, deterministic, mixture,'
                            f'mixture_deterministic_stochastic or mixture_stochastic_stochastic')
    elif experiment_type.lower() == 'deg':
        if has_splicing:
            if model in ['deterministic', 'stochastic']:
                layer_u = 'X_ul' if ('X_ul' in subset_adata.layers.keys() and not only_sfs) else 'ul'
                layer_s = 'X_sl' if ('X_sl' in subset_adata.layers.keys() and not only_sfs) else 'sl'

                X, X_raw = prepare_data_has_splicing(subset_adata, subset_adata.var.index, time, layer_u=layer_u, layer_s=layer_s)
                # X_sigma = [X[i][[2, 3], :] for i in range(len(X))]
            elif model.startswith('mixture'):
                layers = ['X_ul', 'X_sl', 'X_uu', 'X_su'] if ('X_ul' in subset_adata.layers.keys() and not only_sfs) \
                    else ['ul', 'sl', 'uu', 'su']

                X, _, X_raw = prepare_data_deterministic(subset_adata, subset_adata.var.index, time, layers=layers)

            if model == 'deterministic':
                X = [X[i][[0, 1] , :]for i in range(len(X))]
                _param_ranges = {'beta': [0, 1000], 'gamma': [0, 1000], }
                x0 = {'u0': [0, 1000], 's0': [0, 1000], }
                Est, simulator = Estimation_DeterministicDeg, Deterministic
            elif model == 'stochastic':
                _param_ranges = {'beta': [0, 1000], 'gamma': [0, 1000], }
                x0 = {'u0': [0, 1000], 's0': [0, 1000],
                      'uu0': [0, 1000], 'ss0': [0, 1000],
                      'us0': [0, 1000], }
                Est, simulator = Estimation_MomentDeg, Moments_NoSwitching
            raise Exception(f'model {model} with kinetic assumption is not implemented. '
                            f'current supported models for degradation experiment include: '
                            f'stochastic, deterministic.')
        else:
            layer = 'X_new' if ('X_new' in subset_adata.layers.keys() and not only_sfs) else 'new'
            X, X_raw = prepare_data_no_splicing(subset_adata, subset_adata.var.index, time, layer=layer)
            # X_sigma = [X[i][1, :] for i in range(len(X))]

            if model == 'deterministic':
                X = [X[i][0, :] for i in range(len(X))]
                _param_ranges = {'gamma': [0, 10], }
                x0 = {'u0': [0, 1000]}
                Est, simulator = Estimation_DeterministicDegNosp, Deterministic_NoSplicing
            elif model == 'stochastic':
                _param_ranges = {'gamma': [0, 10], }
                x0 = {'u0': [0, 1000], 'uu0': [0, 1000]}
                Est, simulator = Estimation_MomentDegNosp, Moments_NoSwitchingNoSplicing
            raise Exception(f'model {model} with kinetic assumption is not implemented. '
                            f'current supported models for degradation experiment include: '
                            f'stochastic, deterministic.')
    elif experiment_type.lower() == 'mix_std_stm':
        raise Exception(f'experiment {experiment_type} with kinetic assumption is not implemented')
    elif experiment_type.lower() == 'mix_pulse_chase':
        raise Exception(f'experiment {experiment_type} with kinetic assumption is not implemented')
    elif experiment_type.lower() == 'pulse_time_series':
        raise Exception(f'experiment {experiment_type} with kinetic assumption is not implemented')
    elif experiment_type.lower() == 'dual_labeling':
        raise Exception(f'experiment {experiment_type} with kinetic assumption is not implemented')
    else:
        raise Exception(f'experiment {experiment_type} is not recognized')

    _param_ranges = update_dict(_param_ranges, param_rngs)
    x0_ = np.vstack([ran for ran in x0.values()]).T if x0 != {} else {}

    n_genes = subset_adata.n_vars
    cost, logLL = np.zeros(n_genes), np.zeros(n_genes)
    all_keys = list(_param_ranges.keys()) + list(x0.keys())
    all_keys = [cur_key for cur_key in all_keys if cur_key != 'alpha_i']
    half_life, Estm = np.zeros(n_genes), [None] * n_genes #np.zeros((len(X), len(all_keys)))

    for i_gene in tqdm(range(n_genes), desc="estimating kinetic-parameters using kinetic model"):
        if model.startswith('mixture'):
            estm = Est
            if model == 'mixture':
                cur_X_data = np.vstack([X[i_layer][i_gene] for i_layer in range(len(X))])
                if issparse(X_raw[0]):
                    cur_X_raw = np.hstack([X_raw[i_layer][:, i_gene].A for i_layer in range(len(X))])
                else:
                    cur_X_raw = np.hstack([X_raw[i_layer][:, i_gene] for i_layer in range(len(X))])
            else:
                cur_X_data = X[i_gene]
                cur_X_raw = X_raw[i_gene]

            if issparse(cur_X_raw[0, 0]):
                cur_X_raw = np.hstack((cur_X_raw[0, 0].A, cur_X_raw[1, 0].A))

            _, cost[i_gene] = estm.auto_fit(np.unique(time), cur_X_data)
            model_1, model_2, kinetic_parameters, mix_x0 = estm.export_dictionary().values()
            tmp = list(kinetic_parameters.values())
            tmp.extend(mix_x0)
            Estm[i_gene] = tmp
            _MixtureModels = dispatcher[type(Est).__name__]
            simulator = _MixtureModels([dispatcher[model_1], dispatcher[model_2]], estm.param_distributor)
        else:
            if experiment_type.lower() == 'kin':
                cur_X_data, cur_X_raw = X[i_gene], X_raw[i_gene]

                alpha0 = guestimate_alpha(np.sum(cur_X_data, 0), np.unique(time))
                if model =='stochastic':
                    _param_ranges.update({'alpha_a': [0, alpha0*10]})
                elif model == 'deterministic':
                    _param_ranges.update({'alpha': [0, alpha0 * 10]})
                param_ranges = [ran for ran in _param_ranges.values()]
                estm = Est(*param_ranges, x0=x0_) if 'x0' in inspect.getfullargspec(Est) \
                    else Est(*param_ranges)
                Estm[i_gene], cost[i_gene] = estm.fit_lsq(np.unique(time), cur_X_data, **est_kwargs)
            elif experiment_type.lower() == 'deg':
                estm = Est()
                cur_X_data, cur_X_raw = X[i_gene], X_raw[i_gene]

                Estm[i_gene], cost[i_gene] = estm.auto_fit(np.unique(time), cur_X_data)

            if issparse(cur_X_raw[0, 0]):
                cur_X_raw = np.hstack((cur_X_raw[0, 0].A, cur_X_raw[1, 0].A))

        half_life[i_gene] = np.log(2)/Estm[i_gene][-1] if experiment_type.lower() == 'kin' else estm.calc_half_life('gamma')
        if model.startswith('mixture'):
            gof = GoodnessOfFit(estm.export_model(), params=estm.export_parameters())
        else:
            gof = GoodnessOfFit(estm.export_model(), params=estm.export_parameters(), x0=estm.simulator.x0)

        gof.prepare_data(time, cur_X_raw.T, normalize=True)
        logLL[i_gene] = gof.calc_gaussian_loglikelihood()

    Estm_df = pd.DataFrame(np.vstack(Estm), columns=[*all_keys[:len(Estm[0])]])

    return Estm_df, half_life, cost, logLL, _param_ranges


def fbar(a, b, alpha_a, alpha_i):
    if any([i is None for i in [a, b, alpha_a, alpha_i]]):
        return None
    else:
        return b / (a + b) * alpha_a + a / (a + b) * alpha_i


def get_dispatcher():
    dispatcher = {'Deterministic': Deterministic,
                  'Deterministic_NoSplicing': Deterministic_NoSplicing,
                  'Moments_NoSwitching': Moments_NoSwitching,
                  'Moments_NoSwitchingNoSplicing': Moments_NoSwitchingNoSplicing,
                  'Mixture_KinDeg_NoSwitching': Mixture_KinDeg_NoSwitching,
                  }

    return dispatcher
