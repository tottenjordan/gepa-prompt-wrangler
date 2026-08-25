"""Tests for engine classification and pruning.

80 Agent Engines accumulated unnoticed, going back to 2026-02-13, because
CLAUDE.md forbids pinning engine ids — so nothing in the repo names them and
nothing reaps them either.

The dangerous part is not the count. Only 48 of the 80 were labelled
`solution=promp-wrangler`, and three of the *unlabelled* ones were the busiest
engines in the project (8,576 / 3,101 / 2,397 requests in 30 days). An
age-based or name-based sweep would have deleted someone else's live work.

So classification is by evidence and every signal has a veto. These tests are
the policy; everything else in the module is I/O.
"""

import pytest

from wrangler.tools import engines


def _eng(
    eid="1",
    display_name="gepa-sonnet",
    labels=None,
    min_instances=None,
    create_time="2026-06-01T00:00:00",
):
    return {
        "id": eid,
        "display_name": display_name,
        "labels": {"solution": "promp-wrangler"} if labels is None else labels,
        "min_instances": min_instances,
        "create_time": create_time,
    }


def _classify(engine, traffic=0, referenced=False):
    return engines.classify(engine, traffic=traffic, referenced=referenced)


class TestOwnershipVeto:
    """No label saying it is ours means it is not ours to delete.

    This is the rule that would have saved `coordinator_agent_jt1` and friends:
    unlabelled, and among the busiest engines in the project.
    """

    def test_an_unlabelled_engine_is_never_deletable(self):
        out = _classify(_eng(labels={}))
        assert out["deletable"] is False
        assert "label" in out["reason"].lower()

    def test_another_solutions_engine_is_never_deletable(self):
        out = _classify(_eng(labels={"solution": "geap-tour"}))
        assert out["deletable"] is False

    def test_an_unlabelled_engine_stays_protected_even_when_idle_and_ancient(self):
        out = _classify(_eng(labels={}, create_time="2026-02-13T00:00:00"), traffic=0)
        assert out["deletable"] is False

    def test_an_engine_labelled_ours_and_otherwise_clear_is_deletable(self):
        assert _classify(_eng())["deletable"] is True


class TestTrafficVeto:
    def test_any_traffic_protects(self):
        out = _classify(_eng(), traffic=1)
        assert out["deletable"] is False
        assert "traffic" in out["reason"].lower()

    def test_zero_traffic_does_not_protect_on_its_own(self):
        assert _classify(_eng(), traffic=0)["deletable"] is True


class TestReferenceVeto:
    def test_a_referenced_engine_is_protected(self):
        out = _classify(_eng(), referenced=True)
        assert out["deletable"] is False
        assert "referenc" in out["reason"].lower()


class TestWarmthVeto:
    """Warm *and* referenced means someone is deliberately keeping it hot."""

    def test_warm_and_referenced_is_protected(self):
        assert _classify(_eng(min_instances=2), referenced=True)["deletable"] is False

    def test_warm_but_unreferenced_and_idle_is_still_deletable(self):
        """Otherwise the probe engines — the whole point — could never be reaped."""
        assert _classify(_eng(min_instances=2))["deletable"] is True


class TestLegacyException:
    """Five engines predate the labelling convention. Named, not pattern-matched."""

    def test_a_legacy_listed_engine_is_deletable_despite_no_label(self):
        eid = next(iter(engines.LEGACY_UNLABELLED))
        assert _classify(_eng(eid=eid, labels={}))["deletable"] is True

    def test_a_legacy_listed_engine_still_obeys_the_other_vetoes(self):
        eid = next(iter(engines.LEGACY_UNLABELLED))
        assert _classify(_eng(eid=eid, labels={}), traffic=5)["deletable"] is False

    def test_every_legacy_entry_carries_its_reason(self):
        """Extending this list must be a visible, argued diff."""
        for eid, reason in engines.LEGACY_UNLABELLED.items():
            assert eid.isdigit(), eid
            assert len(reason) > 20, f"{eid} needs a real reason, got {reason!r}"

    def test_the_geap_tour_lookalike_is_not_on_the_list(self):
        """`sonnet_agent` uses the underscore style of the geap-tour set."""
        assert "8467456143491334144" not in engines.LEGACY_UNLABELLED


