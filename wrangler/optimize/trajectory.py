"""Replay a stopping rule against an archived GEPA run.

**Why this exists.** An optimize stage costs ~11 hours on a flat `max_metric_calls`
budget and `gepa.optimize` is never told to stop early. Choosing a patience by feel
would be a guess; the pipeline already archives `gepa_state.bin` for every arm
(`components.py` uploads GEPA's run_dir), and that file carries every candidate with
its validation subscores. So the question "what would patience *p* have cost us?" is
answerable against our own runs, for free, with no new spend.

**The replay drives the real stopper, it does not imitate it.** `gepa_state.bin` is a
plain `dict` of `GEPAState.__dict__` (gepa's `save()` serializes everything except
`_budget_hooks`), so the state rehydrates with `object.__new__` and a `__dict__`
update. `program_full_scores_val_set` is a *property* over
`prog_candidate_val_subscores`, which means the score list the stopper reads is
recomputed by gepa's own code rather than reconstructed by ours. A replay that
reimplemented the stopper's notion of "improvement" would be worthless the moment the
two definitions drifted; this one cannot drift, because there is only one definition.

**Fidelity detail that decides the answer by one iteration.** gepa polls the stopper at
the *top* of the loop (`engine.py`: `while not self._should_stop(state)`), so at the
start of iteration `i` the state holds only candidates discovered in iterations
strictly before `i`. Candidates are appended, so candidate index order is discovery
order and truncating the score list to a prefix reproduces the state exactly.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Patience values the replay reports on. Spans "aggressive" to "barely ever fires"
#: so the tradeoff curve is visible rather than a single opinion.
DEFAULT_PATIENCES = (3, 5, 8, 10, 15)


@dataclass
class Trajectory:
    """One archived optimize run, reduced to what a stopping rule reads.

    Small and JSON-native on purpose: committing this rather than the multi-megabyte
    pickle keeps the analysis reproducible without GCS, and outlives the pickle. Same
    reasoning as the canary, which is JSON for the same reason.
    """

    arm: str
    #: Validation aggregate score per candidate, in discovery order.
    scores: list[float]
    #: Candidate index -> the iteration during which it was discovered.
    discovered_at: dict[int, int]
    #: Metric calls consumed at each candidate's discovery.
    calls_at_discovery: list[int]
    #: Total iterations the run actually performed.
    total_iterations: int
    #: Total metric calls the run actually consumed.
    total_calls: int
    #: Number of cases in the validation subset the scores are averaged over.
    val_subset_size: int = 0

    @property
    def best_idx(self) -> int:
        """The candidate GEPA would return.

        Mirrors `gepa.core.result.best_idx`, which is
        `max(range(len(scores)), key=scores.__getitem__)` — and Python's `max` returns
        the **first** maximal element. So on a tie the earliest candidate wins, which
        is the fact that makes early stopping lossless here rather than merely cheap.
        """
        return max(range(len(self.scores)), key=lambda i: self.scores[i])

    def candidates_before(self, iteration: int) -> int:
        """How many candidates exist at the top of `iteration`.

        The seed (candidate 0) is always present; every other candidate counts once
        the iteration that produced it has finished.
        """
        return 1 + sum(1 for c, i in self.discovered_at.items() if c != 0 and i < iteration)


@dataclass
class StopPoint:
    """What a given patience would have done to one run."""

    patience: int
    #: Iteration the stopper fired at, or None if it never fired.
    stop_iteration: int | None
    candidates_at_stop: int
    calls_at_stop: int
    best_idx_at_stop: int
    best_score_at_stop: float
    #: True when stopping returns the same prompt the full run returned.
    same_candidate: bool = field(default=False)

    @property
    def fired(self) -> bool:
        return self.stop_iteration is not None


def load_state(path: str | Path) -> dict[str, Any]:
    """Unpickle an archived `gepa_state.bin`.

    Deliberately not wrapped in a try/except: a state that will not load is the one
    fact the caller most needs to hear, and swallowing it would silently turn a broken
    archive into an empty result.
    """
    with open(path, "rb") as f:
        state = pickle.load(f)  # noqa: S301 - our own artifact from our own pipeline
    if not isinstance(state, dict):
        raise TypeError(f"expected gepa's serialized dict, got {type(state).__name__}")
    return state


def _rehydrate(state: dict[str, Any]):
    """Rebuild a real `GEPAState` from the serialized dict, bypassing `__init__`.

    `__init__` wants a seed candidate and a base evaluation and would re-run setup we
    do not want; the saved dict already *is* the instance state.
    """
    from gepa.core.state import GEPAState

    g = object.__new__(GEPAState)
    g.__dict__.update(state)
    return g


def val_scores(state: dict[str, Any]) -> list[float]:
    """Per-candidate validation aggregate scores, computed by gepa's own property."""
    return list(_rehydrate(state).program_full_scores_val_set)


