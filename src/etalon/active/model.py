"""Small-data, multi-endpoint Gaussian surrogate trained only on admitted observations.

Each observable keeps its own units, mean and scale. Cross-endpoint transfer is learned from
paired molecules, with shrinkage towards independence. The resulting task covariance is PSD by
construction. This is an empirical-Bayes working model, NOT a calibrated confidence guarantee,
and deliberately has a size limit rather than silently subsampling historical labels.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from etalon.active.schema import Candidate, Endpoint, digest, finite


class MultiEndpointGP:
    version = "paired-task-gp/4"

    def __init__(self, candidates: Mapping[str, Candidate], endpoints: Mapping[str, Endpoint],
                 objective: str, observations: Sequence[dict[str, Any]], *, limit: int = 1500) -> None:
        import numpy as np
        from scipy.linalg import cho_solve

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("the surrogate observation limit must be a positive integer")
        if objective not in endpoints:
            raise ValueError("the surrogate objective must be a registered endpoint")
        if any(key != row.id for key, row in candidates.items()):
            raise ValueError("candidate mapping keys must match candidate identities")
        if any(key != row.id for key, row in endpoints.items()):
            raise ValueError("endpoint mapping keys must match endpoint identities")
        if len({row.target for row in endpoints.values()}) != 1:
            raise ValueError("the surrogate models one campaign target")
        self.candidates = dict(candidates)
        self.endpoints = dict(endpoints)
        self.objective = objective
        self.ids = sorted(candidates)
        if not self.ids:
            raise ValueError("the surrogate needs a nonempty candidate pool")
        self.index = {identifier: i for i, identifier in enumerate(self.ids)}
        self.tasks = [objective, *sorted(set(endpoints) - {objective})]
        self.task_index = {identifier: i for i, identifier in enumerate(self.tasks)}
        # Unlabelled pool features are allowed (a transductive design); unseen outcomes are not.
        # Finite float64 features can overflow float64 variance and silently collapse
        # distinct molecules to the same zero vector. Extended intermediates retain the
        # existing standardization convention, with an explicit failure if unsupported.
        raw = np.asarray([candidates[key].features for key in self.ids], dtype=np.longdouble)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            center, spread = raw.mean(axis=0), raw.std(axis=0)
            normalized = (raw - center) / np.maximum(spread, 1e-8)
        if (not np.all(np.isfinite(center)) or not np.all(np.isfinite(spread))
                or not np.all(np.isfinite(normalized))):
            raise ValueError("candidate standardization exceeds numerical range; rescale the representation")
        self.x = np.asarray(normalized, dtype=float)
        sample = self.x[np.linspace(0, len(self.ids) - 1, min(len(self.ids), 128), dtype=int)]
        distances = self._distance(sample, sample)
        positive = distances[distances > 1e-12]
        self.lengthscale2 = float(np.median(positive)) if len(positive) else 1.0
        if any(type(o.get("admitted")) is not bool for o in observations):
            raise ValueError("observation admission must be an explicit boolean")
        self.training = [deepcopy(o["result"]) for o in observations if o["admitted"]]
        self.training_action_ids = [o["action_id"] for o in observations
                                    if o["admitted"] and "action_id" in o]
        if (any(not isinstance(key, str) or not key for key in self.training_action_ids)
                or len(set(self.training_action_ids)) != len(self.training_action_ids)):
            raise ValueError("admitted action identities must be nonempty and unique; duplicates are not replicates")
        if len(self.training) > limit:
            raise ValueError(f"exact GP limit is {limit} admitted observations; use a scalable backend")
        grouped: dict[str, list[float]] = defaultdict(list)
        paired: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for row in self.training:
            identifier, task = row["candidate_id"], row["endpoint_id"]
            if identifier not in candidates or task not in endpoints or row["value"] is None:
                raise ValueError("training observation has no registered subject, endpoint or value")
            if row["units"] != endpoints[task].units:
                raise ValueError("training observation has invalid units")
            if row.get("status") != "ok":
                raise ValueError("an admitted training observation must have status ok")
            finite(row["value"], "training value")
            if row.get("uncertainty") is not None:
                finite(row["uncertainty"], "training uncertainty", minimum=0)
            grouped[task].append(row["value"])
            paired[task][identifier].append(row["value"])
        self.means = np.asarray([float(np.mean(grouped[t], dtype=np.longdouble)) if grouped[t] else endpoints[t].prior_mean
                                 for t in self.tasks])
        self.scales = np.asarray([max(float(np.std(grouped[t], dtype=np.longdouble)), endpoints[t].noise)
                                  if len(grouped[t]) >= 2 else endpoints[t].prior_scale
                                  for t in self.tasks])
        if not np.all(np.isfinite(self.means)) or not np.all(np.isfinite(self.scales)):
            raise ValueError("endpoint moments exceed numerical range; rescale endpoint units")
        loadings = np.zeros(len(self.tasks))
        loadings[0] = 1.0
        self.pair_counts = {}
        for i, task in enumerate(self.tasks[1:], 1):
            shared = sorted(set(paired[objective]) & set(paired[task]))
            self.pair_counts[task] = len(shared)
            if len(shared) >= 3:
                # Test degeneracy in standardized units: an absolute raw-unit threshold
                # would erase correlation simply by changing e.g. molar to a smaller unit.
                high = (np.asarray([np.mean(paired[objective][key], dtype=np.longdouble) for key in shared]) - self.means[0]) / self.scales[0]
                low = (np.asarray([np.mean(paired[task][key], dtype=np.longdouble) for key in shared]) - self.means[i]) / self.scales[i]
                if min(float(high.std()), float(low.std())) > 1e-12:
                    loadings[i] = float(np.corrcoef(high, low)[0, 1]) * len(shared) / (len(shared) + 3)
        self.task_cov = np.outer(loadings, loadings) + np.diag(1 - loadings**2)
        self.train_x = np.asarray([self.x[self.index[o["candidate_id"]]] for o in self.training])
        self.train_t = np.asarray([self.task_index[o["endpoint_id"]] for o in self.training], dtype=int)
        self.counts = {task: len(grouped[task]) for task in self.tasks}
        self.fingerprint = digest({"model": self.version, "endpoints": [endpoints[t].as_dict() for t in self.tasks],
                                   "pool": [(key, candidates[key].features) for key in self.ids],
                                   "training": self.training})
        self.cholesky = None
        self.alpha = None
        if self.training:
            covariance = self._kernel(self.train_x, self.train_t, self.train_x, self.train_t)
            noise = np.asarray([max(endpoints[o["endpoint_id"]].noise, o.get("uncertainty") or 0.0)
                                / self.scales[t] for o, t in zip(self.training, self.train_t, strict=True)])
            covariance[np.diag_indices_from(covariance)] += noise**2 + 1e-8
            self.cholesky = np.linalg.cholesky(covariance)
            y = np.asarray((np.asarray([o["value"] for o in self.training], dtype=np.longdouble)
                            - self.means[self.train_t]) / self.scales[self.train_t], dtype=float)
            self.alpha = cho_solve((self.cholesky, True), y)

    @staticmethod
    def _distance(a: Any, b: Any) -> Any:
        import numpy as np

        return np.maximum((np.sum(a*a, axis=1)[:, None] + np.sum(b*b, axis=1)[None, :]
                           - 2 * a @ b.T) / a.shape[1], 0.0)

    def _kernel(self, a: Any, ta: Any, b: Any, tb: Any) -> Any:
        import numpy as np

        return np.exp(-0.5 * self._distance(a, b) / self.lengthscale2) * self.task_cov[ta[:, None], tb[None, :]]

    @staticmethod
    def _physical_values(values: Any, name: str) -> Any:
        """Do not silently return NaN/inf or erase nonzero physical variances to zero."""
        import numpy as np

        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            converted = np.asarray(values, dtype=float)
        if not np.all(np.isfinite(converted)) or np.any((values != 0) & (converted == 0)):
            raise ValueError(f"{name} exceeds numerical range; rescale endpoint units")
        return converted

    def _query(self, ids: Sequence[str], endpoint: str) -> tuple[Any, Any, Any]:
        import numpy as np
        from scipy.linalg import solve_triangular

        x = self.x[[self.index[key] for key in ids]]
        tasks = np.full(len(ids), self.task_index[endpoint], dtype=int)
        solved = np.empty((0, len(ids)))
        if self.cholesky is not None:
            cross = self._kernel(self.train_x, self.train_t, x, tasks)
            solved = solve_triangular(self.cholesky, cross, lower=True)
        return x, tasks, solved

    def predict(self, ids: Sequence[str], endpoint: str, *, batch_size: int = 256) -> tuple[Any, Any]:
        """Posterior moments in original units, with bounded query-matrix allocation.

        Chunking does not subsample candidates or training labels. Exact GP training
        and global covariance requests retain their existing size/complexity limits.
        """
        import numpy as np

        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("prediction batch_size must be a positive integer")
        if len(ids) > batch_size:
            pieces = [self.predict(ids[start:start + batch_size], endpoint, batch_size=batch_size)
                      for start in range(0, len(ids), batch_size)]
            return tuple(np.concatenate([part[i] for part in pieces]) for i in range(2))
        x, tasks, solved = self._query(ids, endpoint)
        mean = self.means[tasks].copy()
        if self.alpha is not None:
            mean += self._kernel(x, tasks, self.train_x, self.train_t) @ self.alpha * self.scales[tasks]
        variance = np.maximum(1.0 - np.sum(solved**2, axis=0), 1e-12)
        sd = np.sqrt(variance) * self.scales[tasks]
        if not np.all(np.isfinite(mean)) or not np.all(np.isfinite(sd)) or np.any(sd <= 0):
            raise ValueError("posterior moments exceed numerical range; rescale endpoint units")
        return mean, sd

    def posterior_covariance(self, left_ids: Sequence[str], left_endpoint: str,
                             right_ids: Sequence[str], right_endpoint: str) -> Any:
        """Latent cross-covariance, in the product of the two endpoints' original units.

        Observation noise is NOT included. Callers valuing a future observation must add its
        noise variance to that observation's denominator. This computes the complete requested
        rectangle; callers control its size and may chunk either argument without changing it.
        """
        import numpy as np

        left, left_tasks, left_solved = self._query(left_ids, left_endpoint)
        right, right_tasks, right_solved = self._query(right_ids, right_endpoint)
        covariance = self._kernel(left, left_tasks, right, right_tasks) - left_solved.T @ right_solved
        if left_endpoint == right_endpoint:
            # Match predict()'s numerical latent-variance floor, including repeated query ids.
            identical = np.equal.outer(list(left_ids), list(right_ids))
            covariance = np.where(identical, np.maximum(covariance, 1e-12), covariance)
        # Multiplication in physical units may overflow even though the standardized
        # covariance is well behaved. Reject unrepresentable outputs explicitly.
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            physical = (covariance.astype(np.longdouble) * self.scales[left_tasks, None].astype(np.longdouble)
                        * self.scales[right_tasks][None, :].astype(np.longdouble))
        return self._physical_values(physical, "posterior covariance")

    def objective_reduction(self, ids: Sequence[str], endpoint: str, *, batch_size: int = 256) -> Any:
        """Expected objective variance reduction from one observation on the SAME molecule.

        A local information proxy, not a full knowledge-gradient or global optimum. Unknown
        cross-task correlation gives zero transfer until paired calibration data exist.
        """
        import numpy as np

        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("prediction batch_size must be a positive integer")
        if len(ids) > batch_size:
            return np.concatenate([self.objective_reduction(ids[start:start + batch_size], endpoint,
                                                           batch_size=batch_size)
                                   for start in range(0, len(ids), batch_size)])
        _, _, high = self._query(ids, self.objective)
        _, tasks, low = self._query(ids, endpoint)
        task = self.task_index[endpoint]
        with np.errstate(over="ignore", invalid="ignore", divide="ignore", under="ignore"):
            scales = self.scales.astype(np.longdouble)
            covariance = (self.task_cov[0, task] - np.sum(high * low, axis=0)).astype(np.longdouble) * scales[0] * scales[task]
            variance = np.maximum(1 - np.sum(low**2, axis=0), 1e-12).astype(np.longdouble) * scales[tasks]**2
            reduction = covariance**2 / (variance + np.longdouble(self.endpoints[endpoint].noise)**2)
        return self._physical_values(reduction, "objective variance reduction")

    def snapshot(self) -> dict[str, Any]:
        return {"version": self.version, "training_hash": self.fingerprint, "training_size": len(self.training),
                "counts": dict(self.counts), "paired_molecules": dict(self.pair_counts),
                "objective_correlations": {task: float(self.task_cov[0, i]) for i, task in enumerate(self.tasks)},
                "uncertainty": "empirical Bayes posterior; not conformal or frequentist coverage"}
