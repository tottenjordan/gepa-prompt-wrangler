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
@click.option("--agent-dir", "-a", default=None, help="Path to an ADK agent module to auto-detect.")
def init(output: str, agent_dir: str):
    """Create a starter manifest.yaml for a new optimization run.

    With --agent-dir, inspects the agent module and pre-populates the
    manifest with real agent name, model, and tool names. Also generates
    a skeleton eval_cases.yaml with correct tool names.
    """
    if os.path.exists(output):
        click.echo(f"Error: {output} already exists. Use --output to specify a different path.")
        raise SystemExit(1)

    if agent_dir:
        from .inspector import AgentInspector

        click.echo(f"Inspecting agent at {agent_dir}...")
        try:
            spec = AgentInspector.inspect(agent_dir)
        except Exception as e:
            click.echo(f"Error inspecting agent: {e}")
            raise SystemExit(1)

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
            click.echo(f"  Edit the TODO placeholders with real queries and expected responses.")

        if spec.tools:
            click.echo(f"\n  Tool names for eval cases:")
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

    if spec.tools:
        click.echo(f"\nTool names for eval cases:")
        for t in spec.tools:
            prefix = f" -> use '{t.eval_name}_<function>' in eval cases" if t.tool_type == "mcp_toolset" else ""
            click.echo(f"  {t.eval_name:40s} [{t.tool_type}]{prefix}")


@main.command()
@click.argument("manifest", default="manifest.yaml")
@click.option("--dry-run", is_flag=True, help="Parse and validate without executing.")
def run(manifest: str, dry_run: bool):
    """Run the full pipeline: deploy -> eval -> optimize -> redeploy -> eval -> report."""
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
@click.argument("manifest", default="manifest.yaml", required=False)
@click.option("--pair", "-p", help="Evaluate only a specific pair by ID.")
@click.option("--engine-id", help="Engine ID of the deployed agent.")
@click.option("--eval-data", help="Path to eval data file (required with --engine-id without manifest).")
def eval_cmd(manifest: str, pair: str, engine_id: str, eval_data: str):
    """Run batch evaluation against a deployed agent.

    Can be used two ways:
      1. With a manifest: wrangler eval manifest.yaml --engine-id <id>
      2. Standalone: wrangler eval --engine-id <id> --eval-data <path>
    """
    from .converter import load_eval_file
    from .evaluator import run_batch_eval

    if engine_id and eval_data and (not manifest or not os.path.exists(manifest)):
        eval_cases = load_eval_file(eval_data)
    elif manifest and os.path.exists(manifest):
        from .factory import PairFactory
        m = PairFactory.load(manifest)
        eval_cases = load_eval_file(m.eval_data)
    else:
        click.echo("Error: provide a manifest.yaml OR both --engine-id and --eval-data.")
        raise SystemExit(1)

    if not engine_id:
        click.echo("Error: --engine-id is required. Deploy the agent first with 'wrangler deploy'.")
        raise SystemExit(1)

    result = run_batch_eval(engine_id, eval_cases, agent_name=engine_id)
    click.echo(f"\nResults:")
    for metric, score in sorted(result.scores.items()):
        click.echo(f"  {metric:40s} {score:.2f}")


@main.command("generate-evalset")
@click.option("--from", "from_path", required=True, help="Path to simplified eval YAML.")
@click.option("--output", "-o", required=True, help="Output directory for GEPA evalset files.")
@click.option("--count", "-n", default=15, help="Number of eval cases to include (default: 15).")
@click.option("--balanced/--no-balanced", default=True, help="Balance across complexity levels.")
@click.option("--app-name", default=None, help="App name (defaults to output directory name).")
def generate_evalset(from_path: str, output: str, count: int, balanced: bool, app_name: str):
    """Generate a GEPA-compatible evalset from simplified eval cases.

    Creates the evalset JSON and sampler_config.json needed for GEPA optimization.
    """
    from .converter import load_eval_file, generate_gepa_evalset, generate_sampler_config

    cases = load_eval_file(from_path)
    click.echo(f"Loaded {len(cases)} eval cases from {from_path}")

    if app_name is None:
        app_name = Path(output).name

    eval_set_id = f"{app_name.replace('-', '_')}_eval_set"
    evalset_path = generate_gepa_evalset(
        cases, output, eval_set_id=eval_set_id,
        app_name=app_name, count=count, balanced=balanced,
    )
    click.echo(f"  Evalset: {evalset_path} ({min(count, len(cases))} cases)")

    generate_sampler_config(app_name, eval_set_id, output_dir=output)
    click.echo(f"  Sampler config: {Path(output) / 'sampler_config.json'}")
    click.echo(f"\nReady for optimization:")
    click.echo(f"  wrangler optimize --agent-dir <agent_path> --evalset-dir {output}")


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
