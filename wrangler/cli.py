"""CLI entry point for GEPA Prompt Wrangler."""

import os
from pathlib import Path

import click
import yaml

from .core.models import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_AGENT_MODEL_ALT,
    DEFAULT_MANIFEST_JUDGE_MODEL,
)


def _is_experiment_dir(path: str) -> bool:
    """True if path is an experiment directory (has config.yaml)."""
    return Path(path).is_dir() and (Path(path) / "config.yaml").exists()


@click.group()
@click.version_option(version="0.1.0", prog_name="gepa-prompt-wrangler")
def main():
    """GEPA Prompt Wrangler — prompt optimization harness for ADK agents."""


# ── Experiment management ──────────────────────────────────────


@main.group()
def experiment():
    """Manage DOE experiment campaigns."""


@experiment.command("create")
@click.argument("manifest")
@click.option("--name", "-n", default=None, help="Experiment name (defaults to manifest name).")
@click.option("--version", "-v", default=None, help="Version tag (e.g. wrangler_v5).")
@click.option(
    "--dir", "base_dir", default="experiments/active", help="Base directory for experiments."
)
def experiment_create(manifest: str, name: str, version: str, base_dir: str):
    """Create a new experiment from a manifest YAML."""
    from .orchestration.experiment import Experiment

    exp = Experiment.create(manifest, name=name, version=version, base_dir=base_dir)
    click.echo(f"Created experiment: {exp.dir}")
    click.echo(f"  Name:    {exp.name}")
    click.echo(f"  Version: {exp.version}")
    click.echo(f"  Pairs:   {len(exp.pair_ids)}")
    click.echo(f"\nNext: wrangler deploy {exp.dir}")


@main.command("status")
@click.argument("experiment_dir")
def status(experiment_dir: str):
    """Show experiment stage completion status."""
    from .orchestration.experiment import Experiment

    exp = Experiment.load(experiment_dir)
    exp.print_status()


# ── Pipeline stages (experiment-aware) ─────────────────────────


@main.command()
@click.argument("target", default="manifest.yaml")
@click.option("--pair", "-p", default=None, help="Deploy only a specific pair by ID.")
def deploy(target: str, pair: str):
    """Deploy agent pairs to GEAP.

    TARGET can be an experiment directory or a manifest.yaml file.
    """
    if _is_experiment_dir(target):
        from .orchestration.experiment import Experiment
        from .orchestration.stages import stage_deploy

        exp = Experiment.load(target)
        click.echo(f"Deploying — experiment: {exp.name}")
        stage_deploy(exp, pair_id=pair)
    else:
        from .orchestration.runner import WranglerPipeline

        pipeline = WranglerPipeline(target)
        pairs = [pipeline.manifest.get_pair(pair)] if pair else pipeline.manifest.pairs
        for p in pairs:
            click.echo(f"\n[{p.id}] Deploying {p.model}...")
            engine_id = pipeline._deploy_pair(p)
            click.echo(f"  Engine ID: {engine_id}")


