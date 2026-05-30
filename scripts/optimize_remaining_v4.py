"""Resume GEPA v4 optimization for flash, pro, sonnet, opus (lite already done)."""

import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrangler.optimizer import optimize
from wrangler.runner import _fmt_duration

MODELS = ["flash", "pro", "sonnet", "opus"]
VERSION = "wrangler_v4"
AGENTS_DIR = Path("examples/multi_model_agents/agents")
PROMPTS_DIR = Path("examples/multi_model_agents/prompts")
EVAL_PATH = Path("examples/multi_model_agents/eval_data/eval_cases.yaml")
RUN_DIR = Path("outputs/gepa_runs")


def clean_run_dir(model: str):
    run_dir = RUN_DIR / f"{model}_opt"
    if run_dir.exists():
        shutil.rmtree(run_dir)
        print(f"  Cleared {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)


def save_prompt(model: str, prompt: str, elapsed: float):
    prompts_file = PROMPTS_DIR / f"{model}_prompts.py"
    if not prompts_file.exists():
        print(f"  WARNING: {prompts_file} not found, skipping save")
        return

    content = prompts_file.read_text()

    entry_lines = [
        f'    "{VERSION}": {{',
        f'        "prompt": """{prompt}""",',
        f'        "source": "wrangler GEPA optimization (5 criteria, generic seed)",',
        f'        "eval_cases": 40,',
        f'        "judge_model": "gemini-2.5-pro",',
        f'        "criteria": "response_match, final_response_match_v2, safety, rubric_response_quality, rubric_tool_use_quality",',
        f'        "duration": "{_fmt_duration(elapsed)}",',
        f'        "notes": "Generic 78-char seed, 28/12 train/val, 5 criteria with tool use + instruction adherence rubrics",',
        f'        "timestamp": "{datetime.now().isoformat()}",',
        '    },',
    ]
    new_entry = "\n".join(entry_lines)

    if f'"{VERSION}"' in content:
        content = re.sub(
            rf'    "{VERSION}": \{{.*?\}},\n',
            '',
            content,
            flags=re.DOTALL,
        )

    closing = content.rstrip()
    insert_pos = closing.rfind("}")
    updated = closing[:insert_pos] + new_entry + "\n}\n"
    prompts_file.write_text(updated)
    print(f"  Saved {VERSION} to {prompts_file}")


def main():
    results = {}
    pipeline_start = time.time()

    print(f"{'=' * 60}")
    print(f"GEPA v4 OPTIMIZATION — REMAINING MODELS")
    print(f"{'=' * 60}")
    print(f"  Models:   {', '.join(MODELS)} (lite already done)")
    print(f"  Seed:     generic 78-char prompt")
    print(f"  Criteria: 5 (response_match, final_response_match_v2, safety,")
    print(f"            rubric_response_quality, rubric_tool_use_quality)")
    print()

    for i, model in enumerate(MODELS, 1):
        agent_path = AGENTS_DIR / f"{model}_opt"
        sampler_cfg = agent_path / "sampler_config.json"

        print(f"\n{'=' * 60}")
        print(f"[{i}/{len(MODELS)}] {model.upper()}")
        print(f"{'=' * 60}")

        clean_run_dir(model)

        t0 = time.time()
        try:
            optimized = optimize(
                str(agent_path),
                eval_data_path=str(EVAL_PATH),
                sampler_config_path=str(sampler_cfg) if sampler_cfg.exists() else None,
                agent_name=f"{model}-v4",
            )
            elapsed = time.time() - t0
            results[model] = {
                "status": "ok",
                "elapsed": elapsed,
                "prompt_len": len(optimized),
            }
            save_prompt(model, optimized, elapsed)
            print(f"\n  {model}: DONE ({_fmt_duration(elapsed)}) — {len(optimized)} chars")

        except Exception as e:
            elapsed = time.time() - t0
            results[model] = {"status": "error", "elapsed": elapsed, "error": str(e)}
            print(f"\n  {model}: FAILED ({_fmt_duration(elapsed)}) — {e}")

    total = time.time() - pipeline_start

    print(f"\n\n{'=' * 60}")
    print(f"RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"{'Model':<10} {'Status':<8} {'Duration':<10} {'Prompt Len':<12}")
    print(f"{'-'*10} {'-'*8} {'-'*10} {'-'*12}")
    for model in MODELS:
        r = results[model]
        if r["status"] == "ok":
            print(f"{model:<10} {'OK':<8} {_fmt_duration(r['elapsed']):<10} {r['prompt_len']:<12}")
        else:
            print(f"{model:<10} {'FAIL':<8} {_fmt_duration(r['elapsed']):<10} {r['error'][:30]}")
    print(f"\nTotal: {_fmt_duration(total)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
