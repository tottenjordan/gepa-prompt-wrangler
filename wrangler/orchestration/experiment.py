"""Experiment management — self-contained DOE campaigns with per-stage persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..core.factory import AgentPromptPair, Manifest, PairFactory
from ..core.models import DEFAULT_MANIFEST_JUDGE_MODEL

STAGES = ("deploy", "eval_before", "optimize", "redeploy", "eval_after", "report", "analyze")

STAGE_GATES = {
    "eval_before": "deploy",
    "optimize": "eval_before",
    "redeploy": "optimize",
    "eval_after": "redeploy",
    "report": "eval_after",
    "analyze": "eval_after",
}


@dataclass
class Experiment:
    """A self-contained DOE experiment campaign."""

    name: str
    dir: Path
    version: str
    created_at: str
    _config_cache: dict | None = field(default=None, repr=False)

    # ── factory methods ────────────────────────────────────────

    @classmethod
    def create(
        cls,
        manifest_path: str | Path,
        name: str | None = None,
        version: str | None = None,
        base_dir: str | Path = "experiments/active",
    ) -> Experiment:
        manifest_path = Path(manifest_path)
        manifest = PairFactory.load(manifest_path)

        name = name or manifest.name
        version = version or "wrangler_v1"
        now = datetime.now(tz=UTC).isoformat(timespec="seconds")

        exp_dir = Path(base_dir) / name
        if exp_dir.exists():
            raise FileExistsError(f"Experiment already exists: {exp_dir}")

        exp_dir.mkdir(parents=True)
        (exp_dir / "stages").mkdir()
        (exp_dir / "reports").mkdir()
        (exp_dir / "images").mkdir()

        config = {
            "experiment": {
                "name": name,
                "description": manifest.description,
                "created": now,
                "version": version,
            },
            "agent_module": manifest.agent_module,
            "eval_data": manifest.eval_data,
            "defaults": {
                "num_runs": 3,
                "judge_model": manifest.eval_config.get(
                    "judge_model", DEFAULT_MANIFEST_JUDGE_MODEL
                ),
            },
            "pairs": [],
            "eval_config": manifest.eval_config,
        }

        for pair in manifest.pairs:
            config["pairs"].append(
                {
                    "id": pair.id,
                    "model": pair.model,
                    "description": pair.description,
                    "agent_module": pair.agent_module,
                    "engine_id": pair.engine_id,
                    "system_prompt": pair.system_prompt,
                }
            )

        with open(exp_dir / "config.yaml", "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, width=120)

        tracking: dict[str, Any] = {
            "experiment": name,
            "created_at": now,
            "version": version,
            "stages": {},
        }
        for stage in STAGES:
            tracking["stages"][stage] = {"status": "pending", "pairs": {}}

        with open(exp_dir / "manifest.json", "w") as f:
            json.dump(tracking, f, indent=2)

        return cls(name=name, dir=exp_dir, version=version, created_at=now)

    @classmethod
    def load(cls, experiment_dir: str | Path) -> Experiment:
        exp_dir = Path(experiment_dir)
        config_path = exp_dir / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"No config.yaml in {exp_dir}")

        with open(config_path) as f:
            config = yaml.safe_load(f)

        meta = config.get("experiment", {})
        return cls(
            name=meta.get("name", exp_dir.name),
            dir=exp_dir,
            version=meta.get("version", ""),
            created_at=meta.get("created", ""),
        )

    # ── config / manifest ──────────────────────────────────────

    @property
    def config(self) -> dict:
        if self._config_cache is None:
            with open(self.dir / "config.yaml") as f:
                self._config_cache = yaml.safe_load(f)
        return self._config_cache

    @property
    def manifest(self) -> Manifest:
        cfg = self.config
        pairs = [
            AgentPromptPair(
                id=entry["id"],
                model=entry["model"],
                system_prompt=entry.get("system_prompt", ""),
                description=entry.get("description", ""),
                agent_module=entry.get("agent_module", ""),
                engine_id=entry.get("engine_id", ""),
            )
            for entry in cfg.get("pairs", [])
        ]
        return Manifest(
            name=cfg.get("experiment", {}).get("name", self.name),
            description=cfg.get("experiment", {}).get("description", ""),
            agent_module=cfg.get("agent_module", ""),
            eval_data=cfg.get("eval_data", ""),
            pairs=pairs,
            eval_config=cfg.get("eval_config", {}),
        )

    @property
    def pair_ids(self) -> list[str]:
        return [p["id"] for p in self.config.get("pairs", [])]

    # ── stage I/O ──────────────────────────────────────────────

    def stage_path(self, stage: str) -> Path:
        return self.dir / "stages" / f"{stage}.json"

    def read_stage(self, stage: str) -> dict:
        path = self.stage_path(stage)
        if not path.exists():
            return {}
        with open(path) as f:
            return json.load(f)

    def write_stage(self, stage: str, data: dict) -> None:
        path = self.stage_path(stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def merge_pair(self, stage: str, pair_id: str, data: dict) -> None:
        current = self.read_stage(stage)
        current[pair_id] = data
        self.write_stage(stage, current)
        self.update_tracking(stage, pair_id, "complete")

    # ── tracking (manifest.json) ───────────────────────────────

    def _read_tracking(self) -> dict:
        path = self.dir / "manifest.json"
        if not path.exists():
            return {"experiment": self.name, "stages": {}}
        with open(path) as f:
            return json.load(f)

    def _write_tracking(self, tracking: dict) -> None:
        with open(self.dir / "manifest.json", "w") as f:
            json.dump(tracking, f, indent=2)

    def update_tracking(self, stage: str, pair_id: str, status: str) -> None:
        tracking = self._read_tracking()
        stages = tracking.setdefault("stages", {})
        stage_info = stages.setdefault(stage, {"status": "pending", "pairs": {}})
        stage_info["pairs"][pair_id] = {
            "status": status,
            "completed_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        }

        all_pairs = set(self.pair_ids)
        done_pairs = {
            pid for pid, info in stage_info["pairs"].items() if info.get("status") == "complete"
        }
        if done_pairs >= all_pairs:
            stage_info["status"] = "complete"
            stage_info["completed_at"] = datetime.now(tz=UTC).isoformat(timespec="seconds")
        elif done_pairs:
            stage_info["status"] = "partial"
        self._write_tracking(tracking)

    # ── status ─────────────────────────────────────────────────

    def status(self) -> dict[str, dict]:
        tracking = self._read_tracking()
        all_pairs = set(self.pair_ids)
        result = {}
        for stage in STAGES:
            info = tracking.get("stages", {}).get(stage, {"status": "pending", "pairs": {}})
            done = [
                pid for pid, p in info.get("pairs", {}).items() if p.get("status") == "complete"
            ]
            remaining = sorted(all_pairs - set(done))
            result[stage] = {
                "status": info.get("status", "pending"),
                "pairs_complete": sorted(done),
                "pairs_remaining": remaining,
            }
        return result

    def print_status(self) -> None:
        st = self.status()
        print(f"\nExperiment: {self.name}")
        print(f"Directory:  {self.dir}")
        print(f"Version:    {self.version}")
        print(f"Pairs:      {len(self.pair_ids)}")
        print()
        for stage in STAGES:
            info = st[stage]
            done = len(info["pairs_complete"])
            total = done + len(info["pairs_remaining"])
            icon = {"complete": "+", "partial": "~", "pending": " "}.get(info["status"], " ")
            print(f"  [{icon}] {stage:20s} {done}/{total} pairs")
            if info["status"] == "partial":
                for pid in info["pairs_remaining"]:
                    print(f"        remaining: {pid}")

    # ── phase gates ────────────────────────────────────────────

    def check_gate(self, target_stage: str, pair_id: str | None = None) -> tuple[bool, str]:
        required = STAGE_GATES.get(target_stage)
        if required is None:
            return True, ""

        stage_data = self.read_stage(required)
        if not stage_data:
            return (
                False,
                f"Stage '{required}' has no results yet (required before '{target_stage}')",
            )

        if pair_id:
            if pair_id not in stage_data:
                return False, f"Pair '{pair_id}' has no results in stage '{required}'"
            return True, ""

        missing = [pid for pid in self.pair_ids if pid not in stage_data]
        if missing:
            return False, f"Stage '{required}' missing results for: {', '.join(missing)}"
        return True, ""
