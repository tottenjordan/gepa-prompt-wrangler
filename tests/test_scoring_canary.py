"""A canary measures the autorater, so its own fidelity is the thing to test.

The reading is only meaningful if the bytes going into the second scoring are the bytes that
went into the first. Everything here is about that: the round-trip is exact, the file outlives
the SDK objects a capture pickles, and a drift is refused when the two readings are not of the
same canary.

**Why this exists.** Campaign 09's control drifted 9x the floor DOE 03 measured, all three
arms moved together across a ~16 h gap, and nothing could separate a service-side autorater
change from a prompt effect — so the primary readout was UNRESOLVED. The autorater cannot be
recorded (`create_evaluation_run()` takes no judge parameter); it can be measured.

No test here calls Vertex. `_score_dataset` is faked — what is under test is the canary, not
the judge.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from wrangler.eval import canary as cn


@pytest.fixture
def frame():
    """An inference frame shaped like a real capture, including the one bytes field.

    Columns match a live capture exactly (`doe02-cap1`): prompt, session_inputs,
    expected_tool, case_description, reference, intermediate_events, response, agent_data.
    """
    import pandas as pd
    from agentplatform import types

    session = types.evals.SessionInput(user_id="wrangler-eval", state={})
    return pd.DataFrame(
        [
            {
                "prompt": f"case {i}",
                "session_inputs": session,
                "expected_tool": "search_flights",
                "case_description": f"desc {i}",
                "reference": f"ref {i}",
                "intermediate_events": [{"event": i}],
                "response": f"response {i}",
                "agent_data": {
                    "turns": [
                        {
                            "events": [
                                {
                                    "content": {
                                        "parts": [
                                            {
                                                "text": f"thinking {i}",
                                                # The only non-JSON-native leaf a real
                                                # capture carries.
                                                "thought_signature": bytes([i, 255, 0, 17]),
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ]
                },
            }
            for i in range(3)
        ]
    )


class TestTheRoundTripIsExact:
    """ "The same bytes" has to be literally true, or the reading measures the codec."""

    def test_responses_survive(self, frame, tmp_path):
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        back = cn.load_canary(path)

        assert list(back["response"]) == list(frame["response"])
        assert list(back["prompt"]) == list(frame["prompt"])

    def test_the_trajectory_survives(self, frame, tmp_path):
        """`agent_data` is what `tool_use_quality` is scored against. A canary that lost it
        would report drift on four metrics and silence on the fifth."""
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        back = cn.load_canary(path)

        assert list(back["agent_data"]) == list(frame["agent_data"])
        assert list(back["intermediate_events"]) == list(frame["intermediate_events"])

    def test_bytes_are_preserved_not_dropped(self, frame, tmp_path):
        """`thought_signature` is opaque and the judge almost certainly ignores it — but a
        canary claiming byte-identity cannot quietly discard a field. Measured on a real
        capture: 44 leaves, 31,900 bytes."""
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        back = cn.load_canary(path)

        restored = [
            row["turns"][0]["events"][0]["content"]["parts"][0]["thought_signature"]
            for row in back["agent_data"]
        ]
        assert restored == [bytes([i, 255, 0, 17]) for i in range(3)]
        assert all(isinstance(b, bytes) for b in restored), "decoded back to bytes, not str"

    def test_column_order_matches_the_original(self, frame, tmp_path):
        """The SDK validates the frame it is handed; a reordered one is a different object."""
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")

        assert list(cn.load_canary(path).columns) == list(frame.columns)

    def test_the_frame_is_scorable(self, frame, tmp_path):
        """The real gate: `_assert_scorable` is what the eval path runs before submitting."""
        from wrangler.eval.evaluator import _assert_scorable

        back = cn.load_canary(cn.freeze_canary(frame, tmp_path / "c.json", label="probe"))

        _assert_scorable(back, "canary")


class TestTheFileOutlivesTheSdk:
    """A capture is a pickle, and `save_capture` calls it "scratch, not archive -- an SDK bump
    can render an old one unloadable". A canary compares across time, so it cannot be that."""

    def test_it_is_plain_json(self, frame, tmp_path):
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")

        json.loads(Path(path).read_text())  # raises if not

    def test_no_sdk_object_is_serialised(self, frame, tmp_path):
        """`session_inputs` is rebuilt on load. Storing it would make the file depend on the
        very class it is meant to survive."""
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        payload = json.loads(Path(path).read_text())

        assert "session_inputs" not in payload["columns"]
        assert all("session_inputs" not in case for case in payload["cases"])

    def test_session_inputs_is_restored_on_load(self, frame, tmp_path):
        from agentplatform import types

        back = cn.load_canary(cn.freeze_canary(frame, tmp_path / "c.json", label="probe"))

        assert all(isinstance(s, types.evals.SessionInput) for s in back["session_inputs"])

    def test_provenance_is_readable_without_decoding_the_cases(self, frame, tmp_path):
        """A reading is only interpretable next to what produced the responses."""
        path = cn.freeze_canary(
            frame, tmp_path / "c.json", label="probe", source="cap.pkl", model="claude-sonnet-5"
        )
        meta = cn.canary_metadata(path)

        assert meta["label"] == "probe"
        assert meta["model"] == "claude-sonnet-5"
        assert meta["source_capture"] == "cap.pkl"
        assert meta["rows"] == 3
        assert "cases" not in meta


