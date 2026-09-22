"""`run_quick_eval` — the on-demand health check against a deployed agent.

At **21%** this was the least-covered of the four modules the audit flagged under "under-
tested modules that touch cloud resources". It is the smallest and the least load-bearing of
them, so these tests aim at the handful of places where a quiet mistake would produce a
plausible-looking number rather than an error:

- the **score extraction**, which walks four levels of `getattr` inside a bare
  `except Exception` -- so a shape change upstream yields `{}` and a "clean" report
- the **tool-use aliasing**, which has to match `run_batch_eval` or the same metric appears
  under two names across the two paths
- the **ownership label**, without which the eval run is not reapable
- the **poll loop**, which must stop on a terminal state rather than spin for ten minutes

`time.sleep` is patched throughout; the real loop polls every 15 s for up to 600 s.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from wrangler.eval import online_monitors as om


class TestResolvingTheAgentResource:
    def test_a_bare_engine_id_is_expanded(self):
        resolved = om._resolve_agent_resource("4023875557346246656")

        assert resolved.endswith("/reasoningEngines/4023875557346246656")
        assert resolved.startswith("projects/")

    def test_a_full_resource_name_is_left_alone(self):
        """Callers pass whichever they have. Re-wrapping an already-qualified name produces
        a path that resolves to nothing."""
        full = "projects/p/locations/us-central1/reasoningEngines/123"

        assert om._resolve_agent_resource(full) == full


def _avg(metric: str) -> str:
    """The summary-metrics key shape: `metric/<name>/AVERAGE`."""
    return f"metric/{metric}/AVERAGE"


def _evaluation_run(state: str, metrics: dict | None = None):
    run = mock.MagicMock()
    run.name = "runs/monitor-1"
    run.state = state
    if metrics is None:
        run.evaluation_run_results = None
    else:
        run.evaluation_run_results.summary_metrics.metrics = metrics
    return run


@pytest.fixture
def monitor(tmp_path):
    """Drive `run_quick_eval` with every cloud call faked.

    Yields the client mock so a test can assert on what was sent, and points `OUTPUTS_DIR`
    at a tmp dir so the run's JSON can be read back.
    """
    client = mock.MagicMock()

    with (
        mock.patch.object(om, "vertex_init"),
        mock.patch.object(om, "agent_client", return_value=client),
        mock.patch.object(om.time, "sleep"),
        mock.patch.object(om, "OUTPUTS_DIR", str(tmp_path)),
    ):
        yield client, tmp_path


class TestScoresAreReadOutOfTheEvaluationRun:
    def test_average_metrics_are_extracted(self, monitor):
        client, _ = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run(
            "SUCCEEDED",
            {_avg("safety_v1"): 0.91, _avg("final_response_quality_v1"): 0.83},
        )

        scores = om.run_quick_eval("eng-1", num_cases=1)

        assert scores == {"safety_v1": 0.91, "final_response_quality_v1": 0.83}

    def test_non_average_entries_are_ignored(self, monitor):
        """The summary carries STDDEV and COUNT rows beside the averages. Reading them as
        scores would put a case count on the same scale as a 0-1 metric."""
        client, _ = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run(
            "SUCCEEDED",
            {_avg("safety_v1"): 0.91, "metric/safety_v1/STDDEV": 0.02},
        )

        assert om.run_quick_eval("eng-1", num_cases=1) == {"safety_v1": 0.91}

    def test_the_custom_tool_use_metric_is_aliased_to_the_report_key(self, monitor):
        """The metric is named `tool_use_quality` so it routes to `LLMMetricHandler` rather
        than being hijacked by the predefined handler. Every report reads
        `tool_use_quality_v1`, so the alias has to happen here too -- `run_batch_eval` does
        the same, and the two paths disagreeing would split one metric into two columns."""
        client, _ = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run(
            "SUCCEEDED", {_avg("tool_use_quality"): 1.0}
        )

        assert om.run_quick_eval("eng-1", num_cases=1) == {"tool_use_quality_v1": 1.0}

    def test_a_response_with_no_results_yields_no_scores_rather_than_raising(self, monitor):
        """A failed eval run has no results. Returning `{}` lets the caller see an empty
        report; raising would lose the run record that is written below it."""
        client, _ = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run("FAILED")

        assert om.run_quick_eval("eng-1", num_cases=1) == {}


class TestThePollLoopStopsOnATerminalState:
    @pytest.mark.parametrize("state", ["JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED"])
    def test_a_terminal_state_ends_polling_immediately(self, monitor, state):
        """A **bounded** `side_effect` rather than a `return_value`, deliberately.

        The loop runs `while time.time() - poll_start < 600` and this fixture patches
        `time.sleep` to a no-op, so a loop that fails to break spins on real wall-clock for
        ten minutes. With `return_value` a mutation removing the break made this test *hang*
        instead of fail -- a 600 s timeout in CI rather than a red assertion. Exhausting the
        list raises `StopIteration` on the second call, so the same mutation now fails in
        under a second.
        """
        client, _ = monitor
        client.evals.get_evaluation_run.side_effect = [_evaluation_run(state, {})]

        om.run_quick_eval("eng-1", num_cases=1)

        assert client.evals.get_evaluation_run.call_count == 1

    def test_it_keeps_polling_while_the_run_is_pending(self, monitor):
        client, _ = monitor
        client.evals.get_evaluation_run.side_effect = [
            _evaluation_run("JOB_STATE_RUNNING"),
            _evaluation_run("JOB_STATE_RUNNING"),
            _evaluation_run("JOB_STATE_SUCCEEDED", {}),
        ]

        om.run_quick_eval("eng-1", num_cases=1)

        assert client.evals.get_evaluation_run.call_count == 3


class TestTheRunIsLabelledAndRecorded:
    def test_the_evaluation_run_carries_the_ownership_label(self, monitor):
        """Every GCP resource this project creates carries `solution: promp-wrangler`; an
        eval run without it cannot be found or cleaned up later."""
        client, _ = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run("SUCCEEDED", {})

        om.run_quick_eval("eng-1", num_cases=1)

        assert client.evals.create_evaluation_run.call_args.kwargs["labels"] == {
            "solution": "promp-wrangler"
        }

    def test_the_result_is_written_to_disk_for_trend_analysis(self, monitor):
        """The module exists to "store results for trend analysis" -- a run that scored and
        did not record has done half its job."""
        client, tmp_path = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run(
            "SUCCEEDED", {_avg("safety_v1"): 0.9}
        )

        om.run_quick_eval("eng-1", num_cases=2)

        written = list((tmp_path / "monitors").glob("*.json"))
        assert len(written) == 1
        record = json.loads(written[0].read_text())
        assert record["agent_id"] == "eng-1"
        assert record["num_cases"] == 2
        assert record["scores"] == {"safety_v1": 0.9}

    def test_num_cases_limits_what_is_sent(self, monitor):
        client, _ = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run("SUCCEEDED", {})

        om.run_quick_eval("eng-1", num_cases=2)

        sent = client.evals.run_inference.call_args.kwargs["src"]
        assert len(sent) == 2

    def test_no_limit_sends_every_case(self, monitor):
        client, _ = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run("SUCCEEDED", {})

        om.run_quick_eval("eng-1")

        sent = client.evals.run_inference.call_args.kwargs["src"]
        assert len(sent) == len(om.QUICK_EVAL_CASES)
