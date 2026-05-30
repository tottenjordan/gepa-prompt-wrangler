"""Solo GEPA optimization for sonnet-claude."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wrangler.optimizer import optimize
from wrangler.runner import _fmt_duration


def main():
    agent_path = Path("examples/multi_model_agents/agents/sonnet_opt")
    eval_path = Path("examples/multi_model_agents/eval_data/eval_cases.yaml")
    sampler_cfg = agent_path / "sampler_config.json"

    print(f"{'=' * 60}")
    print(f"SOLO SONNET OPTIMIZATION")
    print(f"{'=' * 60}")
    print(f"  Agent:       {agent_path}")
    print(f"  Eval data:   {eval_path}")
    print(f"  Sampler cfg: {sampler_cfg}")
    print()

    t0 = time.time()
    optimized = optimize(
        str(agent_path),
        eval_data_path=str(eval_path),
        sampler_config_path=str(sampler_cfg) if sampler_cfg.exists() else None,
        agent_name="sonnet-claude",
    )
    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"OPTIMIZATION COMPLETE — {_fmt_duration(elapsed)}")
    print(f"{'=' * 60}")
    print(f"  Prompt length: {len(optimized)} chars")
    print()

    # Save to prompts file
    from datetime import datetime
    prompts_file = Path("examples/multi_model_agents/prompts/sonnet_prompts.py")
    content = prompts_file.read_text()

    entry_lines = [
        '    "wrangler_v3": {',
        f'        "prompt": """{optimized}""",',
        '        "source": "wrangler sequential GEPA optimization",',
        '        "eval_cases": 40,',
        '        "judge_model": "gemini-2.5-pro",',
        '        "notes": "Solo re-run with fresh auth, 40-case evalset, train/val split",',
        f'        "timestamp": "{datetime.now().isoformat()}",',
        '    },',
    ]
    new_entry = "\n".join(entry_lines)

    # Remove old wrangler_v3 if present (from the failed run)
    if '"wrangler_v3"' in content:
        import re
        content = re.sub(
            r'    "wrangler_v3": \{.*?\},\n',
            '',
            content,
            flags=re.DOTALL,
        )

    closing = content.rstrip()
    insert_pos = closing.rfind("}")
    updated = closing[:insert_pos] + new_entry + "\n}\n"
    prompts_file.write_text(updated)
    print(f"  Saved wrangler_v3 to {prompts_file}")


if __name__ == "__main__":
    main()