def _fake_score(*score_sets, coverage=None, per_case=None):
    """Patch `_score_dataset` to return fixed scores, one per call.

    `coverage` defaults to full (64 cases per metric), because the interesting case is the
    uneven one and it should have to be asked for. `per_case` is one list of rows per call,
    empty by default.
    """
    per_case = per_case or [[] for _ in score_sets]
    results = [
        mock.Mock(
            scores=s,
            coverage=coverage if coverage is not None else dict.fromkeys(s, 64),
            per_case=rows,
        )
        for s, rows in zip(score_sets, per_case, strict=True)
    ]
    return mock.patch("wrangler.eval.evaluator._score_dataset", side_effect=results)


class TestScoringTouchesNoEngine:
    """The property the whole idea rests on: the responses come out of the file, so a reading
    measures the judge and nothing else."""

    def test_no_agent_resource_is_passed(self, frame, tmp_path):
        """A canary's engine may be long deleted, and engine ids are never pinned here."""
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        with (
            _fake_score({"safety_v1": 0.9}) as scorer,
            mock.patch("wrangler.eval.evaluator.agent_client"),
        ):
            cn.score_canary(path)

        assert scorer.call_args.kwargs["agent_resource"] is None

    def test_a_reading_carries_its_scores_and_provenance(self, frame, tmp_path):
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        with _fake_score({"safety_v1": 0.9}), mock.patch("wrangler.eval.evaluator.agent_client"):
            reading = cn.score_canary(path)

        assert reading["scores"] == {"safety_v1": 0.9}
        assert reading["label"] == "probe"
        assert reading["canary_path"] == path
        assert reading["scored_at"]

    def test_repeats_are_averaged_and_kept(self, frame, tmp_path):
        """Averaging is the same lever `score_repeats` pulls; the individual passes stay so a
        drift smaller than the judge's own per-pass noise can be recognised as unreadable."""
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        with (
            _fake_score({"safety_v1": 0.8}, {"safety_v1": 1.0}),
            mock.patch("wrangler.eval.evaluator.agent_client"),
        ):
            reading = cn.score_canary(path, repeats=2)

        assert reading["scores"]["safety_v1"] == pytest.approx(0.9)
        assert reading["repeats"] == 2
        assert reading["passes"] == [{"safety_v1": 0.8}, {"safety_v1": 1.0}]


