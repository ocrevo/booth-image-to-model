"""Aggregates with bootstrap confidence intervals. numpy only.

Rows of one scene (the same case under several perturbations) are not independent, so the bootstrap resamples whole scenes
(clusters, keyed by case_id) and keeps every row of a chosen scene. Inside one perturbation group each scene appears once.
"""
from __future__ import annotations
import numpy as np

DEFAULT_RESAMPLES = 1000
BOOTSTRAP_SEED = 20261019


def _stat(kind, data):
    if kind == 'ratio':
        den = data[:, 1].sum()
        return float(data[:, 0].sum() / den) if den > 0 else None
    if data.size == 0:
        return None
    if kind == 'mean':
        return float(np.mean(data))
    if kind == 'median':
        return float(np.median(data))
    if kind == 'p90':
        return float(np.percentile(data, 90))
    if kind == 'rate':
        return float(np.mean(data))
    raise ValueError(f'unknown statistic {kind!r}')


def summarize(rows, extract, kind, resamples=DEFAULT_RESAMPLES, seed=BOOTSTRAP_SEED, level=0.95):
    """Point value and percentile bootstrap interval of one statistic.

    extract(row) returns None (row does not count), a number (mean, median, p90, rate) or a (numerator, denominator) pair (ratio).
    Returns {'n', 'value', 'lo', 'hi'}; value is None when no row counts, lo/hi are None with fewer than two scenes."""
    clusters = {}
    for r in rows:
        v = extract(r)
        if v is None:
            continue
        if kind == 'ratio':
            clusters.setdefault(r['case_id'], []).append([float(v[0]), float(v[1])])
        else:
            clusters.setdefault(r['case_id'], []).append(float(v))
    ids = sorted(clusters)
    if not ids:
        return {'n': 0, 'value': None, 'lo': None, 'hi': None}
    arrays = [np.asarray(clusters[i], float) for i in ids]
    if kind == 'ratio':
        arrays = [a.reshape(-1, 2) for a in arrays]
    full = np.concatenate(arrays)
    value = _stat(kind, full)
    n = int(len(full))
    if len(ids) < 2 or value is None:
        return {'n': n, 'value': value, 'lo': None, 'hi': None}
    rng = np.random.default_rng(seed)
    reps = []
    for _ in range(resamples):
        pick = rng.integers(0, len(ids), len(ids))
        s = _stat(kind, np.concatenate([arrays[j] for j in pick]))
        if s is not None:
            reps.append(s)
    if not reps:
        return {'n': n, 'value': value, 'lo': None, 'hi': None}
    tail = (1.0 - level) / 2.0 * 100.0
    lo, hi = np.percentile(reps, [tail, 100.0 - tail])
    return {'n': n, 'value': value, 'lo': float(lo), 'hi': float(hi)}


def _clusters(rows, extract, kind):
    out = {}
    for r in rows:
        v = extract(r)
        if v is None:
            continue
        out.setdefault(r['case_id'], []).append([float(v[0]), float(v[1])] if kind == 'ratio' else float(v))
    return {k: np.asarray(v, float).reshape(-1, 2) if kind == 'ratio' else np.asarray(v, float) for k, v in out.items()}


def paired_difference(rows_a, rows_b, extract, kind, resamples=DEFAULT_RESAMPLES, seed=BOOTSTRAP_SEED, level=0.95):
    """Statistic of B minus statistic of A over the scenes both sides scored, with a paired scene-level bootstrap interval.

    rows_a and rows_b are the rows of the two sides (already filtered, for example to one perturbation each); scenes are matched
    by case_id. -> {'n_scenes', 'a', 'b', 'delta', 'lo', 'hi'} (None where there is nothing to compare)."""
    ca, cb = _clusters(rows_a, extract, kind), _clusters(rows_b, extract, kind)
    ids = sorted(set(ca) & set(cb))
    empty = {'n_scenes': len(ids), 'a': None, 'b': None, 'delta': None, 'lo': None, 'hi': None}
    if not ids:
        return empty
    cat = lambda side, pick: np.concatenate([side[ids[j]] for j in pick])
    a, b = _stat(kind, cat(ca, range(len(ids)))), _stat(kind, cat(cb, range(len(ids))))
    if a is None or b is None:
        return empty
    out = {'n_scenes': len(ids), 'a': a, 'b': b, 'delta': b - a, 'lo': None, 'hi': None}
    if len(ids) < 2:
        return out
    rng = np.random.default_rng(seed)
    reps = []
    for _ in range(resamples):
        pick = rng.integers(0, len(ids), len(ids))
        sa, sb = _stat(kind, cat(ca, pick)), _stat(kind, cat(cb, pick))
        if sa is not None and sb is not None:
            reps.append(sb - sa)
    if reps:
        tail = (1.0 - level) / 2.0 * 100.0
        out['lo'], out['hi'] = (float(x) for x in np.percentile(reps, [tail, 100.0 - tail]))
    return out