class TestEphemeralWaivesTheTrafficVeto:
    """Probe traffic is traffic we generated ourselves, measuring the engine.

    The first run of this policy kept all 14 probe engines, protected by
    "traffic in window" — 100-244 requests each, every one of them sent by the
    probe. Self-generated load is not evidence that anybody depends on the
    engine, so an engine that declares itself scratch waives that veto.

    Every other veto still applies: an ephemeral engine that somehow became
    referenced, or that is not ours, is still protected.
    """

    def test_probe_traffic_does_not_protect_an_ephemeral_engine(self):
        out = _classify(
            _eng(labels={"solution": "promp-wrangler", "lifecycle": "ephemeral"}), traffic=244
        )
        assert out["deletable"] is True
        assert "ephemeral" in out["reason"]

    def test_traffic_still_protects_a_normal_engine(self):
        assert _classify(_eng(), traffic=244)["deletable"] is False

    def test_an_ephemeral_engine_that_is_referenced_is_still_protected(self):
        out = _classify(
            _eng(labels={"solution": "promp-wrangler", "lifecycle": "ephemeral"}),
            traffic=244,
            referenced=True,
        )
        assert out["deletable"] is False

    def test_an_ephemeral_label_on_someone_elses_engine_grants_nothing(self):
        out = _classify(_eng(labels={"solution": "geap-tour", "lifecycle": "ephemeral"}))
        assert out["deletable"] is False

    def test_the_pre_label_probe_engines_are_listed_explicitly(self):
        """These predate the lifecycle label, so they are named rather than matched."""
        assert len(engines.EPHEMERAL_PRE_LABEL) == 14
        for eid, reason in engines.EPHEMERAL_PRE_LABEL.items():
            assert eid.isdigit(), eid
            assert "campaign" in reason.lower() or "2x2" in reason.lower(), reason

    def test_a_pre_label_probe_engine_is_deletable_despite_probe_traffic(self):
        eid = next(iter(engines.EPHEMERAL_PRE_LABEL))
        assert _classify(_eng(eid=eid), traffic=244)["deletable"] is True


class TestDisposition:
    def test_engines_are_grouped_for_review(self):
        rows = [
            _eng(eid="1"),
            _eng(eid="2", labels={}),
            _eng(eid="3"),
        ]
        plan = engines.plan_prune(rows, traffic={"3": 40}, referenced=set())
        assert [e["id"] for e in plan["delete"]] == ["1"]
        assert {e["id"] for e in plan["keep"]} == {"2", "3"}

    def test_the_plan_counts_warm_instances_freed(self):
        rows = [_eng(eid="1", min_instances=2), _eng(eid="2", min_instances=2, labels={})]
        plan = engines.plan_prune(rows, traffic={}, referenced=set())
        assert plan["warm_freed"] == 2

    def test_every_kept_engine_says_why_it_was_kept(self):
        rows = [_eng(eid="2", labels={}), _eng(eid="3")]
        plan = engines.plan_prune(rows, traffic={"3": 5}, referenced=set())
        for e in plan["keep"]:
            assert e["reason"]


class TestReferencedIds:
    def test_only_engine_id_keyed_values_are_matched(self, tmp_path):
        """A bare number match picked up 728 false hits from result files."""
        f = tmp_path / ".env"
        f.write_text(
            "SONNET_ENGINE_ID=3411962152116813824\n"
            "SOME_TOTAL=999888777666555444\n"
            '  "score": 123456789012345678\n'
        )
        found = engines.referenced_ids([str(f)])
        assert "3411962152116813824" in found
        assert "999888777666555444" not in found
        assert "123456789012345678" not in found

    def test_yaml_and_json_engine_id_keys_are_matched(self, tmp_path):
        f = tmp_path / "m.yaml"
        f.write_text('engine_id: 111111111111111111\n"engine_id": "222222222222222222"\n')
        found = engines.referenced_ids([str(f)])
        assert found == {"111111111111111111", "222222222222222222"}

    def test_a_missing_path_is_not_an_error(self):
        assert engines.referenced_ids(["/nonexistent/nope.env"]) == set()


