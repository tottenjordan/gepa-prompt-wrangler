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
        out = oe.orphaned_evaluators([_ev("1", "gone")], live_engine_ids=set())
        assert out[0]["engine_id"] == "gone"

    def test_an_evaluator_with_no_agent_resource_is_not_guessed_at(self):
        """Unattributable. Report it rather than delete it."""
        assert (
            oe.orphaned_evaluators([{"name": ".../onlineEvaluators/1"}], live_engine_ids=set())
            == []
        )

    def test_an_empty_live_set_does_not_orphan_everything(self):
        """A failed engine lookup must not read as 'every engine is deleted'."""
        out = oe.orphaned_evaluators([_ev("1", "a")], live_engine_ids=None)
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

        out = oe.prune_evaluators(
            [_ev("1", "a"), _ev("2", "b")],
            live_engine_ids=set(),
            delete_fn=_flaky,
            confirm=True,
        )
        assert seen == ["1", "2"]
        assert out["deleted"] == ["2"]
        assert "1" in out["failed"]


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
