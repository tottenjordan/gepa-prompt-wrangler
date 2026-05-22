"""CLI entry point for GEPA Prompt Wrangler."""

import os
import click
import yaml


@click.group()
@click.version_option(version="0.1.0", prog_name="gepa-prompt-wrangler")
def main():
    """GEPA Prompt Wrangler -- prompt optimization harness for ADK agents."""


@main.command()
@click.option(
    "--output",
    "-o",
    default="manifest.yaml",
    help="Output path for the generated manifest.",
)
def init(output: str):
    """Create a starter manifest.yaml for a new optimization run."""
    manifest = {
        "name": "my-optimization-run",
        "description": "Prompt optimization run",
        "agent_module": "agents.example_agent",
        "eval_data": "eval_data/example_eval.yaml",
        "pairs": [
            {
                "id": "pair-1",
                "model": "gemini-3.5-flash",
                "system_prompt": "You are a helpful travel assistant.",
            },
        ],
        "eval_config": {
            "metrics": ["tool_trajectory", "response_match"],
            "judge_model": "gemini-2.5-flash",
        },
    }

    if os.path.exists(output):
        click.echo(f"Error: {output} already exists. Use --output to specify a different path.")
        raise SystemExit(1)

    with open(output, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)

    click.echo(f"Created starter manifest at {output}")


@main.command()
@click.argument("manifest", default="manifest.yaml")
def inspect(manifest: str):
    """Show parsed manifest details and validate structure."""
    click.echo("Not implemented yet.")


@main.command()
@click.argument("manifest", default="manifest.yaml")
@click.option("--pair", "-p", help="Run only a specific pair by ID.")
@click.option("--dry-run", is_flag=True, help="Parse and validate without executing.")
def run(manifest: str, pair: str, dry_run: bool):
    """Deploy agents defined in the manifest."""
    click.echo("Not implemented yet.")


@main.command("eval")
@click.argument("manifest", default="manifest.yaml")
@click.option("--pair", "-p", help="Evaluate only a specific pair by ID.")
@click.option(
    "--output-dir",
    "-o",
    default="eval_outputs",
    help="Directory for evaluation results.",
)
def eval_cmd(manifest: str, pair: str, output_dir: str):
    """Run ADK evaluation against deployed agents."""
    click.echo("Not implemented yet.")


@main.command()
@click.argument("manifest", default="manifest.yaml")
@click.option("--strategy", type=click.Choice(["grid", "bayesian", "llm"]), default="llm")
@click.option("--iterations", "-n", default=3, help="Number of optimization iterations.")
def optimize(manifest: str, strategy: str, iterations: int):
    """Optimize prompts based on evaluation results."""
    click.echo("Not implemented yet.")


@main.command()
@click.argument("manifest", default="manifest.yaml")
@click.option(
    "--output-dir",
    "-o",
    default="outputs",
    help="Directory for report artifacts.",
)
@click.option("--format", "fmt", type=click.Choice(["html", "json", "csv"]), default="html")
def report(manifest: str, output_dir: str, fmt: str):
    """Generate a comparison report across all evaluated pairs."""
    click.echo("Not implemented yet.")


@main.command()
@click.argument("manifest", default="manifest.yaml")
@click.option("--pair", "-p", required=True, help="Pair ID to deploy as winner.")
@click.option("--engine-id", help="Existing Agent Engine resource ID to update.")
def deploy(manifest: str, pair: str, engine_id: str):
    """Deploy the winning prompt/model pair to Agent Engine."""
    click.echo("Not implemented yet.")


if __name__ == "__main__":
    main()
