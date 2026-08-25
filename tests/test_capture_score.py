"""Tests for capture/score separation.

Inference and scoring have always been separable — `run_batch_eval` runs one
then hands the result to the other — but they were welded together, so every
question about a *judge* cost a full pass of agent calls against an engine that
drops a third of them.

Splitting them makes judge non-determinism, judge-model comparisons and metric
prompt changes cheap and exactly comparable: the same responses, scored again.
It also puts those campaigns out of reach of the empty-stream defect entirely,
because after the capture there are no agent calls at all.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from wrangler.eval import evaluator


def _frame(n=3):
    """A frame shaped like what run_inference returns, objects included."""
    return pd.DataFrame(
        {
            "prompt": [f"case {i}" for i in range(n)],
            "response": [f"answer {i}" for i in range(n)],
            "agent_data": [{"tool_calls": [{"name": "search_flights"}]} for _ in range(n)],
            "expected_tool": ["search_flights"] * n,
        }
    )


class TestCaptureRoundTrip:
    def test_capture_writes_frame_and_sidecar(self, tmp_path: Path):
        path = evaluator.save_capture(
            _frame(), out_dir=str(tmp_path), label="ctrl", engine_id="123", model="m"
        )
        assert Path(path).exists()
        assert Path(path).with_suffix(".json").exists()

    def test_frame_survives_the_round_trip(self, tmp_path: Path):
        original = _frame()
        path = evaluator.save_capture(original, out_dir=str(tmp_path), label="ctrl")
        pd.testing.assert_frame_equal(evaluator.load_capture(path), original)

    def test_sidecar_identifies_the_capture(self, tmp_path: Path):
        """It must stay readable even when the pickle cannot be loaded.

        The frame holds SDK pydantic objects, so an SDK bump can make an old
        capture unloadable. The sidecar is what says what it was.
        """
        path = evaluator.save_capture(
            _frame(5), out_dir=str(tmp_path), label="ctrl", engine_id="eng9", model="mod"
        )
        meta = json.loads(Path(path).with_suffix(".json").read_text())
        assert meta["engine_id"] == "eng9"
        assert meta["model"] == "mod"
        assert meta["rows"] == 5
        assert meta["label"] == "ctrl"
        assert meta["captured_at"]

    def test_submitted_and_dropped_are_recorded(self, tmp_path: Path):
        """How many cases the dropout cost is part of what a capture *is*."""
        path = evaluator.save_capture(_frame(3), out_dir=str(tmp_path), label="c", submitted=10)
        meta = json.loads(Path(path).with_suffix(".json").read_text())
        assert meta["rows"] == 3
        assert meta["submitted"] == 10
        assert meta["dropped"] == 7


class TestScoringMakesNoAgentCalls:
    """The whole point. If scoring re-runs inference, none of the campaigns work."""

    def test_score_captured_never_calls_run_inference(self, tmp_path: Path):
        path = evaluator.save_capture(_frame(), out_dir=str(tmp_path), label="c")

        client = MagicMock()
        client.evals.run_inference.side_effect = AssertionError("scoring must not infer")
        run = MagicMock()
        run.name = "projects/p/locations/l/evaluationRuns/r"
        run.state = "SUCCEEDED"
        client.evals.create_evaluation_run.return_value = run
        client.evals.get_evaluation_run.return_value = run

        with (
            patch.object(evaluator, "Client", return_value=client),
            patch.object(evaluator, "vertexai"),
            patch.object(evaluator, "_extract_aggregate_scores", return_value={"safety_v1": 1.0}),
            patch.object(evaluator, "_extract_per_case_scores", return_value=([], "sdk")),
        ):
            result = evaluator.score_captured(path, agent_name="c")

        client.evals.run_inference.assert_not_called()
        assert result.scores == {"safety_v1": 1.0}

    def test_scoring_passes_the_captured_frame_through(self, tmp_path: Path):
        path = evaluator.save_capture(_frame(4), out_dir=str(tmp_path), label="c")

        client = MagicMock()
        run = MagicMock()
        run.name = "projects/p/locations/l/evaluationRuns/r"
        run.state = "SUCCEEDED"
        client.evals.create_evaluation_run.return_value = run
        client.evals.get_evaluation_run.return_value = run

        with (
            patch.object(evaluator, "Client", return_value=client),
            patch.object(evaluator, "vertexai"),
            patch.object(evaluator, "_extract_aggregate_scores", return_value={}),
            patch.object(evaluator, "_extract_per_case_scores", return_value=([], "sdk")),
        ):
            evaluator.score_captured(path)

        sent = client.evals.create_evaluation_run.call_args.kwargs["dataset"]
        assert len(sent.eval_dataset_df) == 4

    def test_custom_metrics_reach_the_eval_run(self, tmp_path: Path):
        """Campaign 2 scores one capture with several different judges."""
        path = evaluator.save_capture(_frame(), out_dir=str(tmp_path), label="c")

        client = MagicMock()
        run = MagicMock()
        run.name = "projects/p/locations/l/evaluationRuns/r"
        run.state = "SUCCEEDED"
        client.evals.create_evaluation_run.return_value = run
        client.evals.get_evaluation_run.return_value = run
        sentinel = ["just-this-one"]

        with (
            patch.object(evaluator, "Client", return_value=client),
            patch.object(evaluator, "vertexai"),
            patch.object(evaluator, "_extract_aggregate_scores", return_value={}),
            patch.object(evaluator, "_extract_per_case_scores", return_value=([], "sdk")),
        ):
            evaluator.score_captured(path, metrics=sentinel)

        assert client.evals.create_evaluation_run.call_args.kwargs["metrics"] == sentinel


class TestRepeatScoring:
    def test_repeat_scores_the_same_capture_n_times(self, tmp_path: Path):
        path = evaluator.save_capture(_frame(), out_dir=str(tmp_path), label="c")
        calls = []

        def _fake(capture_path, metrics=None, agent_name="", expected=None):
            calls.append(capture_path)
            return evaluator.EvalResult(scores={"safety_v1": 0.5 + 0.1 * len(calls)})

        with patch.object(evaluator, "score_captured", side_effect=_fake):
            out = evaluator.score_captured_repeated(path, repeat=3)

        assert len(calls) == 3
        assert out["n"] == 3
        assert out["mean"]["safety_v1"] == pytest.approx(0.7)

    def test_repeat_reports_spread_not_just_the_mean(self):
        """Judge non-determinism is the quantity; a mean of five hides it."""
        results = [evaluator.EvalResult(scores={"safety_v1": v}) for v in (0.2, 0.4, 0.6, 0.8, 1.0)]
        summary = evaluator.summarize_repeats(results)
        assert summary["mean"]["safety_v1"] == pytest.approx(0.6)
        assert summary["std"]["safety_v1"] == pytest.approx(0.316, abs=0.01)
        assert summary["min"]["safety_v1"] == 0.2
        assert summary["max"]["safety_v1"] == 1.0

    def test_a_single_repeat_has_zero_spread_not_an_error(self):
        summary = evaluator.summarize_repeats([evaluator.EvalResult(scores={"m": 0.5})])
        assert summary["std"]["m"] == 0.0

    def test_no_results_is_empty_rather_than_zero(self):
        assert evaluator.summarize_repeats([])["n"] == 0
