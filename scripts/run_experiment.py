#!/usr/bin/env python3
"""Run a DOE experiment from its `experiments/` directory.

The entry point `experiments/README.md` describes, over the **experiment** schema rather
than the manifest schema. Those are two different shapes and mixing them is an easy hour
lost -- see the guard in `_load` below.

    # inspect the plan; makes NO GCP call
    uv run scripts/run_experiment.py experiments/active/<name>/config.yaml --runs 1 --dry-run

    # submit as ONE managed Vertex AI Pipelines job
    uv run scripts/run_experiment.py experiments/active/<name>/config.yaml --runs 2 --pipeline

**Why the pipeline path is the default recommendation.** A campaign is 10-24 h of compute.
Driven from a local script it is tied to this machine and this shell, and every long-running
local driver in this project has eventually been killed by something unrelated to the work --
a stale ADC took out the campaign 08 driver and the m01 watcher mid-run. Pipeline stages
survive the client, retry, and leave artifacts in GCS.

Orchestration is delegated, never reimplemented: this resolves the config and calls the same
`wrangler pipeline run` path a human would.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_EXAMPLE_ENV = Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents" / ".env"
if _EXAMPLE_ENV.exists():
    load_dotenv(str(_EXAMPLE_ENV), override=True)


def _load(config_path: Path):
    """Load the experiment. Fails loudly, and usefully, on a manifest-schema file.

    `PairFactory.load()` on an experiment config raises
    `ValueError: Manifest is missing required field: 'name'`, which says nothing about the
    real problem. The two schemas differ in exactly one visible way -- the manifest has a
    top-level `name:`, the experiment config nests it under `experiment:` -- and nothing in
    the repo said so until this message existed.
    """
    import yaml

    from wrangler.orchestration.experiment import Experiment

    if config_path.is_dir():
        config_path = config_path / "config.yaml"
    if not config_path.is_file():
        raise SystemExit(f"no config at {config_path}")

    raw = yaml.safe_load(config_path.read_text()) or {}
    if "experiment" not in raw and "name" in raw:
        raise SystemExit(
            f"{config_path} looks like a MANIFEST, not an experiment config.\n"
            f"  manifest    (manifests/*.yaml)                 top-level `name:`\n"
            f"  experiment  (experiments/active/*/config.yaml) `name:` nested under "
            f"`experiment:`\n\n"
            f"Create the experiment from the manifest first:\n"
            f"  uv run wrangler experiment create {config_path} --name <name> --version <v>"
        )
    return Experiment.load(config_path.parent)


def describe(exp, runs: int, score_repeats: int) -> list[str]:
    """The plan, as text. Pure, so --dry-run can be tested without touching GCP."""
    manifest = exp.manifest
    enabled = manifest.enabled_pairs
    lines = [
        f"Experiment: {exp.name}  (version {exp.version or '—'})",
        f"Directory:  {exp.dir}",
        f"Agent:      {manifest.agent_module}",
        f"Eval data:  {manifest.eval_data}",
        f"Knobs:      num_runs={runs}, score_repeats={score_repeats}",
        f"Pairs:      {len(enabled)} enabled of {len(manifest.pairs)}",
    ]
    for pair in manifest.pairs:
        flags = []
        if pair.skip_optimize:
            flags.append("CONTROL (no optimize)")
        flags.append(f"rationale={'on' if pair.forward_rationale else 'OFF'}")
        if not pair.enabled:
            flags.append(f"disabled: {pair.disabled_reason or 'no reason given'}")
        lines.append(f"  - {pair.id:28} {pair.model:22} [{', '.join(flags)}]")

    if not any(p.skip_optimize for p in enabled):
        lines.append(
            "\n  WARNING: no control arm. CLAUDE.md requires an arm whose prompt does not "
            "change;\n  without one no delta from this run can be called an improvement."
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", help="experiments/active/<name>/config.yaml (or its dir)")
    parser.add_argument("--runs", type=int, default=None, help="override defaults.num_runs")
    parser.add_argument(
        "--score-repeats",
        type=int,
        default=None,
        help="override defaults.score_repeats. DOE 03 made this a first-class knob: it "
        "recovers coverage rather than reducing the aggregate floor.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and exit. Makes no GCP call and constructs no client.",
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="submit as one managed Vertex AI Pipelines job (recommended over a local run).",
    )
    args = parser.parse_args()

    exp = _load(Path(args.config))
    defaults = exp.config.get("defaults", {}) or {}
    runs = args.runs if args.runs is not None else defaults.get("num_runs", 1)
    repeats = (
        args.score_repeats if args.score_repeats is not None else defaults.get("score_repeats", 1)
    )

    for line in describe(exp, runs, repeats):
        print(line)

    if args.dry_run:
        print("\nDry run — nothing submitted, no GCP call made.")
        return 0

    if not args.pipeline:
        print(
            "\nRefusing to run locally without --pipeline.\n"
            "  A campaign is 10-24 h of compute; a local driver ties it to this shell and\n"
            "  dies with it. Pass --pipeline to submit it as a managed job, or use\n"
            "  `uv run wrangler run <manifest>` deliberately if a local run is really what\n"
            "  you want.",
            file=sys.stderr,
        )
        return 2

    # Delegate: the same path a human would take, so there is one orchestrator, not two.
    #
    # `deploy_pipeline` takes a MANIFEST path and re-parses it with PairFactory.load, which
    # cannot read an experiment config. `Experiment.create` records where it came from for
    # exactly this hop; experiments created before that was added have to be told.
    from wrangler.pipeline.deploy_pipeline import deploy_pipeline

    source = exp.config.get("source_manifest")
    if not source:
        raise SystemExit(
            f"{exp.dir}/config.yaml has no `source_manifest`, so there is no manifest to\n"
            f"submit. It predates that field. Add the path it was created from:\n"
            f"  source_manifest: manifests/<name>_manifest.yaml"
        )
    if not Path(source).is_file():
        raise SystemExit(f"source_manifest points at {source}, which does not exist")

    print(f"\nSubmitting {source} as a Vertex AI Pipelines job...")
    result = deploy_pipeline(manifest_path=source, num_runs=runs, score_repeats=repeats)
    print(f"  Run ID:    {result['run_id']}")
    print(f"  Job ID:    {result['job_id']}")
    print(f"  Dashboard: {result['dashboard_uri']}")
    print("\n  Record the job id in the experiment's doe_plan.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