@main.command("eval")
@click.argument("target", default="manifest.yaml", required=False)
@click.argument("phase", default="before", required=False)
@click.option("--pair", "-p", default=None, help="Evaluate only a specific pair by ID.")
@click.option("--engine-id", default=None, help="Engine ID (standalone mode only).")
@click.option("--eval-data", default=None, help="Path to eval data file (standalone mode).")
@click.option("--agent-name", default=None, help="Label for this agent in results.")
@click.option(
    "--num-runs",
    "-n",
    default=None,
    type=int,
    help="Number of eval runs to average (default: the experiment's config).",
)
@click.option(
    "--retry-failed/--no-retry-failed",
    default=True,
    help="Retry failed inference cases (default: on).",
)
def eval_cmd(
    target: str,
    phase: str,
    pair: str,
    engine_id: str,
    eval_data: str,
    agent_name: str,
    num_runs: int,
    retry_failed: bool,
):
    """Run batch evaluation against deployed agents.

    Experiment mode:  wrangler eval <experiment_dir> before|after [--pair ID]
    Standalone mode:  wrangler eval --engine-id <id> --eval-data <path>
    """
    if _is_experiment_dir(target):
        from .orchestration.experiment import Experiment
        from .orchestration.stages import stage_eval

        if phase not in ("before", "after"):
            click.echo(f"Error: phase must be 'before' or 'after', got '{phase}'")
            raise SystemExit(1)

        exp = Experiment.load(target)
        click.echo(f"Evaluating ({phase}) — experiment: {exp.name}")
        stage_eval(
            exp,
            phase=phase,
            pair_id=pair,
            # Passed through as-is. This used to be `num_runs if num_runs > 1
            # else None`, which — with the option defaulting to 1 — made
            # `-n 1` indistinguishable from the flag being absent. It fell
            # through to the experiment's own default and silently ran three
            # times the work. `default=None` above is what lets 1 be a real,
            # lowering choice rather than a no-op.
            num_runs=num_runs,
            retry_failed=retry_failed,
        )
    elif engine_id:
        from .core.converter import load_eval_file
        from .eval.evaluator import run_batch_eval_averaged

        if eval_data:
            eval_cases = load_eval_file(eval_data)
        elif target and os.path.exists(target):
            from .core.factory import PairFactory

            m = PairFactory.load(target)
            eval_cases = load_eval_file(m.eval_data)
        else:
            click.echo("Error: provide --eval-data or a manifest.yaml.")
            raise SystemExit(1)

        label = agent_name or engine_id
        result = run_batch_eval_averaged(
            engine_id,
            eval_cases,
            num_runs=num_runs or 1,
            agent_name=label,
            retry_failed=retry_failed,
        )
        click.echo(f"\nResults for {label}:")
        for metric, score in sorted(result.scores.items()):
            std = result.scores_std.get(metric)
            std_str = f" +/- {std:.3f}" if std else ""
            click.echo(f"  {metric:40s} {score:.2f}{std_str}")

        # Persist per-case rows, not just the means. A control arm runs through
        # this path twice, and comparing two runs by subtracting aggregate means
        # measures the difference between their case subsets as much as anything
        # else. With the rows on disk the two can be paired by case index.
        from .eval.evaluator import save_eval_results

        saved = save_eval_results(
            agent_name=label,
            scores=result.scores,
            phase="standalone",
            per_case=result.per_case,
            coverage=result.coverage,
            scoring=result.scoring,
        )
        source = result.scoring.get("source", "unknown")
        click.echo(
            f"\n  Saved: {saved} ({len(result.per_case)} per-case rows, scoring source: {source})"
        )
    else:
        click.echo("Error: provide an experiment directory or --engine-id.")
        raise SystemExit(1)


@main.command()
@click.argument("target", default="manifest.yaml")
@click.option("--pair", "-p", default=None, help="Optimize only a specific pair by ID.")
@click.option("--judge-model", "-j", default=None, help="Judge model for GEPA eval.")
@click.option("--version", "-v", default=None, help="Version tag for saved prompts.")
def optimize(target: str, pair: str, judge_model: str, version: str):
    """Run GEPA optimization for pairs.

    TARGET can be an experiment directory or a manifest.yaml file.
    """
    if _is_experiment_dir(target):
        from .orchestration.experiment import Experiment
        from .orchestration.stages import stage_optimize

        exp = Experiment.load(target)
        click.echo(f"Optimizing — experiment: {exp.name}")
        stage_optimize(exp, pair_id=pair)
    else:
        from .core.factory import PairFactory
        from .optimize.optimizer import optimize as run_optimize

        m = PairFactory.load(target)
        pairs = [m.get_pair(pair)] if pair else m.pairs

        for p in pairs:
            click.echo(f"\n[{p.id}] Optimizing with model {p.model}...")
            result = run_optimize(m.agent_module, m.eval_data)
            click.echo(f"  Optimized instruction ({len(result)} chars)")

            output_path = Path("outputs/prompts") / f"{p.id}_optimized.txt"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(result)
            click.echo(f"  Saved to: {output_path}")


