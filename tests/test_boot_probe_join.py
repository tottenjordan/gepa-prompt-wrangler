"""Tests for wrangler.tools.boot_probe_join — attaching each attempt to its worker.

The claim under test in the field is "the request was consumed by a worker that
had not finished booting". Until now that was inferred from PIDs co-occurring in
a time window, never from a per-request join, and the note carried an unverified
caveat that PIDs recur across containers and so cannot identify a worker at all.

This module does the join and *checks* the caveat rather than assuming it either
way.
"""

from datetime import UTC, datetime, timedelta

from wrangler.tools import boot_probe_join as bpj


def _ts(offset_s: float) -> datetime:
    return datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=offset_s)


def _iso(offset_s: float) -> str:
    return _ts(offset_s).isoformat()


def _entry(offset_s: float, pid: int, msg: str) -> bpj.LogEntry:
    return bpj.LogEntry(ts=_ts(offset_s), pid=pid, message=msg)


def _row(sent: float, finished: float, **kw) -> dict:
    row = {
        "arm": "mcp-claude",
        "engine_id": "eng1",
        "attempt_index": 0,
        "nonce": "abc123",
        "sent_at": _iso(sent),
        "finished_at": _iso(finished),
        "event_count": 0,
        "reached": False,
        "error": "",
    }
    row.update(kw)
    return row


class TestParseLogLine:
    def test_parses_the_gcloud_value_format(self):
        line = "2026-08-24T12:00:05.123456Z\t[3258]    INFO:     Application startup complete."
        entry = bpj.parse_log_line(line)
        assert entry is not None
        assert entry.pid == 3258
        assert "Application startup complete" in entry.message
        assert entry.ts.year == 2026

    def test_a_line_without_a_pid_prefix_is_skipped(self):
        assert bpj.parse_log_line("2026-08-24T12:00:05Z\tno pid here") is None

    def test_a_blank_line_is_skipped(self):
        assert bpj.parse_log_line("") is None
        assert bpj.parse_log_line("   ") is None


class TestPidReuse:
    """The caveat that weakened every previous PID argument, now checkable.

    A PID is unique within a container, not across them. If two containers in
    one window both used PID 19, that PID names two different workers and the
    join is meaningless. Measured on 2026-08-23 the reuse count was zero -- but
    that is a property of a window, not of the system, so it is asserted per
    run rather than assumed.
    """

    def test_no_reuse_when_each_pid_starts_once(self):
        entries = [
            _entry(0, 19, "Started server process [19]"),
            _entry(1, 3258, "Started server process [3258]"),
        ]
        assert bpj.pid_reuse_count(entries) == 0

    def test_reuse_is_counted_when_a_pid_starts_twice(self):
        entries = [
            _entry(0, 19, "Started server process [19]"),
            _entry(90, 19, "Started server process [19]"),
            _entry(1, 3258, "Started server process [3258]"),
        ]
        assert bpj.pid_reuse_count(entries) == 1


class TestWorkerTimelines:
    """A PID maps to a *list* of incarnations, one per `Started server process`."""

    def test_first_seen_and_startup_complete(self):
        entries = [
            _entry(0, 100, "Started server process [100]"),
            _entry(3, 100, "Application startup complete."),
            _entry(9, 100, "MCP summary: 3 OK, 0 failed"),
        ]
        workers = bpj.build_worker_timelines(entries)
        assert len(workers[100]) == 1
        assert workers[100][0].first_seen == _ts(0)
        assert workers[100][0].startup_complete == _ts(3)

    def test_a_worker_that_never_finished_booting_has_no_completion(self):
        workers = bpj.build_worker_timelines([_entry(0, 100, "Started server process [100]")])
        assert workers[100][0].startup_complete is None

    def test_a_restart_opens_a_second_incarnation(self):
        entries = [
            _entry(0, 19, "Started server process [19]"),
            _entry(100, 19, "Started server process [19]"),
        ]
        assert len(bpj.build_worker_timelines(entries)[19]) == 2


