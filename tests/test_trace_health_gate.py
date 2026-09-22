"""`wrangler evaluators trace-health` is a gate, so its exit code is load-bearing.

Online evaluators score OTel traces. A dropped span batch is missing *input* to the scorer,
and the scorer cannot tell "the agent did not do this" from "the export never arrived" -- it
fails toward a lower score, and it correlates with load, so the busiest runs lose the most
evidence (silent-failures #8). The export timeouts were fixed; nothing *detected* them, which
is why they survived so long.

This module is the detector, and it was at **28%**. The parts that were untested are exactly
the parts a gate is judged on: whether it exits non-zero on a real problem, and whether it
stays up long enough to report one.

Everything here drives the real functions with `requests.post` faked. `time.sleep` is patched
throughout -- the backoff is 5+10+15 s, and a test that actually waited would be 30 s of
nothing.
"""

from __future__ import annotations

from unittest import mock

import pytest

from wrangler.eval import online_evaluators as oe


def _response(status: int, entries: list | None = None):
    resp = mock.MagicMock()
    resp.status_code = status
    resp.json.return_value = {"entries": entries or []}
    return resp


@pytest.fixture(autouse=True)
def _no_sleeping():
    """The backoff is real seconds; no test should pay them."""
    with mock.patch.object(oe.time, "sleep"):
        yield


@pytest.fixture(autouse=True)
def _no_auth():
    with mock.patch.object(oe, "_get_headers", return_value={"Authorization": "Bearer x"}):
        yield


# ── count_span_export_errors ──────────────────────────────────────


class TestCountingDroppedBatches:
    def test_a_clean_window_reports_zero_and_no_error(self):
        with mock.patch.object(oe.http_requests, "post", return_value=_response(200, [])):
            result = oe.count_span_export_errors("eng-1", minutes=30)

        assert result["dropped_batches"] == 0
        assert result["error"] == ""
        assert result["window_minutes"] == 30

    def test_each_log_entry_is_one_dropped_batch(self):
        with mock.patch.object(oe.http_requests, "post", return_value=_response(200, [{}] * 7)):
            result = oe.count_span_export_errors("eng-1")

        assert result["dropped_batches"] == 7
        assert result["truncated"] is False

    def test_a_full_page_is_flagged_as_truncated(self):
        """`pageSize` is 200. At exactly 200 the real count is "200 or more", and reporting
        it as a flat 200 understates a run that lost far more."""
        with mock.patch.object(oe.http_requests, "post", return_value=_response(200, [{}] * 200)):
            result = oe.count_span_export_errors("eng-1")

        assert result["truncated"] is True

    def test_the_query_is_scoped_to_the_engine_and_the_window(self):
        """A filter that lost its engine clause would report another engine's drops against
        this one -- and still look like a working check."""
        with mock.patch.object(oe.http_requests, "post", return_value=_response(200)) as post:
            oe.count_span_export_errors("eng-42", minutes=15)

        sent = post.call_args.kwargs["json"]["filter"]
        assert 'reasoning_engine_id="eng-42"' in sent
        assert oe._SPAN_DROP_MARKER in sent
        assert "timestamp>=" in sent


class TestTheDetectorStaysUpUnderLoad:
    """ "A monitoring tool that dies under load reports nothing, which is the exact failure
    it exists to catch." The Logging API rate-limits at five engines in quick succession,
    and an unhandled 429 once took the whole health check down mid-run."""

    def test_a_rate_limit_is_retried_before_giving_up(self):
        post = mock.Mock(side_effect=[_response(429), _response(429), _response(200, [{}])])
        with mock.patch.object(oe.http_requests, "post", post):
            result = oe.count_span_export_errors("eng-1")

        assert post.call_count == 3
        assert result["dropped_batches"] == 1
        assert result["error"] == ""

    def test_a_persistent_rate_limit_gives_up_on_the_engine_not_the_run(self):
        """It must return, not raise: one unreadable engine should not abort a check
        covering four healthy ones."""
        with mock.patch.object(oe.http_requests, "post", return_value=_response(429)) as post:
            result = oe.count_span_export_errors("eng-1")

        assert post.call_count == 4, "four attempts, then give up"
        assert result["error"] == "HTTP 429"

    def test_an_unreadable_engine_is_never_reported_as_clean(self):
        """The dangerous failure. `dropped_batches: 0` with no `error` means "measured, and
        fine"; this path has not measured anything, so the error field is what stops a
        caller reading it as a pass."""
        with mock.patch.object(oe.http_requests, "post", return_value=_response(503)):
            result = oe.count_span_export_errors("eng-1")

        assert result["error"] == "HTTP 503"
        assert result["dropped_batches"] == 0, "zero here means unknown, not clean"


