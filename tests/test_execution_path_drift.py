"""The local and pipeline paths must honour the same manifest keys.

`orchestration/stages.py` (local, `wrangler run`) and `pipeline/components.py` (Vertex
Pipelines, `wrangler pipeline run`) drive the same six stages against the same primitives.
They are separate implementations, and **the divergence has already cost two bugs**:

- `manifest.enabled_pairs` was honoured locally and not in the pipeline, so a disabled pair
  still ran there.
- `skip_optimize` and `forward_rationale` were added as per-pair manifest keys on
  2026-09-17 for campaign 09, wired through the pipeline, and **never wired into the local
  path** -- so `wrangler run` silently ignored a control arm and ran GEPA on it anyway.

Both were found by reading, long after the fact. This test finds the next one.

**It compares FIELDS HONOURED, not source.** The two paths read a pair differently -- the
local path holds `AgentPromptPair` objects and uses attribute access, the pipeline serialises
to dicts and uses `pair["x"]` / `pair.get("x")` -- so a source or signature comparison would
be meaningless. What matters is whether a key a user writes in a manifest has an effect on
both paths.

Anything genuinely one-sided belongs in `PATH_SPECIFIC` **with a reason**. A silent exemption
is how the allowlist eventually swallows a real bug -- the same argument
`test_shared_source_drift.py` makes for comparing behaviour over source.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from wrangler.core.factory import AgentPromptPair

COMPONENTS = Path("wrangler/pipeline/components.py")
STAGES = Path("wrangler/orchestration/stages.py")

#: Pair fields that legitimately exist on one path only, each with the reason it is not a bug.
PATH_SPECIFIC: dict[str, str] = {
    # The pipeline estimates per-arm spend from the pair's cost table and writes it into the
    # stage artifact. The local path does not produce that artifact, so there is nothing for
    # it to populate.
    "costs": "pipeline-only: feeds the stage-artifact cost estimate, which local does not write",
    # Filtered BEFORE serialisation by `_pairs_json(manifest)`, which iterates
    # `manifest.enabled_pairs`. The pipeline therefore never sees a disabled pair and has no
    # reason to read the field off one. The local path filters at read time instead.
    "enabled": "local-only: the pipeline filters via manifest.enabled_pairs before serialising",
    "disabled_reason": "local-only: printed when a sweep skips a pair; same filtering split",
    # Parsed and carried for documentation; PairFactory.load rejects a temperature on models
    # that refuse sampling params, and neither execution path forwards it to a deployed agent.
    "temperature": "neither path forwards it; validated at parse time only",
    "tags": "neither path reads it; carried for manifest documentation",
}


def _dict_keys(path: Path, names: set[str]) -> set[str]:
    """String keys read off a dict-shaped pair: `pair["x"]` and `pair.get("x")`."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in names
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            found.add(node.args[0].value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in names
            and isinstance(node.slice, ast.Constant)
        ):
            found.add(node.slice.value)
    return {k for k in found if isinstance(k, str)}


def _attributes(path: Path, names: set[str]) -> set[str]:
    """Attributes read off an object-shaped pair: `pair.x`."""
    tree = ast.parse(path.read_text())
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in names
    }


def _pair_fields() -> set[str]:
    return {f.name for f in fields(AgentPromptPair)}


def pipeline_honours() -> set[str]:
    return _dict_keys(COMPONENTS, {"pair", "p", "entry"}) & _pair_fields()


def local_honours() -> set[str]:
    names = {"pair", "p"}
    return (_attributes(STAGES, names) | _dict_keys(STAGES, names)) & _pair_fields()


class TestBothPathsHonourTheSameManifestKeys:
    def test_no_field_is_silently_pipeline_only(self):
        missing = pipeline_honours() - local_honours() - set(PATH_SPECIFIC)
        assert not missing, (
            f"{sorted(missing)} affect the pipeline path but are ignored by "
            f"`wrangler run` (orchestration/stages.py). A manifest that sets one gets a "
            f"different experiment depending on how it is launched. Wire it into the local "
            f"path, or add it to PATH_SPECIFIC with the reason it cannot apply."
        )

    def test_no_field_is_silently_local_only(self):
        missing = local_honours() - pipeline_honours() - set(PATH_SPECIFIC)
        assert not missing, (
            f"{sorted(missing)} affect `wrangler run` but are ignored by the pipeline. "
            f"This is the direction the `enabled_pairs` bug went -- a disabled pair still "
            f"ran in the pipeline. Wire it in, or add it to PATH_SPECIFIC with a reason."
        )

    def test_the_allowlist_stays_honest(self):
        """An entry that names a field which no longer exists is rot, not an exemption."""
        unknown = set(PATH_SPECIFIC) - _pair_fields()
        assert not unknown, f"PATH_SPECIFIC names fields that are not on AgentPromptPair: {unknown}"

    @pytest.mark.parametrize("field_name", sorted(PATH_SPECIFIC))
    def test_every_exemption_carries_a_reason(self, field_name):
        reason = PATH_SPECIFIC[field_name]
        assert len(reason) > 30, (
            f"{field_name!r} is exempted with too short a reason to be checkable later"
        )


class TestTheKnownDivergencesStayFixed:
    """One regression test per bug this guard exists because of."""

    def test_both_paths_read_enabled_pairs(self):
        """The pipeline path did not filter, so a disabled pair still ran."""
        for path in (STAGES, Path("wrangler/pipeline/deploy_pipeline.py")):
            assert "enabled_pairs" in path.read_text(), (
                f"{path} no longer reads manifest.enabled_pairs, so a pair switched off with "
                f"`enabled: false` would run anyway"
            )

    def test_the_campaign_09_factors_reach_both_paths(self):
        """`skip_optimize` and `forward_rationale` were pipeline-only when introduced."""
        honoured = local_honours() & {"skip_optimize", "forward_rationale"}
        assert honoured == {"skip_optimize", "forward_rationale"}, (
            f"the local path honours {sorted(honoured)} of the two campaign-09 factors. "
            f"`wrangler run` on a manifest with a control arm would optimize it anyway, and "
            f"a rationale-off arm would silently run with ADK patch 4b enabled."
        )
