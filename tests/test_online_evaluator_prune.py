"""Tests for pruning online evaluators whose target engine is gone.

Online evaluators score OTel traces every 10 minutes. When the engine they
point at is deleted the evaluator stays ACTIVE and keeps running against
nothing — 10 of 28 were in that state on 2026-08-31, while the five live,
warm, actively-used v4 engines had **no** evaluator at all.

`cleanup()` could not fix this: it only deletes evaluators whose agent is
listed in `.env`, and a deleted engine is exactly the thing that is not.

Same shape as the engine policy: judged on evidence, dry-run by default.
"""

from wrangler.eval import online_evaluators as oe


def _ev(eid, agent, state="ACTIVE"):
    return {
        "name": f"projects/1/locations/us-central1/onlineEvaluators/{eid}",
        "agentResource": f"projects/1/locations/us-central1/reasoningEngines/{agent}",
        "state": state,
        "displayName": f"eval-{agent}",
    }


class TestOrphanDetection:
    def test_an_evaluator_for_a_deleted_engine_is_orphaned(self):
        out = oe.orphaned_evaluators([_ev("1", "gone")], live_engine_ids={"here"})
        assert [e["evaluator_id"] for e in out] == ["1"]

    def test_an_evaluator_for_a_live_engine_is_kept(self):
        assert oe.orphaned_evaluators([_ev("1", "here")], live_engine_ids={"here"}) == []

    def test_the_orphan_carries_the_engine_it_pointed_at(self):
        # live_engine_ids must be non-empty here: an *empty* set combined with
        # a non-empty evaluators list is the ambiguous "could not list
        # engines" shape the refusal guard below exists to catch, and it
        # would swallow this orphan rather than report it.
        out = oe.orphaned_evaluators([_ev("1", "gone")], live_engine_ids={"unrelated"})
        assert out[0]["engine_id"] == "gone"

    def test_an_evaluator_with_no_agent_resource_is_not_guessed_at(self):
        """Unattributable. Report it rather than delete it."""
        assert (
            oe.orphaned_evaluators(
                [{"name": ".../onlineEvaluators/1"}], live_engine_ids={"unrelated"}
            )
            == []
        )

    def test_an_empty_live_set_does_not_orphan_everything(self):
        """A failed engine lookup must not read as 'every engine is deleted'."""
        out = oe.orphaned_evaluators([_ev("1", "a")], live_engine_ids=None)
        assert out == []

    def test_an_empty_live_set_with_no_evaluators_is_not_refused(self):
        """A genuinely empty project has nothing for the guard to protect."""
        assert oe.orphaned_evaluators([], live_engine_ids=set()) == []

    def test_an_empty_live_set_with_evaluators_present_refuses_everything(self):
        """B1: the only caller does ``{r["id"] for r in list_engines()}``, which

        is never None -- it is empty on a wrong/unset GCP_REGION, a
        credentials scope problem, or evaluators pointing at engines in
        another location. Reading that as "every engine is gone" is what let
        `prune_evaluators(..., yes=True)` delete 28 evaluators' worth of
        engines it could simply not see.
        """
        out = oe.orphaned_evaluators([_ev("1", "a"), _ev("2", "b")], live_engine_ids=set())
        assert out == []


class TestPruneIsDryByDefault:
    def test_dry_run_deletes_nothing(self):
        deleted = []
        oe.prune_evaluators(
            [_ev("1", "gone")], live_engine_ids=set(), delete_fn=deleted.append, confirm=False
        )
        assert deleted == []

    def test_confirm_deletes_only_orphans(self):
        deleted = []
        oe.prune_evaluators(
            [_ev("1", "gone"), _ev("2", "here")],
            live_engine_ids={"here"},
            delete_fn=deleted.append,
            confirm=True,
        )
        assert deleted == ["1"]

    def test_a_failed_delete_does_not_abort_the_batch(self):
        seen = []

        def _flaky(eid):
            seen.append(eid)
            if eid == "1":
                raise RuntimeError("boom")

        # live_engine_ids non-empty and disjoint from {"a", "b"}: both are
        # orphans, but an *empty* live set here would trip the B1 refusal
        # guard instead and this test would stop exercising the retry path.
        out = oe.prune_evaluators(
            [_ev("1", "a"), _ev("2", "b")],
            live_engine_ids={"unrelated"},
            delete_fn=_flaky,
            confirm=True,
        )
        assert seen == ["1", "2"]
        assert out["deleted"] == ["2"]
        assert "1" in out["failed"]

    def test_an_empty_listing_with_evaluators_present_is_refused_not_deleted(self):
        """B1: this combination means 'we could not see the engines'.

        28 evaluators were deleted this way on 2026-08-31 -- ``list_engines()``
        returning empty (wrong/unset GCP_REGION, a credentials scope problem)
        was indistinguishable from every engine being gone, and
        ``prune_evaluators(..., yes=True)`` deleted all of them.
        """
        deleted = []
        out = oe.prune_evaluators(
            [_ev("1", "a"), _ev("2", "b")],
            live_engine_ids=set(),
            delete_fn=deleted.append,
            confirm=True,
        )
        assert deleted == []
        assert out["orphans"] == []
        assert out["refused_reason"], "the caller must be told why nothing was deleted"


class TestCoverageReport:
    """The other half: which live engines have no evaluator watching them."""

    def test_an_unwatched_engine_is_reported(self):
        out = oe.evaluator_coverage(
            [_ev("1", "agent-a")], engine_ids={"a": "agent-a", "b": "agent-b"}
        )
        assert out["unwatched"] == {"b": "agent-b"}
        assert out["watched"] == {"a": "agent-a"}

    def test_full_coverage_reports_nothing_unwatched(self):
        out = oe.evaluator_coverage([_ev("1", "agent-a")], engine_ids={"a": "agent-a"})
        assert out["unwatched"] == {}


class TestTraceHealthSurvivesRateLimits:
    """A monitoring tool that dies under load reports nothing.

    Reading span health for five engines in quick succession rate-limited the
    Logging API, and the unhandled 429 aborted the whole check mid-run on
    2026-08-31 — two engines never got looked at, and the output gave no hint
    that they hadn't.
    """

    def test_a_rate_limit_is_retried_then_reported_as_unknown(self, monkeypatch):
        calls = []

        class _Resp:
            status_code = 429
            text = "rate limited"

            def json(self):  # pragma: no cover - never reached on 429
                return {}

        monkeypatch.setattr(oe, "_get_headers", dict)
        monkeypatch.setattr(oe.time, "sleep", lambda _s: None)
        monkeypatch.setattr(oe.http_requests, "post", lambda *a, **k: calls.append(1) or _Resp())
        out = oe.count_span_export_errors("eng1", 60)
        assert len(calls) == 4, "should back off and retry before giving up"
        assert out["error"] == "HTTP 429"

    def test_unknown_is_not_reported_as_clean(self, monkeypatch):
        """Zero dropped batches and 'we could not look' are different claims."""

        class _Resp:
            status_code = 429
            text = ""

            def json(self):  # pragma: no cover
                return {}

        monkeypatch.setattr(oe, "_get_headers", dict)
        monkeypatch.setattr(oe.time, "sleep", lambda _s: None)
        monkeypatch.setattr(oe.http_requests, "post", lambda *a, **k: _Resp())
        out = oe.count_span_export_errors("eng1", 60)
        assert out["dropped_batches"] == 0
        assert out["error"], "must carry an error so the caller cannot read it as clean"
