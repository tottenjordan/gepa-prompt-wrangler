"""CLI entry point for GEPA Prompt Wrangler."""

import os
from pathlib import Path

import click
import yaml


@click.group()
@click.version_option(version="0.1.0", prog_name="gepa-prompt-wrangler")
def main():
    """GEPA Prompt Wrangler — prompt optimization harness for ADK agents."""


@main.command()
@click.option("--output", "-o", default="manifest.yaml", help="Output path for the manifest.")
def init(output: str):
    """Create a starter manifest.yaml for a new optimization run."""
    if os.path.exists(output):
        click.echo(f"Error: {output} already exists. Use --output to specify a different path.")
        raise SystemExit(1)

    manifest = {
        "name": "my-optimization-run",
        "description": "Compare prompt quality across models",
        "agent_module": "agents/example_agent",
        "eval_data": "eval_data/example_eval.yaml",
        "pairs": [
            {
                "id": "gemini-flash",
                "model": "gemini-3.5-flash",
                "system_prompt": "You are a helpful assistant. Use tools when needed.",
            },
            {
                "id": "claude-sonnet",
                "model": "claude-sonnet-4-6",
                "system_prompt": "You are a helpful assistant. Use tools when needed.",
            },
        ],
        "eval_config": {
            "judge_model": "gemini-2.5-pro",
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
    from .inspector import AgentInspector

    spec = AgentInspector.inspect(agent_path)
    yaml_str = AgentInspector.to_yaml(spec)

    if output:
        Path(output).write_text(yaml_str)
        click.echo(f"Agent spec saved to: {output}")
    else:
        click.echo(yaml_str)
    click.echo(f"\nDiscovered {len(spec.tools)} tools for agent '{spec.name}'")


@main.command()
@click.argument("manifest", default="manifest.yaml")
@click.option("--dry-run", is_flag=True, help="Parse and validate without executing.")
def run(manifest: str, dry_run: bool):
    """Run the full pipeline: deploy → eval → optimize → redeploy → eval → report."""
    from .runner import WranglerPipeline

    pipeline = WranglerPipeline(manifest)
    if dry_run:
        click.echo(f"Manifest: {pipeline.manifest.name}")
        click.echo(f"Pairs: {len(pipeline.manifest.pairs)}")
        for p in pipeline.manifest.pairs:
            click.echo(f"  {p.summary()}")
        return
    pipeline.run()


@main.command("eval")
@click.argument("manifest", default="manifest.yaml")
@click.option("--pair", "-p", help="Evaluate only a specific pair by ID.")
@click.option("--engine-id", help="Engine ID of the deployed agent.")
def eval_cmd(manifest: str, pair: str, engine_id: str):
    """Run batch evaluation against a deployed agent."""
    from .factory import PairFactory
    from .converter import load_eval_file
    from .evaluator import run_batch_eval

    m = PairFactory.load(manifest)
    eval_cases = load_eval_file(m.eval_data)

    if not engine_id:
        click.echo("Error: --engine-id is required. Deploy the agent first with 'wrangler deploy'.")
        raise SystemExit(1)

    scores = run_batch_eval(engine_id, eval_cases)
    click.echo(f"\nResults:")
    for metric, score in sorted(scores.items()):
        click.echo(f"  {metric:40s} {score:.2f}")


@main.command()
@click.argument("manifest", default="manifest.yaml")
@click.option("--pair", "-p", help="Optimize only a specific pair by ID.")
def optimize(manifest: str, pair: str):
    """Run GEPA optimization for pairs in the manifest."""
    from .factory import PairFactory
    from .optimizer import optimize as run_optimize

    m = PairFactory.load(manifest)
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
@click.argument("results_dir", default="outputs")
def report(results_dir: str):
    """Generate analysis report from existing results."""
    import json

    results_files = sorted(Path(results_dir).glob("results_*.json"))
    if not results_files:
        click.echo("No results files found. Run 'wrangler run' first.")
        return

    with open(results_files[-1]) as f:
        results = json.load(f)

    from .reporter import generate_report
    generate_report(results, "experiment")
    click.echo("Report generated at outputs/reports/experiment_report.md")


@main.command()
@click.argument("manifest", default="manifest.yaml")
@click.option("--pair", "-p", help="Deploy only a specific pair by ID.")
def deploy(manifest: str, pair: str):
    """Deploy agent pairs to GEAP."""
    from .runner import WranglerPipeline
    from . import deploy as deployer

    pipeline = WranglerPipeline(manifest)
    pairs = [pipeline.manifest.get_pair(pair)] if pair else pipeline.manifest.pairs

    for p in pairs:
        click.echo(f"\n[{p.id}] Deploying {p.model}...")
        agent = pipeline._load_agent(p)
        engine_id = deployer.deploy_agent(agent, display_name=p.id)
        click.echo(f"  Engine ID: {engine_id}")


if __name__ == "__main__":
    main()