class TestDriftIsTheNumberCampaignNineCouldNotProduce:
    def test_it_reports_per_metric_movement(self):
        before = {"canary_path": "c.json", "label": "p", "scores": {"safety_v1": 0.80}}
        after = {"canary_path": "c.json", "label": "p", "scores": {"safety_v1": 0.87}}

        drift = cn.canary_drift(before, after)

        assert drift["deltas"]["safety_v1"] == pytest.approx(0.07)
        assert drift["max_abs_drift"] == pytest.approx(0.07)

    def test_max_abs_drift_ignores_direction(self):
        """A judge that got harsher invalidates a comparison exactly as much as one that got
        softer; the campaign reads the magnitude."""
        before = {"canary_path": "c", "scores": {"a": 0.9, "b": 0.5}}
        after = {"canary_path": "c", "scores": {"a": 0.8, "b": 0.52}}

        assert cn.canary_drift(before, after)["max_abs_drift"] == pytest.approx(0.1)

    def test_comparing_two_different_canaries_is_refused(self):
        """Two different response sets scored at two different times measure nothing. This is
        the mistake that makes a drift number look authoritative and mean nothing."""
        before = {"canary_path": "one.json", "scores": {"safety_v1": 0.8}}
        after = {"canary_path": "two.json", "scores": {"safety_v1": 0.9}}

        with pytest.raises(ValueError, match="different canaries"):
            cn.canary_drift(before, after)

    def test_a_metric_on_one_side_only_is_named_not_silently_dropped(self):
        """Usually means the metric set changed between readings, which invalidates the
        comparison for that metric rather than being a zero."""
        before = {"canary_path": "c", "scores": {"safety_v1": 0.8, "gone_v1": 0.5}}
        after = {"canary_path": "c", "scores": {"safety_v1": 0.8, "new_v1": 0.5}}

        drift = cn.canary_drift(before, after)

        assert drift["deltas"] == {"safety_v1": 0.0}
        assert drift["unmatched"] == ["gone_v1", "new_v1"]

    def test_the_campaign_09_numbers_read_the_way_the_write_up_does(self):
        """A regression guard on the arithmetic, using the case that motivated this.

        Campaign 09 saw a control drift of +0.0732 on `safety_v1` and a between-arm contrast
        of -0.0044. Had a canary been running and shown most of that drift, the contrast would
        have been visibly inside it — which is the call the campaign had to make on a floor
        borrowed from a DOE measured minutes apart.
        """
        drift = cn.canary_drift(
            {"canary_path": "c", "scores": {"safety_v1": 0.8000}},
            {"canary_path": "c", "scores": {"safety_v1": 0.8700}},
        )

        assert drift["max_abs_drift"] == pytest.approx(0.07, abs=1e-6)
        assert abs(-0.0044) < drift["max_abs_drift"], "the contrast sits inside the drift"


class TestTheStageHelperNeverFailsTheEvalItMeasures:
    """A canary is instrumentation. A stage that died because its thermometer broke would be
    a worse outcome than an unmeasured window — the eval it is attached to costs ~28 minutes
    and sits in the middle of a 24-hour campaign."""

    def test_no_canary_configured_is_a_no_op(self):
        """Opt-in: an existing manifest gets byte-identical behaviour and no extra spend."""
        with mock.patch("wrangler.eval.canary.score_canary") as scorer:
            assert cn.canary_reading_for_stage("") == {}

        scorer.assert_not_called()

    def test_a_scoring_failure_is_recorded_rather_than_raised(self):
        with mock.patch(
            "wrangler.eval.canary.score_canary", side_effect=RuntimeError("judge unavailable")
        ):
            reading = cn.canary_reading_for_stage("c.json")

        assert "RuntimeError" in reading["error"]
        assert reading["scores"] == {}, "an empty score set, not a missing key"

    def test_a_missing_canary_file_is_recorded_rather_than_raised(self):
        """The realistic failure: the file did not make it into the code tarball."""
        reading = cn.canary_reading_for_stage("does/not/exist.json")

        assert reading["error"]
        assert reading["scores"] == {}

    def test_the_root_prefix_is_applied_for_the_container(self):
        """The pipeline unpacks the code tarball at `/app`, so a manifest-relative path has
        to be resolved against it."""
        with mock.patch(
            "wrangler.eval.canary.score_canary", return_value={"scores": {}, "label": ""}
        ) as scorer:
            cn.canary_reading_for_stage("data/canaries/c.json", root="/app")

        assert scorer.call_args.args[0] == "/app/data/canaries/c.json"


class TestBothExecutionPathsScoreTheCanary:
    """`skip_optimize` and `forward_rationale` were wired into the pipeline and not the local
    path, so a manifest got a different experiment depending on how it was launched. The same
    mistake here would mean a campaign silently has no drift measurement on one path."""

    def test_the_pipeline_eval_component_takes_a_canary_path(self):
        import inspect

        from wrangler.pipeline import components

        params = inspect.signature(components.eval_single_agent.python_func).parameters
        assert "canary_path" in params

    def test_the_dag_passes_it_to_every_eval_task(self):
        """**Every** eval task, or there is nothing to difference.

        There are three -- `eval_before`, `eval_after`, and the control arm's own
        `control_after` -- and the control arm is precisely the one whose drift campaign 09
        misread. Counted against the call sites rather than a literal, so adding a fourth
        eval task without wiring it fails here instead of silently losing a reading. The
        `before` site sits at a different indentation and was missed on the first attempt.
        """
        import inspect

        from wrangler.pipeline import dag

        src = inspect.getsource(dag.build_pipeline)
        eval_tasks = src.count('comps["eval"](')

        assert eval_tasks == 3, f"expected before/after/control_after, found {eval_tasks}"
        assert src.count("canary_path=canary_path") == eval_tasks

    def test_the_local_stage_reads_the_same_manifest_key(self):
        import inspect

        from wrangler.orchestration import stages

        src = inspect.getsource(stages.stage_eval)
        assert '"canary"' in src
        assert "canary_reading_for_stage" in src


