"""Prompt registry — save and load optimized prompts with metadata.

Automatically appends new optimization results to the agent's prompt file.
"""

import importlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path


def save_optimized_prompt(
    agent_name: str,
    optimized_prompt: str,
    version_name: str | None = None,
    source: str = "wrangler",
    eval_cases: int = 15,
    judge_model: str = "gemini-2.5-pro",
    notes: str = "",
    prompts_dir: str | None = None,
) -> str:
    """Save an optimized prompt to the agent's prompt registry file.

    Args:
        agent_name: Agent name (lite, flash, pro, sonnet, opus)
        optimized_prompt: The optimized instruction text
        version_name: Version key (auto-generated if None)
        source: Where the optimization was run
        eval_cases: Number of eval cases used
        judge_model: Judge model used for scoring
        notes: Additional notes
        prompts_dir: Path to prompts directory

    Returns:
        The version name used
    """
    if prompts_dir is None:
        prompts_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "examples",
            "multi_model_agents",
            "prompts",
        )

    prompts_file = os.path.join(prompts_dir, f"{agent_name}_prompts.py")
    if not os.path.exists(prompts_file):
        raise FileNotFoundError(f"Prompt file not found: {prompts_file}")

    # Generate version name if not provided
    if version_name is None:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        version_name = f"{source}_v_{timestamp}"

    # Load existing module to get current OPTIMIZED dict
    sys.path.insert(0, prompts_dir)
    sys.path.insert(0, os.path.dirname(prompts_dir))
    mod = importlib.import_module(f"prompts.{agent_name}_prompts")
    existing_optimized = dict(getattr(mod, "OPTIMIZED", {}))
    generic = getattr(mod, "GENERIC", "")
    active = getattr(mod, "ACTIVE", generic)

    # Add new version
    existing_optimized[version_name] = {
        "prompt": optimized_prompt,
        "source": source,
        "eval_cases": eval_cases,
        "judge_model": judge_model,
        "notes": notes,
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }

    # Rebuild the file
    lines = [
        f'"""Prompt versions for the {agent_name} agent.',
        "",
        "Each version is stored with metadata about its source and optimization config.",
        "Set ACTIVE to whichever prompt you want deployed.",
        '"""',
        "",
        f'GENERIC = "{generic}"',
        "",
        "OPTIMIZED = {",
    ]

    for vname, meta in existing_optimized.items():
        prompt_text = meta["prompt"].replace('"""', "'''")
        lines.append(f'    "{vname}": {{')
        lines.append('        "prompt": """')
        lines.append(f"{prompt_text}")
        lines.append('""",')
        lines.append(f'        "source": "{meta.get("source", "")}",')
        lines.append(f'        "eval_cases": {meta.get("eval_cases", 15)},')
        lines.append(f'        "judge_model": "{meta.get("judge_model", "")}",')
        lines.append(f'        "notes": "{meta.get("notes", "")}",')
        if "timestamp" in meta:
            lines.append(f'        "timestamp": "{meta["timestamp"]}",')
        lines.append("    },")

    lines.append("}")
    lines.append("")

    # Determine what ACTIVE should point to
    if active == generic:
        lines.append("# Which prompt to use for deployment")
        lines.append("ACTIVE = GENERIC")
    else:
        lines.append("# Which prompt to use for deployment")
        lines.append("ACTIVE = GENERIC")

    lines.append("")

    with open(prompts_file, "w") as f:
        f.write("\n".join(lines))

    # Also save raw text to outputs/prompts/
    outputs_dir = Path(os.path.dirname(os.path.dirname(prompts_dir))) / "outputs" / "prompts"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    raw_path = outputs_dir / f"{agent_name}_{version_name}.txt"
    raw_path.write_text(optimized_prompt)

    print(
        f"  Saved to prompt registry: {agent_name} → {version_name} ({len(optimized_prompt)} chars)"
    )
    print(f"  Raw text: {raw_path}")

    # Clean up sys.path
    if prompts_dir in sys.path:
        sys.path.remove(prompts_dir)

    return version_name


def list_versions(agent_name: str, prompts_dir: str | None = None) -> dict:
    """List all prompt versions for an agent."""
    if prompts_dir is None:
        prompts_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "examples",
            "multi_model_agents",
            "prompts",
        )

    sys.path.insert(0, prompts_dir)
    sys.path.insert(0, os.path.dirname(prompts_dir))
    mod = importlib.import_module(f"prompts.{agent_name}_prompts")
    return dict(getattr(mod, "OPTIMIZED", {}))
