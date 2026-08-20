"""Agent-prompt pair factory — parses manifest YAML into executable pairs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AgentPromptPair:
    """A single model + system-prompt combination to evaluate."""

    id: str
    model: str
    system_prompt: str
    temperature: float = 1.0
    description: str = ""
    tags: list[str] = field(default_factory=list)
    engine_id: str = ""
    agent_module: str = ""
    costs: dict[str, float] | None = None

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

    @property
    def pair_ids(self) -> list[str]:
        return [p.id for p in self.pairs]

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

            raw_costs = entry.get("costs")
            costs = None
            if isinstance(raw_costs, dict) and "input" in raw_costs and "output" in raw_costs:
                costs = {"input": float(raw_costs["input"]), "output": float(raw_costs["output"])}

            pairs.append(
                AgentPromptPair(
                    id=pair_id,
                    model=entry["model"],
                    system_prompt=entry["system_prompt"],
                    temperature=float(entry.get("temperature", 1.0)),
                    description=entry.get("description", ""),
                    tags=entry.get("tags", []),
                    engine_id=entry.get("engine_id", ""),
                    agent_module=entry.get("agent_module", ""),
                    costs=costs,
                )
            )

        return Manifest(
            name=raw["name"],
            description=raw.get("description", ""),
            agent_module=raw["agent_module"],
            eval_data=raw.get("eval_data", ""),
            pairs=pairs,
            eval_config=raw.get("eval_config", {}),
        )
