"""Repeated scoring must keep its per-case rows, and say how often the judge disagrees.

DOE 02 asks how much of the noise floor is the judge rather than the agent. `wrangler score
--repeat N` already scores identical responses N times, which is the valid way to ask -- but
the CLI printed mean/sd/min/max and **saved nothing**, so the per-case disagreement the
pre-registration requires was computed and then discarded.

A mean over five scorings hides the quantity being measured. So does an sd, for a different
reason: two metrics can share an sd while one disagrees on every case by a little and the
other on one case by a lot. The per-case rate separates them.
"""

from __future__ import annotations

from wrangler.eval.evaluator import CASE_INDEX_KEY, per_case_disagreement


def _rows(*scores: float) -> list[dict]:
    """One pass over len(scores) cases, one metric."""
    return [{CASE_INDEX_KEY: i, "safety_v1": s} for i, s in enumerate(scores)]


class TestDisagreementRate:
    def test_identical_passes_disagree_nowhere(self):
        runs = [_rows(0.8, 0.9, 1.0)] * 3
        assert per_case_disagreement(runs) == {"safety_v1": 0.0}

    def test_one_case_differing_in_one_pass_counts_once(self):
        runs = [_rows(0.8, 0.9, 1.0), _rows(0.8, 0.5, 1.0), _rows(0.8, 0.9, 1.0)]
        assert per_case_disagreement(runs) == {"safety_v1": 1 / 3}

    def test_every_case_differing_is_one(self):
        runs = [_rows(0.1, 0.2), _rows(0.3, 0.4)]
        assert per_case_disagreement(runs) == {"safety_v1": 1.0}

    def test_cases_are_matched_by_index_not_position(self):
        """Passes drop different cases; position k is a different case in each.

        `average_per_case` was fixed for exactly this and the ~0.034 noise floor had to be
        re-measured because of it. Do not reintroduce it here.
        """
        a = [{CASE_INDEX_KEY: 0, "safety_v1": 0.5}, {CASE_INDEX_KEY: 5, "safety_v1": 0.9}]
        b = [{CASE_INDEX_KEY: 5, "safety_v1": 0.9}]  # only case 5, at position 0
        assert per_case_disagreement([a, b]) == {"safety_v1": 0.0}

    def test_a_case_only_one_pass_scored_cannot_disagree(self):
        """With one observation there is nothing to disagree with -- excluded, not counted.

        Counting it as agreement would dilute the rate toward zero on exactly the runs
        where the judge is flakiest.
        """
        a = [{CASE_INDEX_KEY: 0, "safety_v1": 0.5}, {CASE_INDEX_KEY: 1, "safety_v1": 0.9}]
        b = [{CASE_INDEX_KEY: 0, "safety_v1": 0.4}]
        assert per_case_disagreement([a, b]) == {"safety_v1": 1.0}

    def test_metrics_are_independent(self):
        runs = [
            [{CASE_INDEX_KEY: 0, "a": 1.0, "b": 1.0}],
            [{CASE_INDEX_KEY: 0, "a": 1.0, "b": 0.0}],
        ]
        assert per_case_disagreement(runs) == {"a": 0.0, "b": 1.0}

    def test_the_case_index_is_not_reported_as_a_metric(self):
        assert CASE_INDEX_KEY not in per_case_disagreement([_rows(0.5), _rows(0.5)])

    def test_no_rows_is_empty_not_a_crash(self):
        assert per_case_disagreement([]) == {}
        assert per_case_disagreement([[], []]) == {}
