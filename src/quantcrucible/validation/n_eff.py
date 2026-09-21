"""N_eff — the number of effectively independent trials (Architecture §4.1, ADR-0009).

Trials are clustered by the correlation of their IS return series with López de Prado's ONC
("optimal number of clusters": k-means on the correlation-distance matrix, k chosen by the
silhouette t-statistic, weak clusters re-clustered recursively). Each run is appended to
``clustering_runs`` / ``trial_clusters``; the ledger's ``trial_stats`` view then counts the
clusters of the latest run, plus one per trial recorded since.

``N_eff`` may only lower the bar on principled grounds (§4.1): where ONC cannot run (fewer than
three trials) every trial stays its own cluster, and after ONC a trial stays in a cluster only if
its mean correlation with the other members is statistically significant
(:func:`significant_members`) — k-means alone would put even an unrelated trial somewhere.

Reference: López de Prado & Lewis (2019), "Detection of false investment strategies using
unsupervised learning methods", Quantitative Finance 19(9); López de Prado (2020), *Machine
Learning for Asset Managers*, ch. 4.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_samples

from quantcrucible.ledger.db import Ledger

ONC_METHOD = "onc-v1"
MIN_OVERLAP = 30  # fewer common observations than this ⇒ correlation treated as 0
SIGNIFICANCE_Z = 3.0  # cluster membership needs mean ρ > z/√T (ADR-0009)
MAX_CLUSTERS = 50  # k-means search bound per ONC level; the guard can only split further
N_INIT = 3

Labels = npt.NDArray[np.int64]
FloatMatrix = npt.NDArray[np.float64]


def correlation_matrix(series: Sequence[pd.Series]) -> tuple[FloatMatrix, FloatMatrix]:
    """Pairwise correlation of return series aligned on their timestamps, and the number of
    common observations behind each entry.

    Pairs with fewer than ``MIN_OVERLAP`` common observations, and flat series (a strategy that
    never traded), get correlation 0 — treated as unrelated, which never lowers ``N_eff``.
    """
    frame = pd.concat(list(series), axis=1, join="outer")
    frame.columns = list(range(len(series)))
    corr = frame.corr(min_periods=MIN_OVERLAP).to_numpy(dtype=np.float64)
    corr = np.nan_to_num(corr, nan=0.0)
    np.fill_diagonal(corr, 1.0)
    present = frame.notna().to_numpy(dtype=np.float64)
    overlap = present.T @ present
    return np.clip((corr + corr.T) / 2, -1.0, 1.0), overlap


def significant_members(
    labels: Labels, corr: FloatMatrix, n_obs: FloatMatrix, z: float = SIGNIFICANCE_Z
) -> Labels:
    """Conservative guard on top of ONC (ADR-0009): k-means puts every trial in *some* cluster,
    even an unrelated one. A trial stays in its cluster only while its mean correlation with the
    other members exceeds ``z / √T`` (T = mean common observations); otherwise it becomes its
    own cluster. Weakest member first, repeated until stable."""
    out = labels.copy()
    next_label = int(out.max()) + 1 if len(out) else 0
    changed = True
    while changed:
        changed = False
        for c in sorted(set(out.tolist())):
            idx = np.flatnonzero(out == c)
            if len(idx) < 2:
                continue
            sub = corr[np.ix_(idx, idx)]
            obs = n_obs[np.ix_(idx, idx)]
            mean_rho = (sub.sum(axis=1) - 1.0) / (len(idx) - 1)
            mean_obs = (obs.sum(axis=1) - np.diag(obs)) / (len(idx) - 1)
            threshold = z / np.sqrt(np.maximum(mean_obs, 1.0))
            margin = mean_rho - threshold
            weakest = int(np.argmin(margin))
            if margin[weakest] <= 0:
                out[idx[weakest]] = next_label
                next_label += 1
                changed = True
    return _relabel(out)


def _distance(corr: FloatMatrix) -> FloatMatrix:
    return np.sqrt(np.clip((1.0 - corr) / 2.0, 0.0, None))


def _quality(silh: npt.NDArray[np.float64]) -> float:
    std = float(silh.std())
    return float(silh.mean()) / std if std > 0 else -np.inf


def _base(corr: FloatMatrix, max_k: int, n_init: int, seed: int) -> tuple[Labels, FloatMatrix]:
    """Best k-means partition of the distance matrix over k = 2..max_k by silhouette t-stat."""
    x = _distance(corr)
    best_labels: Labels | None = None
    best_silh = np.zeros(len(corr))
    best_q = -np.inf
    for init in range(n_init):
        for k in range(2, max_k + 1):
            km = KMeans(n_clusters=k, n_init=1, random_state=seed + init * 1000 + k)
            labels = np.asarray(km.fit_predict(x), dtype=np.int64)
            if len(set(labels.tolist())) < 2:
                continue
            silh = np.asarray(silhouette_samples(x, labels), dtype=np.float64)
            q = _quality(silh)
            if best_labels is None or q > best_q:
                best_labels, best_silh, best_q = labels, silh, q
    if best_labels is None:
        return np.zeros(len(corr), dtype=np.int64), best_silh
    return best_labels, best_silh


def _cluster_tstats(labels: Labels, silh: FloatMatrix) -> dict[int, float]:
    out: dict[int, float] = {}
    for c in sorted(set(labels.tolist())):
        s = silh[labels == c]
        std = float(s.std())
        out[c] = float(s.mean()) / std if std > 0 else float(s.mean()) * 1e6  # singleton ⇒ ±big
    return out


def _relabel(labels: Labels) -> Labels:
    _, inverse = np.unique(labels, return_inverse=True)
    return np.asarray(inverse, dtype=np.int64)


def _top(corr: FloatMatrix, max_k: int, n_init: int, seed: int) -> Labels:
    n = len(corr)
    if n < 3:
        return np.arange(n, dtype=np.int64)  # ONC cannot run: every trial its own cluster
    if float(_distance(corr).max()) < 1e-9:
        return np.zeros(n, dtype=np.int64)  # identical series
    labels, silh = _base(corr, min(max_k, n - 1), n_init, seed)
    tstats = _cluster_tstats(labels, silh)
    mean_t = float(np.mean(list(tstats.values())))
    redo = [c for c, t in tstats.items() if t < mean_t]
    if len(redo) <= 1:
        return _relabel(labels)
    idx = np.flatnonzero(np.isin(labels, redo))
    sub = _top(corr[np.ix_(idx, idx)], max_k, n_init, seed)
    new = labels.copy()
    new[idx] = labels.max() + 1 + sub
    new = _relabel(new)
    new_silh = np.asarray(silhouette_samples(_distance(corr), new), dtype=np.float64)
    redo_mean_old = float(np.mean([tstats[c] for c in redo]))
    new_t = _cluster_tstats(new, new_silh)
    redo_mean_new = float(np.mean([new_t[c] for c in set(new[idx].tolist())]))
    return new if redo_mean_new > redo_mean_old else _relabel(labels)


def onc_clusters(
    corr: npt.ArrayLike,
    n_obs: float | npt.ArrayLike,
    max_clusters: int | None = MAX_CLUSTERS,
    n_init: int = N_INIT,
    seed: int = 0,
) -> Labels:
    """Cluster labels (0..k-1) for a correlation matrix: ONC, then the significance guard.
    ``n_obs`` is the number of observations behind each correlation (scalar or matrix).
    Deterministic for a seed."""
    c = np.asarray(corr, dtype=np.float64)
    if c.ndim != 2 or c.shape[0] != c.shape[1]:
        raise ValueError("corr must be a square matrix")
    obs = np.broadcast_to(np.asarray(n_obs, dtype=np.float64), c.shape)
    max_k = max_clusters if max_clusters is not None else len(c) - 1
    return significant_members(_top(c, max_k, n_init, seed), c, obs)


@dataclass(frozen=True, slots=True)
class ClusteringResult:
    run_id: int
    method: str
    n_trials: int
    n_clusters: int


def _load_returns(path: str) -> pd.Series:
    df = pd.read_parquet(path, columns=["ts", "ret"])
    return pd.Series(df["ret"].to_numpy(dtype=np.float64), index=pd.to_datetime(df["ts"]))


def update_n_eff(ledger: Ledger, seed: int = 0, n_init: int = N_INIT) -> ClusteringResult:
    """Cluster every trial in the ledger (all campaigns) and append the run (§4.1)."""
    trials = ledger.trials()
    if not trials:
        raise ValueError("no trials to cluster")
    corr, n_obs = correlation_matrix([_load_returns(t.returns_path) for t in trials])
    labels = onc_clusters(corr, n_obs, n_init=n_init, seed=seed)
    run = ledger.record_clustering(
        ONC_METHOD, {t.id: int(label) for t, label in zip(trials, labels, strict=True)}
    )
    return ClusteringResult(run, ONC_METHOD, len(trials), len(set(labels.tolist())))
