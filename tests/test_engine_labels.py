"""Pipeline-deployed engines must carry the labels that make them reapable.

CLAUDE.md's teardown policy says to deploy scratch engines with
`labels={"lifecycle": "ephemeral", "campaign": "<id>"}` so a campaign's engines
can be found and reaped afterwards. `wrangler engines prune` already honours
that: `classify()` lets an ephemeral engine waive the traffic veto, precisely
because the only traffic a campaign engine sees is the traffic the campaign
sent it.

Only `scripts/deploy_probe_arms.py` ever applied those labels. Everything the
*pipeline* deploys got `{"solution": "promp-wrangler"}` and nothing else, so
campaign 06's four engines were unreapable by policy on 2026-09-08 -- labelled
ours, but with traffic and no lifecycle marker, which `classify()` reads as
"keep". That is how this project reached 80 engines before anyone counted.
"""

from __future__ import annotations

import glob

import yaml

from wrangler.core.factory import PairFactory
from wrangler.tools.engines import classify

OWNED = {"solution": "promp-wrangler"}


class TestClassifyAlreadyHandlesEphemeral:
    """The policy end works. Pinning it so the manifest end has a target."""

    def test_an_ephemeral_engine_with_traffic_is_still_deletable(self):
        row = {"id": "e1", "labels": {**OWNED, "lifecycle": "ephemeral", "campaign": "06"}}
        out = classify(row, traffic=128, referenced=False)
        assert out["deletable"] is True, out["reason"]

    def test_the_same_engine_without_the_label_is_protected(self):
        """Exactly campaign 06's four engines before this change."""
        row = {"id": "e1", "labels": dict(OWNED)}
        out = classify(row, traffic=128, referenced=False)
        assert out["deletable"] is False
        assert "traffic" in out["reason"]

    def test_ephemeral_does_not_override_a_reference(self):
        """A named engine is still someone's live deployment."""
        row = {"id": "e1", "labels": {**OWNED, "lifecycle": "ephemeral"}}
        assert classify(row, traffic=0, referenced=True)["deletable"] is False


class TestManifestsCarryTheLabels:
    def test_a_manifest_labels_block_is_parsed(self, tmp_path):
        path = tmp_path / "m.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "name": "x",
                    "agent_module": "a",
                    "eval_data": "e.yaml",
                    "labels": {"lifecycle": "ephemeral", "campaign": "99"},
                    "pairs": [{"id": "p", "model": "gemini-3.5-flash", "system_prompt": "s"}],
                }
            )
        )
        assert PairFactory.load(str(path)).labels == {
            "lifecycle": "ephemeral",
            "campaign": "99",
        }

    def test_labels_default_to_empty_not_none(self, tmp_path):
        path = tmp_path / "m.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "name": "x",
                    "agent_module": "a",
                    "eval_data": "e.yaml",
                    "pairs": [{"id": "p", "model": "gemini-3.5-flash", "system_prompt": "s"}],
                }
            )
        )
        assert PairFactory.load(str(path)).labels == {}

    def test_every_campaign_manifest_declares_itself_ephemeral(self):
        """A campaign that cannot be reaped is a campaign that accumulates.

        Teardown is the last step of a campaign, not a later chore, and it can
        only happen if the engines are findable.
        """
        campaign_manifests = sorted(glob.glob("manifests/c0[6-9]-*_manifest.yaml"))
        assert campaign_manifests, "expected campaign manifests"
        for path in campaign_manifests:
            labels = PairFactory.load(path).labels
            assert labels.get("lifecycle") == "ephemeral", (
                f"{path} deploys engines that `wrangler engines prune` will refuse "
                f"to reap, because they carry traffic and no lifecycle marker"
            )
            assert labels.get("campaign"), f"{path} has no campaign label to find it by"