# ── trace_health: the gate itself ─────────────────────────────────


def _with_engines(engines: dict[str, str]):
    return mock.patch.object(oe, "_get_agent_engine_ids", return_value=engines)


class TestTheGateExitsOnRealTroubleOnly:
    def test_dropped_batches_exit_non_zero(self, capsys):
        """This is the whole point: the command can gate a run rather than merely inform
        one. If it exits 0 on a degraded engine, the run proceeds on scores that are a
        lower bound rather than a measurement."""
        with (
            _with_engines({"sonnet": "eng-1"}),
            mock.patch.object(
                oe,
                "count_span_export_errors",
                return_value={"dropped_batches": 12, "truncated": False, "error": ""},
            ),
            pytest.raises(SystemExit) as exc,
        ):
            oe.trace_health([])

        assert exc.value.code == 1
        assert "FAIL" in capsys.readouterr().out

    def test_a_clean_sweep_exits_zero(self, capsys):
        with (
            _with_engines({"sonnet": "eng-1", "flash": "eng-2"}),
            mock.patch.object(
                oe,
                "count_span_export_errors",
                return_value={"dropped_batches": 0, "truncated": False, "error": ""},
            ),
        ):
            oe.trace_health([])

        assert "PASS" in capsys.readouterr().out

    def test_one_degraded_engine_among_healthy_ones_still_fails(self):
        """The gate is an AND across engines; a campaign is only as measurable as its worst
        arm."""
        results = {
            "eng-1": {"dropped_batches": 0, "truncated": False, "error": ""},
            "eng-2": {"dropped_batches": 3, "truncated": False, "error": ""},
        }
        with (
            _with_engines({"a": "eng-1", "b": "eng-2"}),
            mock.patch.object(oe, "count_span_export_errors", side_effect=lambda e, m: results[e]),
            pytest.raises(SystemExit) as exc,
        ):
            oe.trace_health([])

        assert exc.value.code == 1

    def test_no_engines_configured_is_not_a_failure(self, capsys):
        """Engine ids are supplied per run and never pinned, so "none set" is the normal
        state outside a campaign -- exiting 1 here would fail every CI run."""
        with _with_engines({}):
            oe.trace_health([])

        assert "nothing to check" in capsys.readouterr().out

    def test_the_window_comes_from_the_argument(self):
        with (
            _with_engines({"sonnet": "eng-1"}),
            mock.patch.object(
                oe,
                "count_span_export_errors",
                return_value={"dropped_batches": 0, "truncated": False, "error": ""},
            ) as count,
        ):
            oe.trace_health(["15"])

        assert count.call_args.args[1] == 15


def _unknown(engine_ids=("eng-1",)):
    """Every engine reports an error, i.e. health could not be measured."""
    return (
        _with_engines({f"a{i}": e for i, e in enumerate(engine_ids)}),
        mock.patch.object(
            oe,
            "count_span_export_errors",
            return_value={"dropped_batches": 0, "truncated": False, "error": "HTTP 429"},
        ),
    )


