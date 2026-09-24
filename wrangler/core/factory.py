"""Agent-prompt pair factory — parses manifest YAML into executable pairs."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .models import MODELS

# The API default. Setting temperature to anything else is what trips the
# sampling-parameter deprecation; leaving it here is not.
DEFAULT_TEMPERATURE = 1.0


@dataclass
class AgentPromptPair:
    """A single model + system-prompt combination to evaluate."""

    id: str
    model: str
    system_prompt: str
    # Currently parsed and carried but not passed to any deployed agent. Kept
    # because manifests document it; see the validation in PairFactory.load.
    temperature: float = DEFAULT_TEMPERATURE
    description: str = ""
    tags: list[str] = field(default_factory=list)
    engine_id: str = ""
    agent_module: str = ""
    costs: dict[str, float] | None = None

    # A pair can be switched off without deleting it. Deleting loses the model
    # id, the agent module and the reason; commenting it out loses the reason
    # too, and a commented block rots. `enabled: false` with a reason keeps the
    # configuration honest and makes re-enabling a one-line diff.
    enabled: bool = True
    disabled_reason: str = ""
    # Campaign factors, per pair so one pipeline job can vary them BETWEEN arms.
    # dag.py forwards the whole pair dict as `pair_json`, so neither needs a DAG
    # signature change. Both default to today's behaviour: every existing manifest
    # omits them, and a pair silently arriving with forward_rationale=False would
    # disable ADK patch 4b for a campaign that never asked to.
    forward_rationale: bool = True
    skip_optimize: bool = False
    # Set when this pair was expanded from a `replicates: N` entry; holds the manifest
    # id the replicates share. Recorded rather than parsed back out of the suffixed id,
    # so grouping replicates of one condition never depends on a regex over ids that
    # users also choose by hand.
    replicate_of: str = ""
    # Stop GEPA after this many iterations with no improvement in the best validation
    # score. Resolved at load time from the pair, then `defaults:`, then `pipeline:`, so
    # a campaign sets it once but an arm can still differ -- which is how a stopping
    # rule gets validated against an unstopped arm in the same job. None = no stopper,
    # exactly as every run before 2026-09-24.
    patience: int | None = None
    # Score cases on continuous metric means rather than ADK's pass/fail collapse.
    # Resolved like `patience`: pair, then `defaults:`, then `pipeline:`. Off by default
    # because it changes what GEPA selects on, and so re-baselines a campaign.
    continuous_val_score: bool = False

    def summary(self) -> str:
        """One-line summary for display."""
        trunc = self.system_prompt[:60].replace("\n", " ")
        return f"[{self.id}] {self.model} | {trunc}..."


@dataclass
class Manifest:
    """Parsed manifest containing agent config and prompt pairs."""

    name: str
    description: str
    agent_module: str
    eval_data: str
    pairs: list[AgentPromptPair]
    eval_config: dict[str, Any] = field(default_factory=dict)
    # The manifest's `pipeline:` block. Parsed so the *local* path can read
    # settings that used to be reachable only from the KFP pipeline —
    # notably max_metric_calls, GEPA's search budget.
    pipeline: dict[str, Any] = field(default_factory=dict)

    # The manifest's `health_gate:` block. Roughly four in ten deployments come
    # up unable to serve and fail by returning 200 with no inference, so the
    # deploy stage probes and rerolls by default; this is how a manifest tunes
    # or disables that. See docs/notes/engine-lifecycle.md.
    health_gate: dict[str, Any] = field(default_factory=dict)

    # Extra GCP labels stamped onto every engine this manifest deploys, merged
    # over the standard {"solution": "promp-wrangler"}.
    #
    # This is how a campaign's engines stay reapable. `wrangler engines prune`
    # lets an engine labelled `lifecycle: ephemeral` waive the traffic veto,
    # because the only traffic a campaign engine ever sees is the traffic the
    # campaign sent it. Without the label a finished campaign's engines read as
    # "ours, but busy" and are kept forever -- which is how this project
    # reached 80 engines. Only the standalone probe script used to apply them,
    # so nothing the pipeline deployed was reapable.
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def pair_ids(self) -> list[str]:
        """Every declared pair's id, disabled ones included.

        Deliberately unfiltered: `get_pair()` below searches the same
        unfiltered `self.pairs`, so its KeyError's "Available" list needs to
        cover a disabled pair too, not just the ones a sweep would run.

        **This is not `Experiment.pair_ids`.** An `Experiment` wraps a
        `Manifest` and exposes a property of the same name with the opposite
        contract -- it filters disabled pairs out, because it answers a
        different question (what a report/gate/tracking entry should count,
        not what get_pair can look up). Conflating the two is exactly the
        trap the disabled-pairs work exists to remove; use `enabled_pairs`
        below when you mean "what a sweep runs."
        """
        return [p.id for p in self.pairs]

    @property
    def enabled_pairs(self) -> list[AgentPromptPair]:
        """Pairs a sweep should act on. Use this, not ``pairs``.

        ``pairs`` is everything the manifest declares, including entries
        switched off with ``enabled: false``. Reading it directly is how a
        disabled pair gets run anyway -- the local path filtered and the
        pipeline path did not, so `wrangler pipeline run` would still have
        deployed and evaluated opus after it was disabled everywhere else.
        """
        return [p for p in self.pairs if p.enabled]

    def get_pair(self, pair_id: str) -> AgentPromptPair:
        """Look up a pair by ID, raising KeyError if not found."""
        for p in self.pairs:
            if p.id == pair_id:
                return p
        raise KeyError(f"No pair with id={pair_id!r}. Available: {self.pair_ids}")


class PairFactory:
    """Parses a manifest YAML file into a Manifest object."""

    @staticmethod
    def load(path: str | Path) -> Manifest:
        """Load and validate a manifest YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {path}")

        with open(path) as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict):
            raise ValueError(  # noqa: TRY004  (file content, not a call argument)
                "Manifest must be a YAML mapping at the top level."
            )

        # Required fields
        for key in ("name", "agent_module", "pairs"):
            if key not in raw:
                raise ValueError(f"Manifest is missing required field: {key!r}")

        # One campaign-wide patience, honoured by both run paths. The local path reads
        # `defaults:` and the pipeline reads `pipeline:` for every other knob, so accept
        # either here rather than making the key depend on how the campaign is launched.
        default_patience = raw.get("defaults", {}).get("patience") or raw.get("pipeline", {}).get(
            "patience"
        )
        default_continuous = raw.get("defaults", {}).get("continuous_val_score") or raw.get(
            "pipeline", {}
        ).get("continuous_val_score", False)

        pairs = []
        for i, entry in enumerate(raw["pairs"]):
            pair_id = entry.get("id", f"pair-{i + 1}")
            if "model" not in entry:
                raise ValueError(f"Pair {pair_id!r} is missing required field: 'model'")
            if "system_prompt" not in entry:
                raise ValueError(f"Pair {pair_id!r} is missing required field: 'system_prompt'")

            temperature = float(entry.get("temperature", DEFAULT_TEMPERATURE))
            spec = MODELS.get(entry["model"])
            if temperature != DEFAULT_TEMPERATURE and spec and not spec.supports_sampling_params:
                # Claude Opus 4.7 and later return a 400 for a non-default
                # temperature/top_p/top_k. Fail here, where the message can name
                # the pair, rather than mid-run behind an SDK stack trace.
                raise ValueError(
                    f"Pair {pair_id!r} sets temperature={temperature}, but "
                    f"{entry['model']} rejects sampling parameters. Remove the field "
                    f"and steer the model through the system prompt instead."
                )

            raw_costs = entry.get("costs")
            costs = None
            if isinstance(raw_costs, dict) and "input" in raw_costs and "output" in raw_costs:
                costs = {"input": float(raw_costs["input"]), "output": float(raw_costs["output"])}

            replicates = entry.get("replicates", 1)
            if not isinstance(replicates, int) or isinstance(replicates, bool) or replicates < 1:
                raise ValueError(
                    f"Pair {pair_id!r} sets replicates={replicates!r}; it must be an integer >= 1."
                )
            if replicates > 1 and entry.get("engine_id"):
                # Replicates exist to draw the search more than once. Pointing them all
                # at one pinned engine would deploy nothing new and have every replicate
                # evaluate the same deployment, which measures the opposite of the point.
                raise ValueError(
                    f"Pair {pair_id!r} sets both replicates={replicates} and an "
                    f"engine_id. Replicates each need their own deployment; drop the "
                    f"engine_id, or drop replicates and name the arms separately."
                )

            pair = AgentPromptPair(
                id=pair_id,
                model=entry["model"],
                system_prompt=entry["system_prompt"],
                temperature=temperature,
                description=entry.get("description", ""),
                tags=entry.get("tags", []),
                engine_id=entry.get("engine_id", ""),
                agent_module=entry.get("agent_module", ""),
                costs=costs,
                enabled=entry.get("enabled", True),
                disabled_reason=entry.get("disabled_reason", ""),
                forward_rationale=entry.get("forward_rationale", True),
                skip_optimize=entry.get("skip_optimize", False),
                patience=entry.get("patience", default_patience),
                continuous_val_score=bool(entry.get("continuous_val_score", default_continuous)),
            )

            # A replicate is just another pair. Everything downstream then works
            # untouched: `run_id` hashes the pair-id list, stage artifacts are keyed
            # `{run_id}/stages/{stage}/{pair_id}.json`, `_pairs_json` iterates
            # `enabled_pairs`, and `PairAnalysis.is_control` keys off the prompt rather
            # than an id convention, so a replicated control still registers as one.
            # No DAG change, no component change, no new artifact layout.
            #
            # `replicates: 1` must leave the id alone -- every existing manifest omits
            # the key, and a suffix would change `run_id` and silently invalidate the
            # cache of every campaign in flight.
            if replicates == 1:
                pairs.append(pair)
            else:
                pairs.extend(
                    replace(pair, id=f"{pair_id}-r{n}", replicate_of=pair_id)
                    for n in range(1, replicates + 1)
                )

        return Manifest(
            name=raw["name"],
            description=raw.get("description", ""),
            agent_module=raw["agent_module"],
            eval_data=raw.get("eval_data", ""),
            pairs=pairs,
            eval_config=raw.get("eval_config", {}),
            pipeline=raw.get("pipeline", {}),
            health_gate=raw.get("health_gate", {}),
            labels=raw.get("labels", {}),
        )
