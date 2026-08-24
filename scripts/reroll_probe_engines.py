"""Campaign 01 Phase B — redeploy engines in place with identical content.

Phase A found ten byte-identical engines splitting into distinct performance
tiers. Phase B asks the question that decides whether anything can be done
about it: does `update_agent_from_source` with the *same* content reroll an
engine's rate, or does the rate follow the engine id?

If it rerolls, a deploy-time health gate works — probe, and redeploy anything
below threshold. If it does not, something durable attaches to the engine and
the only recourse is a fresh deployment, which is a much sharper question to
put to the service owner.

Usage:
    uv run python scripts/reroll_probe_engines.py \\
        --engine lottery-07=<id> --engine lottery-02=<id>
"""

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_EXAMPLE_ENV = Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents" / ".env"
if _EXAMPLE_ENV.exists():
    load_dotenv(str(_EXAMPLE_ENV), override=True)

from scripts.deploy_probe_arms import (  # noqa: E402
    AGENT_MODULE,
    ARMS,
    INSTRUCTION,
    MIN_INSTANCES,
)
from wrangler.core.deploy import update_agent_from_source  # noqa: E402


def _parse(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError(f"--engine expects name=engine_id, got {spec!r}")
    return tuple(spec.split("=", 1))  # ty: ignore[invalid-return-type]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Redeploy probe engines in place")
    parser.add_argument("--engine", action="append", required=True, type=_parse)
    parser.add_argument(
        "--base",
        default="bare-gemini",
        choices=sorted(ARMS),
        help="Which arm's content to redeploy. Must match how it was deployed.",
    )
    args = parser.parse_args()
    spec = ARMS[args.base]

    ok, failed = {}, {}
    for name, engine_id in args.engine:
        print(f"\n{'=' * 64}\nReroll {name} ({engine_id})\n{'=' * 64}")
        t0 = time.time()
        try:
            update_agent_from_source(
                engine_id=engine_id,
                agent_module=AGENT_MODULE,
                model=spec["model"],
                instruction=INSTRUCTION,
                display_name=f"geap-probe-{name}",
                min_instances=MIN_INSTANCES,
                # Must match the original deploy, or the update silently
                # rebuilds a no-MCP agent *with* toolsets and Phase B stops
                # comparing like with like.
                include_mcp=spec["include_mcp"],
            )
            ok[name] = engine_id
            print(f"  {name}: rerolled ({time.time() - t0:.0f}s)")
        except Exception as exc:
            failed[name] = f"{type(exc).__name__}: {exc}"
            print(f"  {name}: FAILED -- {failed[name]}")

    print(f"\n{'=' * 64}\nREROLLED\n{'=' * 64}")
    for name, engine_id in ok.items():
        print(f"  {name:14s} {engine_id}")
    for name, err in failed.items():
        print(f"  {name:14s} FAILED: {err}")
    if ok:
        line = " ".join(f"--arm {n}={e}" for n, e in ok.items())
        print(f"\nNext:\n  uv run python -m wrangler.tools.boot_probe {line} \\")
        print("      --n 100 --spacing 5 --block-size 25 --run-id lottery_b")
    sys.exit(1 if failed else 0)