class TestAnUnmeasurableEngineFailsTheGateToo:
    """**A check that cannot see is not a check that passed.**

    Until 2026-09-22 an unreadable engine exited **0**: the printed text said "Unknown is not
    clean" while the exit code said the opposite, and the exit code is what a gate is made
    of. A Logging API outage was indistinguishable from a clean sweep to any `&&`.

    It now exits **2** -- distinct from the 1 a confirmed drop gives, so `&&` blocks on
    either while a caller who wants to tell them apart still can.
    """

    def test_an_unreadable_engine_exits_two(self, capsys):
        engines, counter = _unknown()
        with engines, counter, pytest.raises(SystemExit) as exc:
            oe.trace_health([])

        assert exc.value.code == 2
        out = capsys.readouterr().out
        assert "UNKNOWN" in out
        assert "unverified rather than clean" in out

    def test_an_exception_rather_than_an_error_field_also_exits_two(self):
        """The two unreadable paths -- a non-200 recorded in `error`, and a raised
        exception -- must reach the same verdict."""
        with (
            _with_engines({"sonnet": "eng-1"}),
            mock.patch.object(
                oe, "count_span_export_errors", side_effect=RuntimeError("connection reset")
            ),
            pytest.raises(SystemExit) as exc,
        ):
            oe.trace_health([])

        assert exc.value.code == 2

    def test_a_confirmed_drop_outranks_an_unmeasured_engine(self):
        """Both conditions at once exits **1**, not 2: a problem you can see is worse news
        than one you cannot, and the caller should read the more specific code."""
        results = {
            "eng-1": {"dropped_batches": 0, "truncated": False, "error": "HTTP 429"},
            "eng-2": {"dropped_batches": 5, "truncated": False, "error": ""},
        }
        with (
            _with_engines({"a": "eng-1", "b": "eng-2"}),
            mock.patch.object(oe, "count_span_export_errors", side_effect=lambda e, m: results[e]),
            pytest.raises(SystemExit) as exc,
        ):
            oe.trace_health([])

        assert exc.value.code == 1

    def test_allow_unknown_restores_the_old_tolerance(self, capsys):
        """The original reasoning -- an outage should not block every campaign -- kept as an
        explicit decision at the call site rather than a silent default."""
        engines, counter = _unknown()
        with engines, counter:
            oe.trace_health(["--allow-unknown"])

        out = capsys.readouterr().out
        assert "PASS (--allow-unknown)" in out
        assert "1 unmeasured" in out, "the pass must still say what it could not measure"

    def test_allow_unknown_does_not_excuse_a_confirmed_drop(self):
        """The flag tolerates *not knowing*; it must not tolerate a measured failure."""
        with (
            _with_engines({"sonnet": "eng-1"}),
            mock.patch.object(
                oe,
                "count_span_export_errors",
                return_value={"dropped_batches": 9, "truncated": False, "error": ""},
            ),
            pytest.raises(SystemExit) as exc,
        ):
            oe.trace_health(["--allow-unknown"])

        assert exc.value.code == 1

    def test_a_flag_is_not_parsed_as_the_window(self):
        """`minutes` and the flags share one argument list. Reading `args[0]` blindly makes
        `trace-health --allow-unknown` die on `int("--allow-unknown")`."""
        with (
            _with_engines({"sonnet": "eng-1"}),
            mock.patch.object(
                oe,
                "count_span_export_errors",
                return_value={"dropped_batches": 0, "truncated": False, "error": ""},
            ) as count,
        ):
            oe.trace_health(["--allow-unknown"])

        assert count.call_args.args[1] == 60, "the default window, not a parse failure"

    def test_an_exception_reading_one_engine_does_not_abort_the_others(self, capsys):
        """A raised exception must be contained per engine, or the first bad engine hides
        the health of every engine after it."""
        results = {
            "eng-1": RuntimeError("connection reset"),
            "eng-2": {"dropped_batches": 0, "truncated": False, "error": ""},
        }

        def fake(engine_id, _minutes):
            value = results[engine_id]
            if isinstance(value, Exception):
                raise value
            return value

        with (
            _with_engines({"a": "eng-1", "b": "eng-2"}),
            mock.patch.object(oe, "count_span_export_errors", side_effect=fake),
            pytest.raises(SystemExit),
        ):
            oe.trace_health([])

        out = capsys.readouterr().out
        assert "UNKNOWN" in out
        assert "eng-2" in out, "the second engine must still be checked"


# ── the CLI wiring ────────────────────────────────────────────────


class TestTheExitCodeSurvivesTheCliWrapper:
    """The gate is invoked as `wrangler evaluators trace-health`, so the contract that
    matters is the process exit code, not `trace_health`'s internal `sys.exit`.

    `--minutes` and `--allow-unknown` share one argument list by the time they reach
    `trace_health`, which is how a flag can get parsed as the look-back window.
    """

    @pytest.mark.parametrize(
        ("argv", "result", "expected"),
        [
            ([], {"dropped_batches": 0, "error": ""}, 0),
            ([], {"dropped_batches": 4, "error": ""}, 1),
            ([], {"dropped_batches": 0, "error": "HTTP 429"}, 2),
            (["--allow-unknown"], {"dropped_batches": 0, "error": "HTTP 429"}, 0),
            (["--allow-unknown"], {"dropped_batches": 4, "error": ""}, 1),
            (["--minutes", "15"], {"dropped_batches": 0, "error": "HTTP 429"}, 2),
        ],
    )
    def test_the_process_exit_code(self, argv, result, expected):
        from click.testing import CliRunner

        from wrangler.cli import main

        with (
            _with_engines({"sonnet": "eng-1"}),
            mock.patch.object(
                oe, "count_span_export_errors", return_value={"truncated": False, **result}
            ),
        ):
            invoked = CliRunner().invoke(main, ["evaluators", "trace-health", *argv])

        assert invoked.exit_code == expected