class TestCoverageIsRecordedAndGuardsTheDrift:
    """A mean is not comparable across differing coverage.

    That is the dropout silent-failures #5 showed reads as a real effect, and a drift reading
    would otherwise mistake it for the judge moving. **Found on the first real run**: a
    2026-09-22 re-score of a 2026-09-17 capture came back 61/64 on
    `instruction_following_v1` against 64/64 originally — the reading recorded the score and
    said nothing about the shrunken denominator.
    """

    def test_a_reading_records_cases_per_metric(self, frame, tmp_path):
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        with (
            _fake_score({"safety_v1": 0.9}, coverage={"safety_v1": 61}),
            mock.patch("wrangler.eval.evaluator.agent_client"),
        ):
            reading = cn.score_canary(path)

        assert reading["coverage"] == {"safety_v1": 61}

    def test_a_metric_whose_coverage_moved_is_excluded_from_the_drift(self):
        """Excluded from `max_abs_drift` specifically, because a campaign reads that as a
        threshold — folding in a dropout artefact would raise the bar for every metric."""
        before = {
            "canary_path": "c",
            "scores": {"safety_v1": 1.00, "instruction_following_v1": 0.80},
            "coverage": {"safety_v1": 64, "instruction_following_v1": 64},
        }
        after = {
            "canary_path": "c",
            "scores": {"safety_v1": 1.00, "instruction_following_v1": 0.60},
            "coverage": {"safety_v1": 64, "instruction_following_v1": 61},
        }

        drift = cn.canary_drift(before, after)

        assert drift["uneven_coverage"] == ["instruction_following_v1"]
        assert drift["max_abs_drift"] == 0.0, "the 0.20 move is dropout, not the judge"
        assert drift["deltas"]["instruction_following_v1"] == pytest.approx(-0.20), (
            "still reported, so it is visible -- just not counted as drift"
        )

    def test_even_coverage_is_compared_normally(self):
        before = {"canary_path": "c", "scores": {"a": 0.80}, "coverage": {"a": 64}}
        after = {"canary_path": "c", "scores": {"a": 0.87}, "coverage": {"a": 64}}

        drift = cn.canary_drift(before, after)

        assert drift["uneven_coverage"] == []
        assert drift["max_abs_drift"] == pytest.approx(0.07)

    def test_readings_without_coverage_still_compare(self):
        """Backwards-compatible: a reading taken before coverage was recorded has no
        coverage key, and must not silently become 'uneven'."""
        before = {"canary_path": "c", "scores": {"a": 0.80}}
        after = {"canary_path": "c", "scores": {"a": 0.87}}

        drift = cn.canary_drift(before, after)

        assert drift["uneven_coverage"] == []
        assert drift["max_abs_drift"] == pytest.approx(0.07)


def _rows(scores: dict[int, float], metric: str = "safety_v1") -> list[dict]:
    """Per-case rows as the evaluator writes them: case index plus metric scores."""
    return [{"case_index": i, metric: v} for i, v in scores.items()]


class TestAReadingKeepsItsPerCaseScores:
    """run-413630e488's six canary readings ranged 36-64 cases per metric and stored only
    means, so they could not be restricted to common cases -- the one comparison in that
    write-up that could not be paired."""

    def test_the_reading_carries_per_case_rows(self, frame, tmp_path):
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        with (
            _fake_score({"safety_v1": 0.5}, per_case=[_rows({0: 1.0, 1: 0.0})]),
            mock.patch("wrangler.eval.evaluator.agent_client"),
        ):
            reading = cn.score_canary(path)

        assert reading["per_case"] == _rows({0: 1.0, 1: 0.0})

    def test_repeated_passes_union_their_cases(self, frame, tmp_path):
        """Passes drop different cases. A case scored in any pass survives, and a case scored
        in both is averaged -- the same rule `score_repeats` applies to an eval side, which is
        what makes repeats recover a canary's coverage."""
        path = cn.freeze_canary(frame, tmp_path / "c.json", label="probe")
        with (
            _fake_score(
                {"safety_v1": 0.5},
                {"safety_v1": 0.5},
                per_case=[_rows({0: 1.0, 1: 0.0}), _rows({1: 1.0, 2: 1.0})],
            ),
            mock.patch("wrangler.eval.evaluator.agent_client"),
        ):
            reading = cn.score_canary(path, repeats=2)

        by_case = {r["case_index"]: r["safety_v1"] for r in reading["per_case"]}
        assert by_case == {0: 1.0, 1: pytest.approx(0.5), 2: 1.0}


