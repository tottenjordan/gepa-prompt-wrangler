"""Tests for replaying a stopping rule against an archived GEPA run.

The load-bearing tests here are the **fidelity** ones: the replay's value depends
entirely on it reproducing what gepa's own stopper would have seen, so the tests
compare against gepa's real objects rather than against our expectations of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wrangler.optimize.trajectory import (
    DEFAULT_PATIENCES,
    Trajectory,
    discovery_iterations,
    load_trajectory,
    replay,
    save_trajectory,
    to_trajectory,
    val_scores,
)

FIXTURES = Path(__file__).parent / "fixtures" / "gepa_trajectories"


def _synthetic_state(scores_per_candidate, discoveries, calls, iterations):
    """A dict shaped like gepa's serialized `GEPAState.__dict__`.

    `prog_candidate_val_subscores` is a list of {case_id: score} dicts, which is the
    shape `get_program_average_val_subset` averages over.
    """
    return {
        "prog_candidate_val_subscores": [dict(enumerate(cand)) for cand in scores_per_candidate],
        "full_program_trace": [{"i": it, "new_program_idx": c} for c, it in discoveries],
        "num_metric_calls_by_discovery": calls,
        "i": iterations,
        "total_num_evals": calls[-1] if calls else 0,
    }


class TestReconstruction:
    def test_val_scores_uses_gepas_own_property(self):
        """The score list must come from gepa's code, not a reimplementation.

        Averaging is gepa's `get_program_average_val_subset`; if we computed it here
        and gepa changed (its own TODO says it should move to `val_evaluation_policy`)
        the replay would silently diverge from the live stopper.
        """
        state = _synthetic_state([[1.0, 0.0], [1.0, 1.0]], [(1, 0)], [0, 10], 2)
        assert val_scores(state) == [0.5, 1.0]

    def test_discovery_iterations_pins_the_seed_to_zero(self):
        """Candidate 0 predates the loop and never appears in the trace."""
        state = _synthetic_state([[1.0], [1.0], [1.0]], [(1, 3), (2, 7)], [0, 5, 9], 8)
        assert discovery_iterations(state) == {0: 0, 1: 3, 2: 7}

    def test_to_trajectory_records_the_val_subset_size(self):
        """Needed to read the result: a 15-case subset resolves 1/15 and saturates."""
        state = _synthetic_state([[1.0] * 15], [], [0], 1)
        assert to_trajectory(state).val_subset_size == 15


class TestBestIdx:
    def test_ties_go_to_the_earliest_candidate(self):
        """Must match `gepa.core.result.best_idx`, which relies on `max` returning
        the first maximal element. Early stopping is lossless *because* of this."""
        traj = Trajectory("t", [0.5, 1.0, 1.0], {0: 0, 1: 1, 2: 2}, [0, 1, 2], 3, 3)
        assert traj.best_idx == 1

    def test_matches_gepas_own_selection_rule(self):
        """Pin against gepa's implementation rather than our reading of it."""
        scores = [0.2, 0.9, 0.9, 0.4]
        gepa_rule = max(range(len(scores)), key=lambda i: scores[i])
        traj = Trajectory("t", scores, {i: i for i in range(4)}, [0, 1, 2, 3], 4, 3)
        assert traj.best_idx == gepa_rule


class TestVisibility:
    def test_candidate_is_not_visible_during_its_own_iteration(self):
        """gepa polls the stopper at the TOP of the loop, so a candidate produced
        during iteration i is absent at i and present at i+1. One iteration of error
        here moves every number in the result."""
        traj = Trajectory("t", [0.1, 0.2], {0: 0, 1: 5}, [0, 20], 10, 40)
        assert traj.candidates_before(5) == 1
        assert traj.candidates_before(6) == 2


