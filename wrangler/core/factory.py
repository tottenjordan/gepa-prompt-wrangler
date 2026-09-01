"""Agent-prompt pair factory — parses manifest YAML into executable pairs."""

from __future__ import annotations

from dataclasses import dataclass, field
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

    @property
    def pair_ids(self) -> list[str]:
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

            pairs.append(
                AgentPromptPair(
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
                )
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
        )