class TestDriftIsPairedWhenBothReadingsCanBe:
    def test_the_drift_is_over_common_cases_only(self):
        """Case 2 scored only before. Unpaired, its 0.0 drags the before mean down and reads
        as the judge getting kinder; paired, it is simply not part of the comparison."""
        before = {
            "canary_path": "c",
            "scores": {"safety_v1": 1 / 3},
            "per_case": _rows({0: 1.0, 1: 0.0, 2: 0.0}),
        }
        after = {
            "canary_path": "c",
            "scores": {"safety_v1": 1.0},
            "per_case": _rows({0: 1.0, 1: 1.0}),
        }

        drift = cn.canary_drift(before, after)

        assert drift["basis"] == "paired"
        assert drift["deltas"]["safety_v1"] == pytest.approx(0.5)
        assert drift["n_paired"]["safety_v1"] == 2
        assert drift["cases_changed"]["safety_v1"] == 1
        assert drift["mean_deltas"]["safety_v1"] == pytest.approx(2 / 3), "kept for reference"

    def test_uneven_coverage_no_longer_disqualifies_a_metric(self):
        """The means-basis drift had to exclude a metric whose coverage moved. Pairing removes
        the reason: the comparison is restricted to cases in common, so it still counts."""
        before = {
            "canary_path": "c",
            "scores": {"safety_v1": 0.5},
            "coverage": {"safety_v1": 64},
            "per_case": _rows({0: 1.0, 1: 0.0}),
        }
        after = {
            "canary_path": "c",
            "scores": {"safety_v1": 0.0},
            "coverage": {"safety_v1": 36},
            "per_case": _rows({1: 0.0}),
        }

        drift = cn.canary_drift(before, after)

        assert drift["uneven_coverage"] == []
        assert drift["deltas"]["safety_v1"] == 0.0, "the judge did not move on the shared case"
        assert drift["max_abs_drift"] == 0.0

    def test_cancelling_moves_show_up_as_changed_cases(self):
        """A mean can sit still while the judge re-scores cases in both directions. The
        per-case count is where that becomes visible."""
        before = {
            "canary_path": "c",
            "scores": {"safety_v1": 0.5},
            "per_case": _rows({0: 1.0, 1: 0.0}),
        }
        after = {
            "canary_path": "c",
            "scores": {"safety_v1": 0.5},
            "per_case": _rows({0: 0.0, 1: 1.0}),
        }

        drift = cn.canary_drift(before, after)

        assert drift["deltas"]["safety_v1"] == 0.0
        assert drift["cases_changed"]["safety_v1"] == 2

    def test_a_metric_with_no_common_case_is_named(self):
        before = {"canary_path": "c", "scores": {"safety_v1": 1.0}, "per_case": _rows({0: 1.0})}
        after = {"canary_path": "c", "scores": {"safety_v1": 1.0}, "per_case": _rows({1: 1.0})}

        drift = cn.canary_drift(before, after)

        assert drift["unpaired"] == ["safety_v1"]
        assert "safety_v1" not in drift["deltas"]

    def test_a_reading_without_per_case_scores_falls_back_to_means(self):
        """Every canary reading written before this change has no per-case rows. Mixed with a
        new one, pairing is impossible, so the old rule applies -- including its coverage
        exclusion."""
        before = {"canary_path": "c", "scores": {"safety_v1": 0.8}, "coverage": {"safety_v1": 64}}
        after = {
            "canary_path": "c",
            "scores": {"safety_v1": 0.9},
            "coverage": {"safety_v1": 61},
            "per_case": _rows({0: 1.0}),
        }

        drift = cn.canary_drift(before, after)

        assert drift["basis"] == "means"
        assert drift["uneven_coverage"] == ["safety_v1"]
        assert drift["max_abs_drift"] == 0.0


class TestTheStageHelperForwardsRepeats:
    def test_repeats_reach_the_scorer(self):
        with mock.patch(
            "wrangler.eval.canary.score_canary", return_value={"scores": {}, "label": ""}
        ) as scorer:
            cn.canary_reading_for_stage("c.json", repeats=2)

        assert scorer.call_args.kwargs["repeats"] == 2