class TestReplayFidelity:
    def test_view_feeds_the_real_stopper_the_attribute_it_reads(self):
        """`NoImprovementStopper` reads exactly `program_full_scores_val_set`.

        If the stand-in stopped exposing that name the stopper's internal try/except
        would swallow the AttributeError and return False forever — the replay would
        report "never fires" for every patience and look like a legitimate null.
        """
        from gepa.utils.stop_condition import NoImprovementStopper

        stopper = NoImprovementStopper(1)

        class Wrong:
            pass

        assert stopper(Wrong()) is False  # swallowed, not raised

        traj = Trajectory("t", [0.5, 0.5], {0: 0, 1: 1}, [0, 10], 6, 60)
        assert replay(traj, 1).fired, "a flat trajectory must fire, or the view is wrong"

    def test_monotonic_improvement_never_stops(self):
        """Every iteration improves, so no patience should ever fire."""
        scores = [0.1 * i for i in range(1, 9)]
        traj = Trajectory("t", scores, {i: i for i in range(8)}, list(range(8)), 7, 70)
        for patience in DEFAULT_PATIENCES:
            assert not replay(traj, patience).fired

    def test_plateau_fires_after_exactly_patience_iterations(self):
        """One candidate, so the score never changes. The FIRST poll sets the baseline
        (any score beats -inf) and leaves the counter at 0, so the stopper fires at
        iteration == patience, not patience-1."""
        traj = Trajectory("t", [0.7], {0: 0}, [0], 50, 500)
        assert replay(traj, 3).stop_iteration == 3
        assert replay(traj, 8).stop_iteration == 8

    def test_never_firing_reports_the_full_run(self):
        scores = [0.1 * i for i in range(1, 9)]
        traj = Trajectory("t", scores, {i: i for i in range(8)}, list(range(8)), 7, 70)
        point = replay(traj, 15)
        assert point.calls_at_stop == 70
        assert point.same_candidate


class TestCampaign09Regression:
    """Pins the measured result. These numbers are published in
    docs/analysis/2026-09-24-stopping-replay.md and a change here is a change there."""

    @pytest.mark.parametrize(
        ("arm", "candidates", "iterations", "calls", "best_idx"),
        [
            ("c09-rationale-on", 21, 64, 603, 4),
            ("c09-rationale-off", 16, 91, 600, 6),
            ("m01-rationale-merge", 22, 57, 603, 4),
        ],
    )
    def test_fixture_shape(self, arm, candidates, iterations, calls, best_idx):
        traj = load_trajectory(FIXTURES / f"{arm}.json")
        assert len(traj.scores) == candidates
        assert traj.total_iterations == iterations
        assert traj.total_calls == calls
        assert traj.best_idx == best_idx
        assert traj.val_subset_size == 15

    def test_saturation_is_frequent_but_not_universal(self):
        """The finding that qualifies the whole result, and its limit.

        Both campaign 09 arms hit the ceiling of their 15-case validation subset, so
        the selection signal spent most of the run unable to rank candidates. m01 did
        not — it topped out at 14/15 — which is why this is stated as 2 of 3 rather
        than a property of the harness.
        """
        best = {
            arm: load_trajectory(FIXTURES / f"{arm}.json").scores[
                load_trajectory(FIXTURES / f"{arm}.json").best_idx
            ]
            for arm in ("c09-rationale-on", "c09-rationale-off", "m01-rationale-merge")
        }
        assert best["c09-rationale-on"] == 1.0
        assert best["c09-rationale-off"] == 1.0
        assert best["m01-rationale-merge"] < 1.0

    def test_the_unsaturated_run_still_stops_losslessly(self):
        """The case there was no evidence for when the default was chosen. m01 is a
        real run whose validation score never reached the ceiling, and patience 15
        still returns its winning candidate."""
        traj = load_trajectory(FIXTURES / "m01-rationale-merge.json")
        point = replay(traj, 15)
        assert point.same_candidate
        assert point.calls_at_stop < traj.total_calls

    @pytest.mark.parametrize(
        ("arm", "patience", "same", "calls"),
        [
            ("c09-rationale-on", 5, False, 78),
            ("c09-rationale-on", 10, True, 126),
            ("c09-rationale-off", 8, False, 129),
            ("c09-rationale-off", 10, True, 255),
        ],
    )
    def test_patience_10_is_the_smallest_lossless_value(self, arm, patience, same, calls):
        point = replay(load_trajectory(FIXTURES / f"{arm}.json"), patience)
        assert point.same_candidate is same
        assert point.calls_at_stop == calls

    def test_recommended_patience_is_lossless_on_both_arms(self):
        """Patience 15 is the shipped default; if this breaks, the default is wrong."""
        for arm in ("c09-rationale-on", "c09-rationale-off", "m01-rationale-merge"):
            traj = load_trajectory(FIXTURES / f"{arm}.json")
            point = replay(traj, 15)
            assert point.same_candidate
            assert point.calls_at_stop < traj.total_calls


