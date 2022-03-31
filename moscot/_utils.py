from typing import Any, Optional

from numpy.typing import ArrayLike
from ott.geometry.costs import Bures, Cosine, Euclidean, UnbalancedBures
import numpy as np

from anndata import AnnData

# TODO(michalk8): improve:
# 1. _get_marginal should do more, unify with BaseProblem._getMass
# 2. normalize doesn't need a specific function
# 3. backend losses shouln't exist here (already does in backend)


def _get_marginal(adata: AnnData, attr: Optional[str] = None, key: Optional[str] = None) -> ArrayLike:
    if attr is None:
        return np.ones(adata.n_obs) / adata.n_obs

    if not hasattr(adata, attr):
        raise AttributeError("TODO: invalid attribute")
    container = getattr(adata, attr)

    if key is None:
        return np.array(_normalize(container))
    if key not in container:
        raise KeyError(f"TODO: unable to find `adata.{attr}['{key}']`.")
    return _normalize(np.array(container[key]))


def _normalize(arr: ArrayLike) -> ArrayLike:
    if arr.ndim != 1:
        raise ValueError("TODO: expected 1D")
    return arr / np.sum(arr)


def _get_backend_losses(
    backend: str = "JAX", **kwargs: Any
):  # TODO(@MUCDK, @michalk8), registry or put somewhere else.
    if backend == "JAX":
        dimension = kwargs.pop("dimension", 1)
        return {
            "Euclidean": Euclidean(**kwargs),
            "Cosine": Cosine(**kwargs),
            "Bures": Bures(dimension, **kwargs),
            "UnbalancedBures": UnbalancedBures(dimension, **kwargs),
        }
    else:
        raise NotImplementedError()