class TestPruneIsDryByDefault:
    def test_dry_run_deletes_nothing(self):
        deleted = []
        rows = [_eng(eid="1"), _eng(eid="2")]
        engines.execute_prune(
            engines.plan_prune(rows, traffic={}, referenced=set()),
            delete_fn=deleted.append,
            confirm=False,
        )
        assert deleted == []

    def test_confirm_executes(self):
        deleted = []
        rows = [_eng(eid="1"), _eng(eid="2")]
        engines.execute_prune(
            engines.plan_prune(rows, traffic={}, referenced=set()),
            delete_fn=deleted.append,
            confirm=True,
        )
        assert deleted == ["1", "2"]

    def test_a_failed_delete_does_not_abort_the_batch(self):
        """Partial progress must be legible, not lost to the first error."""
        seen = []

        def _flaky(eid):
            seen.append(eid)
            if eid == "1":
                raise RuntimeError("boom")

        rows = [_eng(eid="1"), _eng(eid="2")]
        out = engines.execute_prune(
            engines.plan_prune(rows, traffic={}, referenced=set()),
            delete_fn=_flaky,
            confirm=True,
        )
        assert seen == ["1", "2"]
        assert out["deleted"] == ["2"]
        assert "1" in out["failed"]


class TestThrottling:
    """Agent Engine enforces a per-minute write quota per region.

    The first real teardown deleted 11 of 42 and then took 31 consecutive
    429s. Continue-on-failure meant the 11 were kept rather than lost, but a
    tool that reliably fails three quarters of its work is not finished.
    """

    def test_deletes_are_paced(self):
        naps = []
        rows = [_eng(eid=str(i)) for i in range(3)]
        engines.execute_prune(
            engines.plan_prune(rows, traffic={}, referenced=set()),
            delete_fn=lambda _e: None,
            confirm=True,
            pause=2.0,
            sleep_fn=naps.append,
        )
        # Paced between deletes, not before the first one.
        assert naps == [2.0, 2.0]

    def test_a_rate_limited_delete_is_retried(self):
        attempts = []

        def _throttled(eid):
            attempts.append(eid)
            if len(attempts) < 3:
                raise RuntimeError("429 Quota exceeded for quota metric ...")

        out = engines.execute_prune(
            engines.plan_prune([_eng(eid="1")], traffic={}, referenced=set()),
            delete_fn=_throttled,
            confirm=True,
            pause=0,
            sleep_fn=lambda _s: None,
        )
        assert out["deleted"] == ["1"]
        assert len(attempts) == 3

    def test_a_non_quota_error_is_not_retried(self):
        """Retrying a permission error just wastes the quota budget."""
        attempts = []

        def _boom(eid):
            attempts.append(eid)
            raise RuntimeError("PermissionDenied: 403")

        out = engines.execute_prune(
            engines.plan_prune([_eng(eid="1")], traffic={}, referenced=set()),
            delete_fn=_boom,
            confirm=True,
            pause=0,
            sleep_fn=lambda _s: None,
        )
        assert len(attempts) == 1
        assert "1" in out["failed"]


class TestProtectedSetRegression:
    """The engines that would actually hurt. Named, so a policy change trips here."""

    @pytest.mark.parametrize(
        ("eid", "name", "labels", "traffic"),
        [
            ("3639024497392091136", "coordinator_agent_jt1", {}, 8576),
            ("4380288848559603712", "coordinator_agent", {}, 3101),
            ("6134089059699523584", "router_agent_jt1", {}, 2397),
            ("3508578437872746496", "opus_agent_jt1", {"solution": "geap-tour"}, 0),
            ("8467456143491334144", "sonnet_agent", {}, 0),
        ],
    )
    def test_never_deletable(self, eid, name, labels, traffic):
        out = _classify(_eng(eid=eid, display_name=name, labels=labels), traffic=traffic)
        assert out["deletable"] is False, f"{name} must never be deletable"

    def test_the_referenced_v4_set_is_protected(self):
        out = _classify(
            _eng(
                eid="3411962152116813824", display_name="wrangler-sonnet-agent-v4", min_instances=2
            ),
            traffic=0,
            referenced=True,
        )
        assert out["deletable"] is False
