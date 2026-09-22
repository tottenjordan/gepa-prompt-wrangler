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


def _fake_score(*score_sets):
    """Patch `_score_dataset` to return fixed scores, one per call."""
    results = [mock.Mock(scores=s) for s in score_sets]
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