@main.command()
@click.argument("experiment_dir")
@click.option("--pair", "-p", default=None, help="Redeploy only a specific pair by ID.")
def redeploy(experiment_dir: str, pair: str):
    """Redeploy agents with optimized prompts."""
    from .orchestration.experiment import Experiment
    from .orchestration.stages import stage_redeploy

    exp = Experiment.load(experiment_dir)
    click.echo(f"Redeploying — experiment: {exp.name}")
    stage_redeploy(exp, pair_id=pair)


@main.command()
@click.argument("experiment_dir")
def analyze(experiment_dir: str):
    """Analyze experiment results — per-pair diffs, prompt analysis, recommendations."""
    from .orchestration.experiment import Experiment
    from .reporting.analyzer import run_analysis

    exp = Experiment.load(experiment_dir)
    click.echo(f"Analyzing — experiment: {exp.name}")
    report_path = run_analysis(exp)
    click.echo(f"\nFull report: {report_path}")


@main.command()
@click.argument("target", default="outputs")
@click.option(
    "--no-paperbanana",
    is_flag=True,
    default=False,
    help="Skip PaperBanana, use matplotlib only for charts.",
)
def report(target: str, no_paperbanana: bool):
    """Generate analysis report.

    TARGET can be an experiment directory or an outputs directory with results JSON.
    """
    if _is_experiment_dir(target):
        from .orchestration.experiment import Experiment
        from .orchestration.stages import stage_report

        exp = Experiment.load(target)
        click.echo(f"Generating report — experiment: {exp.name}")
        stage_report(exp, use_paperbanana=not no_paperbanana)
    else:
        import json

        results_files = sorted(Path(target).glob("results_*.json"))
        if not results_files:
            click.echo("No results files found. Run 'wrangler run' first.")
            return

        with open(results_files[-1]) as f:
            results = json.load(f)

        from .reporting.reporter import generate_report

        generate_report(results, "experiment", use_paperbanana=not no_paperbanana)
        click.echo("Report generated at outputs/reports/experiment_report.md")


# ── End-to-end ─────────────────────────────────────────────────


@main.command()
@click.argument("manifest", default="manifest.yaml")
@click.option("--name", "-n", default=None, help="Experiment name.")
@click.option("--version", "-v", default=None, help="Version tag (e.g. wrangler_v5).")
@click.option("--num-runs", default=3, type=int, help="Number of eval runs to average.")
@click.option("--pair", "-p", default=None, help="Run only a specific pair.")
@click.option("--dry-run", is_flag=True, help="Parse and validate without executing.")
@click.option(
    "--resume-from",
    "resume_from",
    default=None,
    help="Path to previous results JSON (legacy mode).",
)
@click.option(
    "--from-phase", "from_phase", default=0, type=int, help="Start from this phase (legacy mode)."
)
@click.option(
    "--max-concurrent", "-c", default=1, type=int, help="Max parallel evals (legacy mode)."
)
def run(
    manifest: str,
    name: str,
    version: str,
    num_runs: int,
    pair: str,
    dry_run: bool,
    resume_from: str,
    from_phase: int,
    max_concurrent: int,
):
    """Run the full pipeline: deploy -> eval -> optimize -> redeploy -> eval -> report.

    Creates an experiment directory and runs all stages in sequence.
    """
    if resume_from or from_phase > 0:
        from .orchestration.runner import WranglerPipeline

        pipeline = WranglerPipeline(
            manifest, max_concurrent=max_concurrent, version=version, num_runs=num_runs
        )
        if resume_from:
            pipeline.load_results(resume_from)
        pipeline.run(from_phase=from_phase)
        return

    if dry_run:
        from .core.factory import PairFactory

        m = PairFactory.load(manifest)
        click.echo(f"Manifest: {m.name}")
        click.echo(f"Pairs: {len(m.pairs)}")
        for p in m.pairs:
            click.echo(f"  {p.summary()}")
        return

    from .orchestration.experiment import Experiment
    from .orchestration.stages import (
        stage_deploy,
        stage_eval,
        stage_optimize,
        stage_redeploy,
        stage_report,
    )

    exp = Experiment.create(manifest, name=name, version=version)

    click.echo(f"\n{'=' * 60}")
    click.echo(f"GEPA PROMPT WRANGLER — {exp.name}")
    click.echo(f"{'=' * 60}")
    click.echo(f"  Experiment: {exp.dir}")
    click.echo(f"  Version:    {exp.version}")
    click.echo(f"  Pairs:      {len(exp.pair_ids)}")
    click.echo()

    click.echo("\n--- Deploy ---")
    stage_deploy(exp, pair_id=pair)

    click.echo("\n--- Baseline Evaluation ---")
    stage_eval(exp, phase="before", pair_id=pair, num_runs=num_runs, retry_failed=True)

    click.echo("\n--- GEPA Optimization ---")
    stage_optimize(exp, pair_id=pair)

    click.echo("\n--- Redeploy ---")
    stage_redeploy(exp, pair_id=pair)

    click.echo("\n--- Post-Optimization Evaluation ---")
    stage_eval(exp, phase="after", pair_id=pair, num_runs=num_runs, retry_failed=True)

    click.echo("\n--- Report ---")
    stage_report(exp)

    click.echo(f"\n{'=' * 60}")
    click.echo(f"COMPLETE — results in {exp.dir}")
    click.echo(f"{'=' * 60}")
    exp.print_status()