def discovery_iterations(state: dict[str, Any]) -> dict[int, int]:
    """Candidate index -> iteration it was discovered in.

    Read from `full_program_trace`, whose entries carry `i` and `new_program_idx`.
    Candidate 0 is the seed and predates the loop, so it is pinned to iteration 0.
    """
    found = {0: 0}
    for entry in state.get("full_program_trace", []):
        if isinstance(entry, dict) and entry.get("new_program_idx") is not None:
            found[int(entry["new_program_idx"])] = int(entry["i"])
    return found


def to_trajectory(state: dict[str, Any], arm: str = "") -> Trajectory:
    """Reduce an archived state to the fields a stopping rule reads."""
    subscores = state.get("prog_candidate_val_subscores") or []
    return Trajectory(
        arm=arm,
        scores=val_scores(state),
        discovered_at=discovery_iterations(state),
        calls_at_discovery=list(state.get("num_metric_calls_by_discovery") or []),
        total_iterations=int(state.get("i", 0)),
        total_calls=int(state.get("total_num_evals", 0)),
        val_subset_size=len(subscores[0]) if subscores else 0,
    )


def save_trajectory(traj: Trajectory, path: str | Path) -> str:
    """Write a trajectory as JSON.

    JSON rather than the pickle for the same reason the canary is JSON: the artifact
    whose whole job is comparison across time has to outlive an SDK bump, and
    `gepa_state.bin` is a pickle of gepa's internals. 12 KB for both c09 arms against
    2.2 MB of state.
    """
    import dataclasses
    import json

    payload = dataclasses.asdict(traj)
    payload["discovered_at"] = {str(k): v for k, v in payload["discovered_at"].items()}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    return str(path)


def load_trajectory(path: str | Path) -> Trajectory:
    """Read a trajectory written by `save_trajectory`."""
    import json

    with open(path) as f:
        payload = json.load(f)
    payload["discovered_at"] = {int(k): v for k, v in payload["discovered_at"].items()}
    return Trajectory(**payload)


def replay(traj: Trajectory, patience: int) -> StopPoint:
    """Run gepa's real `NoImprovementStopper` over an archived trajectory.

    Reconstructs the state the stopper would have seen at the top of each iteration by
    truncating the score list to the candidates that existed then, and calls the
    genuine stopper object so the improvement predicate is gepa's, not ours.
    """
    from gepa.utils.stop_condition import NoImprovementStopper

    stopper = NoImprovementStopper(patience)
    final_best = traj.best_idx

    class _View:
        """Minimal stand-in exposing the one attribute the stopper reads."""

        def __init__(self, scores: list[float]):
            self.program_full_scores_val_set = scores

    for iteration in range(traj.total_iterations + 1):
        k = traj.candidates_before(iteration)
        # Duck-typing is the point: the stopper reads exactly one attribute, and a real
        # GEPAState would need the pickle this function deliberately does not require.
        # `test_view_feeds_the_real_stopper_the_attribute_it_reads` pins that the stand-in
        # is accepted -- necessary, because the stopper swallows an AttributeError and
        # would otherwise report "never fires" for every patience.
        if stopper(_View(traj.scores[:k])):  # ty: ignore[invalid-argument-type]
            visible = traj.scores[:k]
            best = max(range(len(visible)), key=lambda i: visible[i])
            return StopPoint(
                patience=patience,
                stop_iteration=iteration,
                candidates_at_stop=k,
                calls_at_stop=_calls_for(traj, k),
                best_idx_at_stop=best,
                best_score_at_stop=visible[best],
                same_candidate=best == final_best,
            )

    return StopPoint(
        patience=patience,
        stop_iteration=None,
        candidates_at_stop=len(traj.scores),
        calls_at_stop=traj.total_calls,
        best_idx_at_stop=final_best,
        best_score_at_stop=traj.scores[final_best],
        same_candidate=True,
    )


def _calls_for(traj: Trajectory, n_candidates: int) -> int:
    """Metric calls consumed once `n_candidates` exist.

    Falls back to the run total when the discovery list is shorter than the candidate
    list, which happens if a run was cut off mid-write.
    """
    idx = n_candidates - 1
    if 0 <= idx < len(traj.calls_at_discovery):
        return traj.calls_at_discovery[idx]
    return traj.total_calls