class TestJoin:
    def test_a_single_request_in_the_window_joins(self):
        entries = [
            _entry(0, 100, "Started server process [100]"),
            _entry(8, 100, "Application startup complete."),
            _entry(10, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
        ]
        joined = bpj.join_rows([_row(9, 14)], entries)
        assert joined[0]["joinable"] is True
        assert joined[0]["serving_pid"] == 100

    def test_worker_age_is_measured_from_the_workers_first_log_line(self):
        entries = [
            _entry(0, 100, "Started server process [100]"),
            _entry(4, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
        ]
        joined = bpj.join_rows([_row(3, 9)], entries)
        assert joined[0]["worker_age_s"] == 4.0

    def test_a_request_served_before_startup_completed_is_flagged(self):
        """This is the defect, stated as data rather than as a narrative."""
        entries = [
            _entry(0, 100, "Started server process [100]"),
            _entry(4, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
            _entry(9, 100, "Application startup complete."),
        ]
        joined = bpj.join_rows([_row(3, 8)], entries)
        assert joined[0]["booted_before_request"] is False

    def test_a_request_served_after_startup_completed_is_flagged(self):
        entries = [
            _entry(0, 100, "Started server process [100]"),
            _entry(3, 100, "Application startup complete."),
            _entry(20, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
        ]
        joined = bpj.join_rows([_row(19, 25)], entries)
        assert joined[0]["booted_before_request"] is True

    def test_no_matching_log_line_is_unjoinable_with_a_reason(self):
        """Unjoinable rows must be reported, not dropped -- that is the whole file."""
        joined = bpj.join_rows([_row(3, 8)], [_entry(0, 100, "Started server process [100]")])
        assert joined[0]["joinable"] is False
        assert joined[0]["serving_pid"] is None
        assert "no" in joined[0]["join_note"].lower()

    def test_two_matching_log_lines_are_unjoinable(self):
        """Serialization is meant to prevent this; if it happens, say so."""
        entries = [
            _entry(4, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
            _entry(5, 200, '10.0.0.1:2 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
        ]
        joined = bpj.join_rows([_row(3, 8)], entries)
        assert joined[0]["joinable"] is False
        assert "2" in joined[0]["join_note"]

    def test_model_reached_is_detected_after_the_request(self):
        entries = [
            _entry(0, 100, "Started server process [100]"),
            _entry(3, 100, "Application startup complete."),
            _entry(10, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
            _entry(12, 100, "Received response from Claude."),
        ]
        joined = bpj.join_rows([_row(9, 14)], entries)
        assert joined[0]["reached_model"] is True

    def test_a_model_response_before_the_request_does_not_count(self):
        """Otherwise a warm worker's previous request credits this one."""
        entries = [
            _entry(0, 100, "Started server process [100]"),
            _entry(2, 100, "Received response from Claude."),
            _entry(10, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
        ]
        joined = bpj.join_rows([_row(9, 14)], entries)
        assert joined[0]["reached_model"] is False

    def test_the_model_pattern_is_configurable(self):
        """Claude's log line is known; Gemini's must be read off a real deploy."""
        entries = [
            _entry(10, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
            _entry(12, 100, "HTTP Request: POST .../models/x:generateContent"),
        ]
        joined = bpj.join_rows([_row(9, 14)], entries, model_patterns=("generateContent",))
        assert joined[0]["reached_model"] is True


class TestDoseResponse:
    """P(empty) against worker age is the artifact the escalation stands on."""

    def test_bins_by_worker_age(self):
        rows = [
            {"joinable": True, "worker_age_s": 1.0, "reached": False},
            {"joinable": True, "worker_age_s": 2.0, "reached": False},
            {"joinable": True, "worker_age_s": 30.0, "reached": True},
            {"joinable": True, "worker_age_s": 45.0, "reached": True},
        ]
        curve = bpj.dose_response(rows, edges=(0, 5, 60))
        assert curve[0]["n"] == 2
        assert curve[0]["reach_rate"] == 0.0
        assert curve[1]["n"] == 2
        assert curve[1]["reach_rate"] == 1.0

    def test_unjoinable_rows_are_excluded_not_counted_as_zero(self):
        rows = [
            {"joinable": False, "worker_age_s": None, "reached": False},
            {"joinable": True, "worker_age_s": 1.0, "reached": True},
        ]
        curve = bpj.dose_response(rows, edges=(0, 5))
        assert curve[0]["n"] == 1
        assert curve[0]["reach_rate"] == 1.0

    def test_every_bin_carries_an_interval(self):
        rows = [{"joinable": True, "worker_age_s": 1.0, "reached": True}]
        curve = bpj.dose_response(rows, edges=(0, 5))
        assert 0.0 <= curve[0]["ci_low"] <= curve[0]["ci_high"] <= 1.0


class TestIncarnations:
    """PID reuse must not corrupt worker age.

    Measured 2026-08-23: a 30-minute window on a settled engine had zero reused
    PIDs, but the first hour of a freshly-deployed one had ten. Taking a PID's
    *first* log line as its birth therefore ages a recycled PID from the wrong
    container -- by minutes. Anchoring on the most recent `Started server
    process` at or before the request fixes it, and turns PID reuse from a
    disqualifier into a fact about the window.
    """

    def test_age_uses_the_most_recent_start_for_that_pid(self):
        entries = [
            _entry(0, 19, "Started server process [19]"),
            _entry(1, 19, "Application startup complete."),
            _entry(100, 19, "Started server process [19]"),
            _entry(101, 19, "Application startup complete."),
            _entry(104, 19, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
        ]
        joined = bpj.join_rows([_row(103, 108)], entries)
        assert joined[0]["worker_age_s"] == 4.0

    def test_boot_state_uses_the_matching_incarnation(self):
        """An earlier incarnation's completed boot must not vouch for a new one."""
        entries = [
            _entry(0, 19, "Started server process [19]"),
            _entry(1, 19, "Application startup complete."),
            _entry(100, 19, "Started server process [19]"),
            _entry(102, 19, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
        ]
        joined = bpj.join_rows([_row(101, 106)], entries)
        assert joined[0]["booted_before_request"] is False

    def test_a_worker_with_no_start_line_in_the_window_is_flagged(self):
        """It booted before the lead-in, so its age is a lower bound, not a fact."""
        entries = [
            _entry(0, 19, "Application startup complete."),
            _entry(10, 19, '10.0.0.1:1 - "POST /api/stream_reasoning_engine HTTP/1.1" 200 OK'),
        ]
        joined = bpj.join_rows([_row(9, 14)], entries)
        assert joined[0]["worker_age_is_lower_bound"] is True


class TestNonceJoin:
    """The authoritative per-request join, found 2026-08-23.

    GEAP emits a structured log stream (`reasoning_engine_stdout`) whose labels
    carry `user.id` and the full input/output messages. The probe puts its
    nonce in the user id, so "did this exact request reach the model" stops
    being an inference from co-occurring PIDs and becomes a lookup.

    Verified on the gate run: of three attempts, only the one that returned
    events appeared in the stream. The other two produced a 200 and no
    inference was ever performed for them.
    """

    def test_a_served_nonce_means_the_model_was_reached(self):
        entries = [_entry(10, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine" 200 OK')]
        joined = bpj.join_rows([_row(9, 14)], entries, served_nonces={"abc123"})
        assert joined[0]["reached_model"] is True
        assert joined[0]["model_join"] == "nonce"

    def test_an_absent_nonce_means_it_did_not(self):
        entries = [_entry(10, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine" 200 OK')]
        joined = bpj.join_rows([_row(9, 14)], entries, served_nonces=set())
        assert joined[0]["reached_model"] is False
        assert joined[0]["model_join"] == "nonce"

    def test_the_nonce_join_works_even_when_the_pid_join_fails(self):
        """The two are independent, which is the point of having both."""
        joined = bpj.join_rows([_row(9, 14)], [], served_nonces={"abc123"})
        assert joined[0]["joinable"] is False
        assert joined[0]["reached_model"] is True

    def test_the_nonce_is_extracted_from_a_logged_prompt(self):
        """user.id was the first choice and some engines do not emit it; the
        prompt text is emitted consistently."""
        payload = '[{"role":"user","parts":[{"content":"Reply with exactly the word OK. Probe id: f84b12ef88e7","type":"text"}]}]'
        assert bpj._NONCE_IN_PROMPT.findall(payload) == ["f84b12ef88e7"]

    def test_falls_back_to_log_patterns_when_no_user_ids_are_supplied(self):
        entries = [
            _entry(10, 100, '10.0.0.1:1 - "POST /api/stream_reasoning_engine" 200 OK'),
            _entry(12, 100, "Received response from Claude."),
        ]
        joined = bpj.join_rows([_row(9, 14)], entries)
        assert joined[0]["reached_model"] is True
        assert joined[0]["model_join"] == "log_pattern"


class TestJoinSummary:
    def test_reports_the_unjoinable_fraction(self):
        rows = [{"joinable": True}, {"joinable": True}, {"joinable": False}]
        summary = bpj.join_summary(rows, pid_reuse=0)
        assert summary["joined"] == 2
        assert summary["unjoinable"] == 1
        assert summary["join_rate"] == 2 / 3

    def test_pid_reuse_is_reported_but_no_longer_disqualifying(self):
        """Incarnations handle a recycled PID; the count stays visible as context."""
        summary = bpj.join_summary([{"joinable": True}], pid_reuse=3)
        assert summary["pid_reuse"] == 3
        assert summary["join_sound"] is True

    def test_a_lower_bound_age_makes_the_join_unsound(self):
        """Its start line fell outside the window, so the age is not a measurement."""
        summary = bpj.join_summary(
            [{"joinable": True, "worker_age_is_lower_bound": True}], pid_reuse=0
        )
        assert summary["join_sound"] is False
        assert summary["ages_lower_bound"] == 1

    def test_measured_ages_make_the_join_sound(self):
        summary = bpj.join_summary(
            [{"joinable": True, "worker_age_is_lower_bound": False}], pid_reuse=0
        )
        assert summary["join_sound"] is True
        assert summary["ages_measured"] == 1