# ── Utility commands ───────────────────────────────────────────


@main.command()
@click.option("--output", "-o", default="manifest.yaml", help="Output path for the manifest.")
@click.option("--agent-dir", "-a", default=None, help="Path to an ADK agent module to auto-detect.")
def init(output: str, agent_dir: str):
    """Create a starter manifest.yaml for a new optimization run."""
    if os.path.exists(output):
        click.echo(f"Error: {output} already exists. Use --output to specify a different path.")
        raise SystemExit(1)

    if agent_dir:
        from .tools.inspector import AgentInspector

        click.echo(f"Inspecting agent at {agent_dir}...")
        try:
            spec = AgentInspector.inspect(agent_dir)
        except Exception as e:
            click.echo(f"Error inspecting agent: {e}")
            raise SystemExit(1) from e

        manifest = AgentInspector.generate_manifest_stub(spec, agent_dir)
        click.echo(f"  Agent: {spec.name}")
        click.echo(f"  Model: {spec.model}")
        click.echo(f"  Tools: {len(spec.tools)}")

        eval_cases = AgentInspector.generate_eval_skeleton(spec)
        eval_path = Path(output).parent / "eval_cases.yaml"
        if not eval_path.exists():
            eval_data = {"eval_cases": eval_cases}
            with open(eval_path, "w") as f:
                yaml.dump(eval_data, f, default_flow_style=False, sort_keys=False, width=100)
            click.echo(f"  Generated eval skeleton: {eval_path} ({len(eval_cases)} cases)")

        if spec.tools:
            click.echo("\n  Tool names for eval cases:")
            for t in spec.tools:
                prefix = f" (eval: {t.eval_name})" if t.eval_name != t.name else ""
                click.echo(f"    - {t.name} [{t.tool_type}]{prefix}")
    else:
        manifest = {
            "name": "my-optimization-run",
            "description": "Compare prompt quality across models",
            "agent_module": "agents/example_agent",
            "eval_data": "eval_data/example_eval.yaml",
            "pairs": [
                {
                    "id": "gemini-flash",
                    "model": DEFAULT_AGENT_MODEL,
                    "system_prompt": "You are a helpful assistant. Use tools when needed.",
                },
                {
                    "id": "claude-sonnet",
                    "model": DEFAULT_AGENT_MODEL_ALT,
                    "system_prompt": "You are a helpful assistant. Use tools when needed.",
                },
            ],
            "eval_config": {
                "judge_model": DEFAULT_MANIFEST_JUDGE_MODEL,
                "response_match_threshold": 0.5,
                "safety_threshold": 0.8,
            },
        }

    with open(output, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)
    click.echo(f"Created {output} — edit it with your agent and eval data paths.")