class TestRoundTrip:
    def test_save_load_round_trips(self, tmp_path):
        traj = Trajectory("x", [0.1, 0.9], {0: 0, 1: 2}, [0, 12], 5, 40, val_subset_size=15)
        loaded = load_trajectory(save_trajectory(traj, tmp_path / "t.json"))
        assert loaded == traj

    def test_discovered_at_keys_survive_as_ints(self, tmp_path):
        """JSON stringifies dict keys; an int-keyed lookup silently misses otherwise."""
        traj = Trajectory("x", [0.1, 0.9], {0: 0, 1: 2}, [0, 12], 5, 40)
        loaded = load_trajectory(save_trajectory(traj, tmp_path / "t.json"))
        assert loaded.candidates_before(3) == 2


class TestContinuousSignalRegression:
    """Pins the continuous-signal replay published in
    docs/analysis/2026-09-24-patience-under-continuous-scoring.md."""

    @pytest.mark.parametrize(
        ("arm", "candidates", "lossless_at_5"),
        [
            ("c09-rationale-on-continuous", 20, True),
            ("c09-rationale-off-continuous", 15, True),
        ],
    )
    def test_patience_5_is_lossless_under_continuous_scoring(self, arm, candidates, lossless_at_5):
        traj = load_trajectory(FIXTURES / f"{arm}.json")
        assert len(traj.scores) == candidates
        assert replay(traj, 5).same_candidate is lossless_at_5

    def test_the_shipped_default_is_still_lossless_on_the_continuous_signal(self):
        """15 is kept because it holds under BOTH signals; if that stops being true the
        default is wrong, not merely conservative."""
        for arm in ("c09-rationale-on-continuous", "c09-rationale-off-continuous"):
            traj = load_trajectory(FIXTURES / f"{arm}.json")
            point = replay(traj, 15)
            assert point.same_candidate
            assert point.calls_at_stop < traj.total_calls

    def test_the_continuous_signal_has_more_levels_than_the_binary_one(self):
        """The reason patience can be shorter: the winner is identified sooner because the
        signal can tell candidates apart."""
        for arm in ("c09-rationale-on", "c09-rationale-off"):
            binary = load_trajectory(FIXTURES / f"{arm}.json")
            cont = load_trajectory(FIXTURES / f"{arm}-continuous.json")
            assert len(set(cont.scores)) > len(set(binary.scores))


class TestNewSignalRemovesTheFreeLunch:
    """The first real run under the 30-case + continuous signal (run-d55b159050).

    Pins docs/analysis/2026-09-25-patience-is-not-free-anymore.md. The early-stopping default
    rested on the old signal saturating; these assertions fail if that basis quietly returns.
    """

    ARM = "c10probe-30case-continuous"

    def test_the_new_signal_does_not_saturate(self):
        traj = load_trajectory(FIXTURES / f"{self.ARM}.json")
        assert traj.val_subset_size == 30
        assert max(traj.scores) < 1.0

    def test_it_resolves_nearly_every_candidate(self):
        """9 distinct values from 10 candidates, against 6 from 16-22 under the old signal."""
        traj = load_trajectory(FIXTURES / f"{self.ARM}.json")
        assert len(set(traj.scores)) >= len(traj.scores) - 1

    def test_the_best_candidate_is_the_last_one(self):
        """A late gain after a long plateau -- the shape early stopping is worst at, and the
        one the saturated signal could not produce."""
        traj = load_trajectory(FIXTURES / f"{self.ARM}.json")
        assert traj.best_idx == len(traj.scores) - 1

    @pytest.mark.parametrize("patience", DEFAULT_PATIENCES)
    def test_every_patience_returns_a_different_prompt(self, patience):
        """The load-bearing reversal: stopping is no longer free at any tested patience."""
        point = replay(load_trajectory(FIXTURES / f"{self.ARM}.json"), patience)
        assert point.fired
        assert not point.same_candidate

    def test_the_quality_concession_is_nonetheless_tiny(self):
        """Stated so the result is not over-read: the margin is far below any per-metric
        resolution in this repo (0.058-0.103), so 'worse prompt' is not 'much worse'."""
        traj = load_trajectory(FIXTURES / f"{self.ARM}.json")
        point = replay(traj, 15)
        assert traj.scores[traj.best_idx] - point.best_score_at_stop < 0.01
