#!/usr/bin/env python
"""Write GEPA's stratified train/validation split into every ``sampler_config.json``.

The split those files carried until 2026-09-24 was hand-written: 49 train / 15 validation,
unstratified, with **7 of 18 ``(tier, category)`` strata absent from validation entirely** — so
no candidate was ever selected on them. The 15-case subset also gave the selection signal 1/15
resolution, and two of three archived runs saturated it outright.
See ``docs/analysis/2026-09-24-validation-subset-scoping.md``.

This regenerates it from :func:`wrangler.core.partitions.gepa_split`, so the split is derived
and reproducible rather than typed. Run with no arguments to preview, ``--write`` to apply:

    uv run python scripts/apply_gepa_split.py
    uv run python scripts/apply_gepa_split.py --write

**This re-baselines every campaign.** Changing which cases GEPA selects on changes which prompt
it returns, so results either side of the change are not comparable — the same kind of boundary
as the 2026-09-17 judge re-baseline. Only the two ``*_eval_case_ids`` keys are touched; criteria
and thresholds are left exactly as they are.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wrangler.core.partitions import (  # noqa: E402
    GEPA_VALIDATION_SIZE,
    case_ids,
    gepa_split,
    load_eval_cases,
    stratum_of,
)

CONFIG_GLOB = "examples/multi_model_agents/agents/*_opt/sampler_config.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply; otherwise preview only")
    parser.add_argument("--validation-size", type=int, default=GEPA_VALIDATION_SIZE)
    args = parser.parse_args(argv)

    cases = load_eval_cases()
    ids = case_ids(cases)
    split = gepa_split(cases, validation_size=args.validation_size)
    train = [ids[i] for i in split["train"]]
    validation = [ids[i] for i in split["validation"]]

    all_strata = {stratum_of(c) for c in cases}
    val_strata = {stratum_of(cases[i]) for i in split["validation"]}
    train_strata = {stratum_of(cases[i]) for i in split["train"]}

    print(f"  train      {len(train):>3} cases, {len(train_strata):>2}/{len(all_strata)} strata")
    print(f"  validation {len(validation):>3} cases, {len(val_strata):>2}/{len(all_strata)} strata")
    missing = sorted(all_strata - val_strata)
    if missing:
        # Expected: a stratum holding exactly one case cannot be on both sides of a
        # disjoint split. Anything else here means the apportionment is wrong.
        singletons = {s for s in all_strata if sum(stratum_of(c) == s for c in cases) == 1}
        unexpected = [s for s in missing if s not in singletons]
        print(f"  absent from validation (all singletons): {missing}")
        if unexpected:
            print(f"  UNEXPECTED non-singleton strata missing: {unexpected}", file=sys.stderr)
            return 2

    paths = sorted(ROOT.glob(CONFIG_GLOB))
    if not paths:
        print(f"no sampler configs matched {CONFIG_GLOB}", file=sys.stderr)
        return 2

    changed = 0
    for path in paths:
        cfg = json.loads(path.read_text())
        before = (
            len(cfg.get("train_eval_case_ids", [])),
            len(cfg.get("validation_eval_case_ids", [])),
        )
        if (
            cfg.get("train_eval_case_ids") == train
            and cfg.get("validation_eval_case_ids") == validation
        ):
            print(f"    {path.parent.name:22} already current")
            continue
        changed += 1
        print(
            f"    {path.parent.name:22} {before[0]}/{before[1]} -> {len(train)}/{len(validation)}"
        )
        if args.write:
            cfg["train_eval_case_ids"] = train
            cfg["validation_eval_case_ids"] = validation
            path.write_text(json.dumps(cfg, indent=2) + "\n")

    if not args.write and changed:
        print(f"\n  {changed} file(s) would change. Re-run with --write to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