@main.command()
@click.argument("agent_path")
@click.option("--output", "-o", default=None, help="Output YAML file path")
def inspect(agent_path: str, output: str):
    """Inspect an agent module and discover its tools."""
    from .tools.inspector import AgentInspector

    spec = AgentInspector.inspect(agent_path)
    yaml_str = AgentInspector.to_yaml(spec)

    if output:
        Path(output).write_text(yaml_str)
        click.echo(f"Agent spec saved to: {output}")
    else:
        click.echo(yaml_str)
    click.echo(f"\nDiscovered {len(spec.tools)} tools for agent '{spec.name}'")

    if spec.tools:
        click.echo("\nTool names for eval cases:")
        for t in spec.tools:
            prefix = (
                f" -> use '{t.eval_name}_<function>' in eval cases"
                if t.tool_type == "mcp_toolset"
                else ""
            )
            click.echo(f"  {t.eval_name:40s} [{t.tool_type}]{prefix}")


@main.command("generate-evalset")
@click.option("--from", "from_path", required=True, help="Path to simplified eval YAML.")
@click.option("--output", "-o", required=True, help="Output directory for GEPA evalset files.")
@click.option("--count", "-n", default=15, help="Number of eval cases to include.")
@click.option("--balanced/--no-balanced", default=True, help="Balance across complexity levels.")
@click.option("--app-name", default=None, help="App name (defaults to output directory name).")
def generate_evalset(from_path: str, output: str, count: int, balanced: bool, app_name: str):
    """Generate a GEPA-compatible evalset from simplified eval cases."""
    from .core.converter import generate_gepa_evalset, generate_sampler_config, load_eval_file

    cases = load_eval_file(from_path)
    click.echo(f"Loaded {len(cases)} eval cases from {from_path}")

    if app_name is None:
        app_name = Path(output).name

    eval_set_id = f"{app_name.replace('-', '_')}_eval_set"
    evalset_path = generate_gepa_evalset(
        cases,
        output,
        eval_set_id=eval_set_id,
        app_name=app_name,
        count=count,
        balanced=balanced,
    )
    click.echo(f"  Evalset: {evalset_path} ({min(count, len(cases))} cases)")

    generate_sampler_config(app_name, eval_set_id, output_dir=output)
    click.echo(f"  Sampler config: {Path(output) / 'sampler_config.json'}")


# ── Pipeline commands ─────────────────────────────────────────


@main.group()
def pipeline():
    """Run GEPA optimization as a Vertex AI Pipeline."""


@pipeline.command("run")
@click.argument("manifest")
@click.option("--run-id", default=None, help="Custom run ID (default: auto-generated timestamp).")
@click.option("--num-runs", default=1, type=int, help="Number of eval averaging runs per pair.")
@click.option("--quick-test", is_flag=True, help="Quick validation run with minimal resources.")
def pipeline_run(manifest: str, run_id: str | None, num_runs: int, quick_test: bool):
    """Compile and submit the GEPA pipeline to Vertex AI."""
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    from .pipeline.deploy_pipeline import deploy_pipeline

    result = deploy_pipeline(
        manifest_path=manifest,
        run_id=run_id,
        num_runs=num_runs,
        quick_test=quick_test,
    )
    click.echo("\nPipeline submitted:")
    click.echo(f"  Run ID:    {result['run_id']}")
    click.echo(f"  Job ID:    {result['job_id']}")
    click.echo(f"  Dashboard: {result['dashboard_uri']}")


@pipeline.command("status")
@click.argument("job_id")
def pipeline_status(job_id: str):
    """Check the status of a pipeline job."""
    from google.cloud import aiplatform

    project_id = os.environ.get("GCP_PROJECT_ID", "")
    location = os.environ.get("GCP_REGION", "us-central1")

    aiplatform.init(project=project_id, location=location)
    job = aiplatform.PipelineJob.get(resource_name=job_id)
    click.echo(f"Job:    {job.display_name}")
    click.echo(f"State:  {job.state}")
    click.echo(f"Create: {job.create_time}")
    end = getattr(job, "end_time", None) or getattr(job, "update_time", None)
    if end:
        click.echo(f"End:    {end}")
    error = getattr(job, "error", None)
    if error:
        click.echo(f"Error:  {error}")


if __name__ == "__main__":
    main()
