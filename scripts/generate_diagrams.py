"""Generate architecture diagrams using PaperBanana.

Usage:
    uv run python scripts/generate_diagrams.py
    uv run python scripts/generate_diagrams.py --source docs/diagram_sources/demo_pipeline.txt
"""

import argparse
import os
import subprocess
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from wrangler.core.config import PAPERBANANA_PROJECT, PAPERBANANA_LOCATION, DIAGRAMS_DIR

DIAGRAM_SOURCES_DIR = Path("docs/diagram_sources")

DIAGRAMS = {
    "demo_pipeline.txt": "End-to-end GEPA optimization pipeline: deploy, eval, optimize, redeploy, eval, report",
    "agent_architecture.txt": "Agent architecture: ADK agents connecting to MCP tool servers via Agent Registry with GEPA optimization loop",
    "before_after_overview.txt": "Visual summary of prompt evolution through GEPA optimization across 5 model tiers",
}


def generate_diagram(source_path: str, caption: str, output_dir: str = None):
    """Generate a diagram using PaperBanana CLI."""
    output_dir = output_dir or DIAGRAMS_DIR
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    dest = Path(output_dir) / f"{Path(source_path).stem}.png"

    env = os.environ.copy()
    env["GOOGLE_CLOUD_PROJECT"] = PAPERBANANA_PROJECT
    env["GOOGLE_CLOUD_LOCATION"] = PAPERBANANA_LOCATION

    cmd = [
        "uv",
        "run",
        "paperbanana",
        "generate",
        "-i",
        source_path,
        "-c",
        caption,
        "-o",
        str(dest),
        "--vlm-provider",
        "gemini",
        "--image-provider",
        "google_imagen",
        "--image-model",
        "gemini-3-pro-image",
        "-n",
        "2",
    ]

    print(f"  Generating: {Path(source_path).stem}...")
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        print(f"    Error: {result.stderr[-200:]}")
        return None

    # PaperBanana may write to a run-specific dir instead of -o path
    if not dest.exists():
        import glob

        run_dirs = sorted(glob.glob(str(Path(output_dir) / "run_*")), reverse=True)
        for run_dir in run_dirs:
            final = Path(run_dir) / "final_output.png"
            if final.exists():
                import shutil

                shutil.copy2(str(final), str(dest))
                break

    if dest.exists():
        print(f"    Saved: {dest}")
        return str(dest)

    print(f"    Output not found. Stdout: {result.stdout[-200:]}")
    return None


def main(source: str = None):
    Path(DIAGRAMS_DIR).mkdir(parents=True, exist_ok=True)

    if source:
        caption = DIAGRAMS.get(Path(source).name, "Architecture diagram")
        generate_diagram(source, caption)
        return

    print("Generating all diagrams...")
    for filename, caption in DIAGRAMS.items():
        source_path = DIAGRAM_SOURCES_DIR / filename
        if source_path.exists():
            generate_diagram(str(source_path), caption)
        else:
            print(f"  Skipping {filename} (not found)")

    print("\nDone!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None, help="Generate a specific diagram source")
    args = parser.parse_args()
    main(args.source)
