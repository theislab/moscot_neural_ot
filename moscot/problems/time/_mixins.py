from typing import Any, Dict, List, Tuple, Union, Mapping, Optional, Protocol, Sequence, TYPE_CHECKING
import logging
import itertools

from sklearn.metrics import pairwise_distances
from pandas.core.dtypes.common import is_categorical_dtype
import ot
import pandas as pd

import numpy as np

from anndata import AnnData

from moscot._docs import d
from moscot._types import ArrayLike, Numeric_t
from moscot.problems.base._mixins import AnalysisMixin, AnalysisMixinProtocol
from moscot.problems.base._compound_problem import B, K, ApplyOutput_t


class TemporalMixinProtocol(AnalysisMixinProtocol[K, B], Protocol[K, B]):
    """Protocol class."""

    problems: Dict[Tuple[K, K], B]
    temporal_key: Optional[str]

    def push(self, *args: Any, **kwargs: Any) -> Optional[ApplyOutput_t[K]]:
        ...

    def pull(self, *args: Any, **kwargs: Any) -> Optional[ApplyOutput_t[K]]:
        ...

    def _validate_args_cell_transition(
        self: "TemporalMixinProtocol[K, B]", arg: Union[str, Mapping[str, Sequence[Any]]]
    ) -> Tuple[Union[str, Sequence[Any]], Sequence[Any]]:
        ...


