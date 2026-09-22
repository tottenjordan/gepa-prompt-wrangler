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


def _raising_run(state: str = "SUCCEEDED"):
    """An evaluation run whose metrics cannot be read.

    **A non-numeric metric value, not a missing attribute.** `getattr(sm, "metrics", None)`
    takes a default, so an `AttributeError` from a moved attribute is swallowed and the
    `except` never sees it -- the extraction degrades to `{}` silently by a different route.
    What does reach the handler is the conversion in the loop body: `float(v)` on a value
    that is not a number, or `dict(nested)` on something that is not a mapping. Both are
    what an upstream shape change actually looks like from here, and that surface already
    moved once at google-cloud-aiplatform 2.1.0.

    Found by writing this test: the first version raised `AttributeError` and the handler
    never fired, which would have made every assertion below vacuous.
    """
    run = mock.MagicMock()
    run.name = "runs/monitor-1"
    run.state = state
    run.evaluation_run_results.summary_metrics.metrics = {_avg("safety_v1"): "not-a-number"}
    return run


class TestAFailedExtractionIsDistinguishableFromAnEmptyResult:
    """The record is durable, so this asymmetry outlives the run that produced it.

    Before 2026-09-22 both "the agent scored nothing" and "we could not read the scores"
    produced `{}` plus a `Warning:` line that scrolls past in a long log -- and the empty
    record was then written into the directory this module exists to accumulate for trend
    analysis, where a missing measurement plots as a zero.
    """

    def _record(self, tmp_path):
        written = list((tmp_path / "monitors").glob("*.json"))
        assert len(written) == 1
        return json.loads(written[0].read_text())

    def test_a_failed_extraction_is_recorded_as_an_error(self, monitor):
        client, tmp_path = monitor
        client.evals.get_evaluation_run.return_value = _raising_run()

        scores = om.run_quick_eval("eng-1", num_cases=1)

        assert scores == {}, "the return type is unchanged; the distinction is in the record"
        record = self._record(tmp_path)
        assert record["scores"] == {}
        assert "ValueError" in record["scores_error"]

    def test_a_genuinely_empty_result_carries_no_error(self, monitor):
        """The other half. Without this the new field could be set unconditionally and every
        test above would still pass."""
        client, tmp_path = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run("JOB_STATE_FAILED")

        om.run_quick_eval("eng-1", num_cases=1)

        record = self._record(tmp_path)
        assert record["scores"] == {}
        assert record["scores_error"] == ""
        assert record["scores_empty_reason"] == "no evaluation_run_results"

    def test_a_successful_run_carries_no_error(self, monitor):
        client, tmp_path = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run(
            "SUCCEEDED", {_avg("safety_v1"): 0.9}
        )

        om.run_quick_eval("eng-1", num_cases=1)

        record = self._record(tmp_path)
        assert record["scores_error"] == ""

    def test_the_run_state_is_recorded_so_an_empty_result_is_explicable(self, monitor):
        """`{}` with no error is ambiguous on its own: a FAILED run and a SUCCEEDED one that
        reported no metrics are different things. The state was already computed by the poll
        loop and thrown away."""
        client, tmp_path = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run("JOB_STATE_FAILED")

        om.run_quick_eval("eng-1", num_cases=1)

        assert self._record(tmp_path)["eval_run_state"] == "JOB_STATE_FAILED"

    def test_the_operator_is_told_the_run_measured_nothing(self, monitor, capsys):
        """A `Warning:` line above a blank "Results:" block reads like a clean run with no
        findings. It has to say the scores are unavailable, not zero."""
        client, _ = monitor
        client.evals.get_evaluation_run.return_value = _raising_run()

        om.run_quick_eval("eng-1", num_cases=1)

        out = capsys.readouterr().out
        assert "UNREADABLE" in out
        assert "Not the same as a zero" in out


class TestASilentlyCollapsedResponseIsAlsoExplained:
    """The second route to an empty result, and the one a `try/except` cannot see.

    Every `getattr` in the extraction chain passes a default, so a response whose shape has
    moved yields `{}` with nothing raised -- indistinguishable from an agent that scored
    nothing. Naming the level that was absent is what separates them.
    """

    def _reason(self, tmp_path):
        written = list((tmp_path / "monitors").glob("*.json"))
        assert len(written) == 1
        record = json.loads(written[0].read_text())
        assert record["scores_error"] == "", "nothing raised on these paths"
        return record["scores_empty_reason"]

    def test_missing_summary_metrics_is_named(self, monitor):
        client, tmp_path = monitor
        run = mock.MagicMock()
        run.name, run.state = "r", "SUCCEEDED"
        run.evaluation_run_results.summary_metrics = None
        client.evals.get_evaluation_run.return_value = run

        om.run_quick_eval("eng-1", num_cases=1)

        assert self._reason(tmp_path) == "results present but no summary_metrics"

    def test_missing_metrics_is_named(self, monitor):
        client, tmp_path = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run("SUCCEEDED", {})

        om.run_quick_eval("eng-1", num_cases=1)

        assert self._reason(tmp_path) == "summary_metrics present but no metrics"

    def test_a_changed_key_format_is_named_rather_than_reported_as_zero_scores(self, monitor):
        """The worst of the three: if Vertex renames `/AVERAGE`, every monitor on every
        engine reports `{}` forever and it looks like the agents stopped scoring."""
        client, tmp_path = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run(
            "SUCCEEDED", {"metric/safety_v1/MEAN": 0.9, "metric/safety_v1/STDDEV": 0.1}
        )

        om.run_quick_eval("eng-1", num_cases=1)

        assert self._reason(tmp_path) == "2 metric(s), none matching /AVERAGE"

    def test_a_successful_extraction_has_no_reason(self, monitor):
        client, tmp_path = monitor
        client.evals.get_evaluation_run.return_value = _evaluation_run(
            "SUCCEEDED", {_avg("safety_v1"): 0.9}
        )

        om.run_quick_eval("eng-1", num_cases=1)

        assert self._reason(tmp_path) == ""


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