class TemporalMixin(AnalysisMixin[K, B]):
    """Analysis Mixin for all problems involving a temporal dimension."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._temporal_key: Optional[str] = None

    def push(
        self: TemporalMixinProtocol[K, B],
        start: K,
        end: K,
        result_key: Optional[str] = None,
        return_all: bool = False,
        scale_by_marginals: bool = True,
        **kwargs: Any,
    ) -> Optional[ApplyOutput_t[K]]:
        """
        Push distribution of cells through time.

        Parameters
        ----------
        start
            Time point of source distribution.
        target
            Time point of target distribution.
        result_key
            Key of where to save the result in :attr:`anndata.AnnData.obs`. If None the result will be returned.
        return_all
            If `True` returns all the intermediate masses if pushed through multiple transport plans.
            If `True`, the result is returned as a dictionary.

        Returns
        -------
        Depending on `result_key` updates `adata` or returns the result. In the former case all intermediate results
        (corresponding to intermediate time points) are saved in :attr:`anndata.AnnData.obs`. In the latter case all
        intermediate step results are returned if `return_all` is `True`, otherwise only the distribution at `end`
        is returned.

        Raises
        ------
        %(CompoundBaseProblem_push.raises)s
        """
        result = self._apply(
            start=start,
            end=end,
            forward=True,
            return_all=return_all or result_key is not None,
            scale_by_marginals=scale_by_marginals,
            **kwargs,
        )

        if result_key is None:
            return result
        if TYPE_CHECKING:
            assert isinstance(result, dict)
        self.adata.obs[result_key] = self._flatten(result, key=self.temporal_key)

    @d.dedent
    def pull(
        self: TemporalMixinProtocol[K, B],
        start: K,
        end: K,
        result_key: Optional[str] = None,
        return_all: bool = False,
        scale_by_marginals: bool = True,
        **kwargs: Any,
    ) -> Optional[ApplyOutput_t[K]]:
        """
        Pull distribution of cells from time point `end` to time point `start`.

        Parameters
        ----------
        start
            Earlier time point, the time point the mass is pulled to.
        end
            Later time point, the time point the mass is pulled from.
        result_key
            Key of where to save the result in :attr:`anndata.AnnData.obs`. If `None` the result will be returned.
        return_all
            If `True` return all the intermediate masses if pushed through multiple transport plans. In this case the
            result is returned as a dictionary.

        Returns
        -------
        Depending on `result_key` updates `adata` or returns the result. In the former case all intermediate results
        (corresponding to intermediate time points) are saved in :attr:`anndata.AnnData.obs`. In the latter case all
        intermediate step results are returned if `return_all` is `True`, otherwise only the distribution at `start`
        is returned.

        Raises
        ------
        %(CompoundBaseProblem_pull.raises)s
        """
        result = self._apply(
            start=start,
            end=end,
            forward=False,
            return_all=return_all or result_key is not None,
            scale_by_marginals=scale_by_marginals,
            **kwargs,
        )
        if result_key is None:
            return result
        if TYPE_CHECKING:
            assert isinstance(result, dict)
        self.adata.obs[result_key] = self._flatten(result, key=self.temporal_key)

    def cell_transition(
        self: TemporalMixinProtocol[K, B],
        start: K,
        end: K,
        early_cells: Union[str, Mapping[str, Sequence[Any]]],
        late_cells: Union[str, Mapping[str, Sequence[Any]]],
        forward: bool = False,  # return value will be row-stochastic if forward=True, else column-stochastic
        **kwargs: Any,
    ) -> pd.DataFrame:
        """
        Compute a grouped cell transition matrix.

        This function computes a transition matrix with entries corresponding to categories, e.g. cell types.
        The transition matrix will be row-stochastic if `forward` is `True`, otherwise column-stochastic.

        Parameters
        ----------
        start
            Time point corresponding to the early distribution.
        end
            Time point corresponding to the late distribution.
        early_cells
            Can be one of the following
                - if `early_cells` is of type :class:`str` this should correspond to a key in
                  :attr:`anndata.AnnData.obs`. In this case, the categories in the transition matrix correspond to the
                  unique values in `anndata.AnnData.obs` ``['{early_cells}']``
                - if `early_cells` is of type `Mapping` its `key` should correspond to a key in
                  :attr:`anndata.AnnData.obs` and its `value` to a subset of categories present in
                  `anndata.AnnData.obs` ``['{early_cells.keys()[0]}']``
        late_cells
            Can be one of the following
                - if `late_cells` is of type :class:`str` this should correspond to a key in
                  :attr:`anndata.AnnData.obs`. In this case, the categories in the transition matrix correspond to the
                  unique values in `anndata.AnnData.obs` ``['{late_cells}']``
                - if `late_cells` is of type `Mapping` its `key` should correspond to a key in
                  :attr:`anndata.AnnData.obs` and its `value` to a subset of categories present in
                  `anndata.AnnData.obs` ``['{late_cells.keys()[0]}']``

        Returns
        -------
        Transition matrix of groups between time points.
        """
        _early_cells_key, _early_cells = self._validate_args_cell_transition(early_cells)
        _late_cells_key, _late_cells = self._validate_args_cell_transition(late_cells)

        transition_table = pd.DataFrame(
            np.zeros((len(_early_cells), len(_late_cells))), index=_early_cells, columns=_late_cells
        )

        df_late = self.adata[self.adata.obs[self.temporal_key] == end].obs[[_late_cells_key]].copy()
        df_early = self.adata[self.adata.obs[self.temporal_key] == start].obs[[_early_cells_key]].copy()
        df_late["distribution"] = np.nan
        df_early["distribution"] = np.nan

        if forward:
            _early_cells_present = set(_early_cells).intersection(set(df_early[_early_cells_key].unique()))
            for subset in _early_cells:
                if subset not in _early_cells_present:
                    transition_table.loc[subset, :] = np.nan
                    continue
                try:
                    result = self.push(
                        start=start,
                        end=end,
                        data=_early_cells_key,
                        subset=subset,
                        normalize=True,
                        return_all=False,
                        scale_by_marginals=False,
                        **kwargs,
                    )
                except ValueError as e:
                    if "no mass" in str(e):  # TODO: adapt
                        logging.info(
                            f"No data points corresponding to {subset} found in `adata.obs[groups_key]` for {start}"
                        )
                        result = np.nan  # type: ignore[assignment]
                    else:
                        raise
                df_late.loc[:, "distribution"] = result
                target_cell_dist = df_late[df_late[_late_cells_key].isin(_late_cells)].groupby(_late_cells_key).sum()
                target_cell_dist /= target_cell_dist.sum()
                transition_table.loc[subset, :] = [
                    target_cell_dist.loc[cell_type, "distribution"]
                    if cell_type in target_cell_dist.distribution.index
                    else 0
                    for cell_type in _late_cells
                ]
            return transition_table
        _late_cells_present = set(_late_cells).intersection(set(df_late[_late_cells_key].unique()))
        for subset in _late_cells:
            if subset not in _late_cells_present:
                transition_table.loc[:, subset] = np.nan
                continue
            try:
                result = self.pull(
                    start=start,
                    end=end,
                    data=_late_cells_key,
                    subset=subset,
                    normalize=True,
                    return_all=False,
                    scale_by_marginals=False,
                    **kwargs,
                )
            except ValueError as e:
                if "no mass" in str(e):  # TODO: adapt
                    logging.info(f"No data points corresponding to {subset} found in `adata.obs[groups_key]` for {end}")
                    result = np.nan  # type: ignore[assignment]
                else:
                    raise
            df_early.loc[:, "distribution"] = result
            target_cell_dist = df_early[df_early[_early_cells_key].isin(_early_cells)].groupby(_early_cells_key).sum()
            target_cell_dist /= target_cell_dist.sum()
            transition_table.loc[:, subset] = [
                target_cell_dist.loc[cell_type, "distribution"]
                if cell_type in target_cell_dist.distribution.index
                else 0
                for cell_type in _early_cells
            ]
        return transition_table

    def _validate_args_cell_transition(
        self: TemporalMixinProtocol[K, B], arg: Union[str, Mapping[str, Sequence[Any]]]
    ) -> Tuple[Union[str, Sequence[Any]], Sequence[Any]]:
        if isinstance(arg, str):
            if arg not in self.adata.obs:
                raise KeyError("TODO")
            if not is_categorical_dtype(self.adata.obs[arg]):
                raise TypeError(f"The column `{arg}` in `adata.obs` must be of categorical dtype.")
            if self.adata.obs[arg].isnull().values.any():  # TODO(giovp, MUCDK): why this check? remove?
                raise ValueError(f"The column `{arg}` in `adata.obs` contains NaN values. Please check.")
            return arg, self.adata.obs[arg].cat.categories
        if isinstance(arg, dict):
            if len(arg.keys()) > 1:
                raise ValueError(
                    f"The length of the dictionary is {len(arg)} but should be 1 as the data can only be filtered "
                    f"according to one column of `adata.obs`."
                )
            _key = list(arg.keys())[0]
            _val = arg[_key]
            if not is_categorical_dtype(self.adata.obs[_key]):
                raise TypeError(f"The column `{_key}` in `adata.obs` must be of categorical dtype.")
            if not set(_val).issubset(self.adata.obs[_key].cat.categories):
                raise ValueError(f"Not all values {_val} could be found in `adata.obs[{_key}]`.")
            if self.adata.obs[_key].isnull().values.any():
                raise ValueError(f"The column `{_key}` in `adata.obs` contains NaN values. Please check.")
            return _key, _val

    # TODO(michalk8): refactor me
    def _get_data(
        self: TemporalMixinProtocol[K, B],
        start: K,
        intermediate: Optional[K] = None,
        end: Optional[K] = None,
        *,
        only_start: bool = False,
    ) -> Union[Tuple[ArrayLike, AnnData], Tuple[ArrayLike, ArrayLike, ArrayLike, AnnData, ArrayLike]]:
        # TODO: use .items()
        for (src, tgt) in self.problems:
            tag = self.problems[src, tgt].xy.tag  # type: ignore[union-attr]
            if tag != "point_cloud":
                raise ValueError(
                    "TODO: This method requires the data to be stored as point_clouds. It is currently stored "  # type: ignore[union-attr]
                    f"as {self.problems[src, tgt].xy.tag}."
                )
            if src == start:
                source_data = self.problems[src, tgt].xy.data  # type: ignore[union-attr]
                if only_start:
                    return source_data, self.problems[src, tgt].adata
                # TODO(michalk8): posterior marginals
                growth_rates_source = self.problems[src, tgt].growth_rates  # type: ignore[attr-defined]
                break
        else:
            raise ValueError(f"No data found for time point {start}")
        for (src, tgt) in self.problems.keys():
            if src == intermediate:
                intermediate_data = self.problems[src, tgt].xy.data  # type: ignore[union-attr]
                intermediate_adata = self.problems[src, tgt].adata
                break
        else:
            raise ValueError(f"No data found for time point {intermediate}")
        for (src, tgt) in self.problems.keys():
            if tgt == end:
                target_data = self.problems[src, tgt].xy.data_y  # type: ignore[union-attr]
                break
        else:
            raise ValueError(f"No data found for time point {end}")

        return source_data, growth_rates_source, intermediate_data, intermediate_adata, target_data  # type: ignore[return-value]

    def compute_interpolated_distance(
        self,
        start: K,
        intermediate: K,
        end: K,
        interpolation_parameter: Optional[float] = None,
        n_interpolated_cells: Optional[int] = None,
        account_for_unbalancedness: bool = False,
        batch_size: int = 256,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Numeric_t:
        """
        Compute the Wasserstein distance between the OT-interpolated distribution and the true cell distribution.

        This is a validation method which interpolates the cell distributions corresponding to `start` and `end`
        leveraging the OT coupling to obtain an approximation of the cell distribution at time point `intermediate`.
        Therefore, the Wasserstein distance between the interpolated and the real distribution is computed.

        It is recommended to compare the Wasserstein distance to the ones obtained by
        :meth:`compute_time_point_distances`,
        :meth:`compute_random_distance`, and
        :meth:`compute_time_point_distance`.

        This method does not instantiate the transport matrix if the solver output does not.

        TODO: link to notebook


        Parameters
        ----------
        start
            Time point corresponding to the early distribution.
        intermediate
            Time point corresponding to the intermediate distribution which is to be interpolated.
        end
            Time point corresponding to the late distribution.
        interpolation_parameter
            Interpolation parameter determining the weight of the source and the target distribution. If `None`
            it is linearly inferred from `source`, `intermediate`, and `target`.
        n_interpolated_cells
            Number of generated interpolated cell. If `None` the number of data points in the `intermediate`
            distribution is taken.
        account_for_unbalancedness
            If `True` unbalancedness is accounted for by assuming exponential growth and death of cells.
        batch_size
            Number of cells simultaneously generated by interpolation.
        seed
            Random seed for sampling from the transport matrix.
        kwargs
            Keyword arguments for computing the Wasserstein distance (TODO make that function public?)

        Returns
        -------
        Wasserstein distance between OT-based interpolated distribution and the true cell distribution.
        """
        source_data, _, intermediate_data, _, target_data = self._get_data(  # type: ignore[misc]
            start,
            intermediate,
            end,
            only_start=False,
        )
        interpolation_parameter = self._get_interp_param(
            start, intermediate, end, interpolation_parameter=interpolation_parameter
        )
        n_interpolated_cells = n_interpolated_cells if n_interpolated_cells is not None else len(intermediate_data)
        interpolation = self._interpolate_gex_with_ot(
            number_cells=n_interpolated_cells,
            source_data=source_data,
            target_data=target_data,
            start=start,
            end=end,
            interpolation_parameter=interpolation_parameter,
            account_for_unbalancedness=account_for_unbalancedness,
            batch_size=batch_size,
            seed=seed,
        )
        return self._compute_wasserstein_distance(intermediate_data, interpolation, **kwargs)

    def compute_random_distance(
        self,
        start: K,
        intermediate: K,
        end: K,
        interpolation_parameter: Optional[float] = None,
        n_interpolated_cells: Optional[int] = None,
        account_for_unbalancedness: bool = False,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Numeric_t:
        """
        Compute the Wasserstein distance of a randomly interpolated cell distribution and the true cell distribution.

        This method interpolates the cell trajectories at the `intermediate` time point using a random coupling and
        computes the distance to the true cell distribution.

        TODO: link to notebook

        Parameters
        ----------
        start
            Time point corresponding to the early distribution.
        intermediate
            Time point corresponding to the intermediate distribution which is to be interpolated.
        end
            Time point corresponding to the late distribution.
        interpolation_parameter
            Interpolation parameter determining the weight of the source and the target distribution. If `None`
            it is linearly inferred from `source`, `intermediate`, and `target`.
        n_interpolated_cells
            Number of generated interpolated cell. If `None` the number of data points in the `intermediate`
            distribution is taken.
        account_for_unbalancedness
            If `True` unbalancedness is accounted for by assuming exponential growth and death of cells.
        seed
            Random seed for generating randomly interpolated cells.
        kwargs
            Keyword arguments for computing the Wasserstein distance (TODO make that function public?)

        Returns
        -------
        The Wasserstein distance between a randomly interpolated cell distribution and the true cell distribution.
        """
        source_data, growth_rates_source, intermediate_data, _, target_data = self._get_data(  # type: ignore[misc]
            start, intermediate, end, only_start=False
        )

        interpolation_parameter = self._get_interp_param(
            start, intermediate, end, interpolation_parameter=interpolation_parameter
        )
        n_interpolated_cells = n_interpolated_cells if n_interpolated_cells is not None else len(intermediate_data)

        growth_rates = growth_rates_source if account_for_unbalancedness else None
        random_interpolation = self._interpolate_gex_randomly(
            n_interpolated_cells,
            source_data,
            target_data,
            interpolation_parameter=interpolation_parameter,
            growth_rates=growth_rates,
            seed=seed,
        )
        return self._compute_wasserstein_distance(intermediate_data, random_interpolation, **kwargs)

    def compute_time_point_distances(
        self, start: K, intermediate: K, end: K, **kwargs: Any
    ) -> Tuple[Numeric_t, Numeric_t]:
        """
        Compute the Wasserstein distance of cell distributions between time points.

        This method computes the Wasserstein distance between the cell distribution corresponding to `start` and `
        intermediate` and `intermediate` and `end`, respectively.

        TODO: link to notebook

        Parameters
        ----------
        start
            Time point corresponding to the early distribution.
        intermediate
            Time point corresponding to the intermediate distribution.
        end
            Time point corresponding to the late distribution.
        kwargs
            Keyword arguments for computing the Wasserstein distance (TODO make that function public?).
        """
        source_data, _, intermediate_data, _, target_data = self._get_data(  # type: ignore[misc]
            start,
            intermediate,
            end,
            only_start=False,
        )

        distance_source_intermediate = self._compute_wasserstein_distance(source_data, intermediate_data, **kwargs)
        distance_intermediate_target = self._compute_wasserstein_distance(intermediate_data, target_data, **kwargs)

        return distance_source_intermediate, distance_intermediate_target

    def compute_batch_distances(self, time: K, batch_key: str, **kwargs: Any) -> Numeric_t:
        """
        Compute the mean Wasserstein distance between batches of a distribution corresponding to one time point.

        Parameters
        ----------
        time
            Time point corresponding to the cell distribution which to compute the batch distances within.
        batch_key
            Key in :attr:`anndata.AnnData.obs` storing which batch each cell belongs to.
        kwargs
            Keyword arguments for computing the Wasserstein distance (TODO make that function public?).

        Returns
        -------
        The mean Wasserstein distance between batches of a distribution corresponding to one time point.
        """
        data, adata = self._get_data(time, only_start=True)  # type: ignore[misc]
        assert len(adata) == len(data), "TODO: wrong shapes"
        dist: List[Numeric_t] = []
        for batch_1, batch_2 in itertools.combinations(adata.obs[batch_key].unique(), 2):
            dist.append(
                self._compute_wasserstein_distance(
                    data[(adata.obs[batch_key] == batch_1).values, :],
                    data[(adata.obs[batch_key] == batch_2).values, :],
                    **kwargs,
                )
            )
        return np.mean(dist)

    # TODO(@MUCDK) possibly offer two alternatives, once exact EMD with POT backend and once approximate,
    # faster with same solver as used for original problems
    def _compute_wasserstein_distance(
        self,
        point_cloud_1: ArrayLike,
        point_cloud_2: ArrayLike,
        a: Optional[ArrayLike] = None,
        b: Optional[ArrayLike] = None,
        **kwargs: Any,
    ) -> Numeric_t:
        cost_matrix = pairwise_distances(
            point_cloud_1, Y=point_cloud_2, metric="sqeuclidean", n_jobs=-1
        )  # TODO(MUCDK): probably change n_jobs=-1, not nice to use all core available by defaults
        _a = [] if a is None else a
        _b = [] if b is None else b
        return np.sqrt(ot.emd2(_a, _b, cost_matrix, **kwargs))  # TODO(MUCDK): don't use POT

    def _interpolate_gex_with_ot(
        self,
        number_cells: int,
        source_data: ArrayLike,
        target_data: ArrayLike,
        start: K,
        end: K,
        interpolation_parameter: float,
        account_for_unbalancedness: bool = True,
        batch_size: int = 256,
        seed: Optional[int] = None,
    ) -> ArrayLike:
        rows_sampled, cols_sampled = self._sample_from_tmap(  # type: ignore[misc]
            start=start,
            end=end,
            n_samples=number_cells,
            source_dim=len(source_data),
            target_dim=len(target_data),
            batch_size=batch_size,
            account_for_unbalancedness=account_for_unbalancedness,
            interpolation_parameter=interpolation_parameter,
            seed=seed,
        )
        return (
            source_data[np.repeat(rows_sampled, [len(col) for col in cols_sampled]), :] * (1 - interpolation_parameter)
            + target_data[np.hstack(cols_sampled), :] * interpolation_parameter
        )

    def _interpolate_gex_randomly(
        self,
        number_cells: int,
        source_data: ArrayLike,
        target_data: ArrayLike,
        interpolation_parameter: float,
        growth_rates: Optional[ArrayLike] = None,
        seed: Optional[int] = None,
    ) -> ArrayLike:
        rng = np.random.RandomState(seed)
        if growth_rates is None:
            row_probability = np.ones(len(source_data))
        else:
            row_probability = growth_rates ** (1 - interpolation_parameter)
        row_probability /= np.sum(row_probability)
        result = (
            source_data[rng.choice(len(source_data), size=number_cells, p=row_probability), :]
            * (1 - interpolation_parameter)
            + target_data[rng.choice(len(target_data), size=number_cells), :] * interpolation_parameter
        )
        return result

    @staticmethod
    def _get_interp_param(
        start: K, intermediate: K, end: K, interpolation_parameter: Optional[float] = None
    ) -> Numeric_t:
        if TYPE_CHECKING:
            assert isinstance(start, (int, float))
            assert isinstance(intermediate, (int, float))
            assert isinstance(end, (int, float))
        if interpolation_parameter is not None and (0 > interpolation_parameter or interpolation_parameter > 1):
            raise ValueError("TODO: interpolation parameter must be in [0,1].")
        if start >= intermediate:
            raise ValueError("TODO: expected start < intermediate")
        if intermediate >= end:
            raise ValueError("TODO: expected intermediate < end")
        return (
            interpolation_parameter if interpolation_parameter is not None else (intermediate - start) / (end - start)
        )

    @property
    def temporal_key(self) -> Optional[str]:
        """Return temporal key."""
        return self._temporal_key

    @temporal_key.setter
    def temporal_key(self, value: Optional[str] = None) -> None:
        # if not is_numeric_dtype(self.adata.obs[value]):
        #    raise TypeError(f"TODO: column must be of numeric data type")
        self._temporal_key = value