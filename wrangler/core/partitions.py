"""The train / validation / test partition of the eval set.

GEPA trains on ``train``, selects on ``validation``, and must never see
``test`` — that partition exists so a reported number can be measured on cases
no optimizer stage has read. Before it existed, GEPA trained on 49 of the 64
cases while ``eval_before``/``eval_after`` scored all 64, so every published
delta was partly measured on training data.

**The split is checked in, not recomputed.** ``examples/multi_model_agents/
eval_data/partitions.yaml`` is the source of truth; :func:`stratified_split` is
how it was made and is seeded so it can be regenerated and audited::

    uv run python -m wrangler.core.partitions            # print
    uv run python -m wrangler.core.partitions --write    # rewrite the file

A split resampled at run time would make every campaign a different
experiment, and cross-campaign comparison is most of what this repo does.

**It is stratified on ``(tier, category)``, and that is not cosmetic.** The old
49/15 split was not stratified — train covered 18 cells, validation 11 — and on
a real campaign the CONTROL arm, which runs no optimize stage and therefore
cannot overfit, showed a train-versus-validation gap of -0.1146: larger than
either optimized arm's, and in the opposite direction. That entire gap was
subset composition being read as an optimization effect. Allocation below is
integer apportionment per cell, so each partition gets the floor or the ceiling
of its proportional share of every cell and never more.

**Two positional id schemes meet here.** Eval result artifacts record
``case_index``, the 0-based position in ``eval_cases.yaml``. GEPA (see
``converter.generate_gepa_evalset``) names the same case ``case_{i+1}_{tier}_
{category}`` — 1-based — and those are the ids in every ``*_opt/
sampler_config.json``. Indices in ``partitions.yaml`` are 0-based; the ``id``
recorded beside each one is the GEPA form, and :func:`load_partitions` refuses
a file where the two disagree, so re-ordering ``eval_cases.yaml`` fails loudly
instead of silently re-labelling cases.
"""

from __future__ import annotations

import random
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "DEFAULT_FRACTIONS",
    "PARTITIONS",
    "SPLIT_SEED",
    "case_ids",
    "load_eval_cases",
    "load_partitions",
    "render_partitions_yaml",
    "stratified_split",
]

#: The three partitions, in the order they are written and reported.
PARTITIONS: tuple[str, ...] = ("train", "validation", "test")

#: Roughly 40 / 12 / 12 of 64. Stratification adjusts the exact counts.
DEFAULT_FRACTIONS: dict[str, float] = {
    "train": 40 / 64,
    "validation": 12 / 64,
    "test": 12 / 64,
}

#: Seed for the checked-in split. Changing it re-partitions the eval set, which
#: invalidates comparison with every campaign run before the change.
SPLIT_SEED = 20260923

_EVAL_DATA_DIR = (
    Path(__file__).resolve().parents[2] / "examples" / "multi_model_agents" / "eval_data"
)
DEFAULT_EVAL_CASES_PATH = _EVAL_DATA_DIR / "eval_cases.yaml"
DEFAULT_PARTITIONS_PATH = _EVAL_DATA_DIR / "partitions.yaml"

Case = Mapping[str, Any]


# ---------------------------------------------------------------------------
# Cases and their ids
# ---------------------------------------------------------------------------


def load_eval_cases(path: str | Path = DEFAULT_EVAL_CASES_PATH) -> list[dict[str, Any]]:
    """The eval cases in file order. Position N is ``case_index`` N.

    Deliberately not ``converter.load_eval_file``: that normalises fields for
    the ADK evalset writers, while everything here is positional and only needs
    ``tier`` and ``category``. Reading the file directly keeps this module's
    notion of "list order" identical to the file's.
    """
    with open(path) as f:
        data = yaml.safe_load(f)
    if isinstance(data, dict) and "eval_cases" in data:
        data = data["eval_cases"]
    if not isinstance(data, list):
        raise ValueError(  # noqa: TRY004  (file content, not a call argument)
            f"Expected a YAML list of eval cases in {path}, got {type(data).__name__}"
        )
    return list(data)


def stratum_of(case: Case) -> tuple[str, str]:
    """The ``(tier, category)`` cell a case belongs to."""
    return (str(case.get("tier", "")), str(case.get("category", "")))


def case_ids(cases: Sequence[Case] | None = None) -> list[str]:
    """The GEPA-style, **1-based** case ids in eval-file order.

    ``case_ids()[n]`` is the id of the case whose 0-based ``case_index`` is
    ``n``. Mirrors ``converter.generate_gepa_evalset``; the ids it returns are
    the ones in the checked-in sampler configs.
    """
    if cases is None:
        cases = load_eval_cases()
    out = []
    for i, case in enumerate(cases):
        tier, category = stratum_of(case)
        if tier and category:
            out.append(f"case_{i + 1}_{tier}_{category}")
        elif tier:
            out.append(f"case_{i + 1}_{tier}")
        else:
            out.append(f"case_{i + 1}")
    return out


# ---------------------------------------------------------------------------
# Making the split
# ---------------------------------------------------------------------------


def _validated_fractions(fractions: Mapping[str, float] | None) -> dict[str, float]:
    if fractions is None:
        return dict(DEFAULT_FRACTIONS)
    unknown = sorted(set(fractions) - set(PARTITIONS))
    if unknown:
        raise ValueError(f"unknown partition name(s): {', '.join(unknown)}")
    missing = sorted(set(PARTITIONS) - set(fractions))
    if missing:
        raise ValueError(f"missing partition name(s): {', '.join(missing)}")
    total = sum(fractions.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"fractions must sum to 1, got {total}")
    return dict(fractions)


def stratified_split(
    cases: Sequence[Case] | None = None,
    *,
    fractions: Mapping[str, float] | None = None,
    seed: int = SPLIT_SEED,
) -> dict[str, list[int]]:
    """Partition case indices across ``PARTITIONS``, balanced per stratum.

    Every ``(tier, category)`` cell is apportioned independently: each
    partition receives ``floor(cell_size * fraction)`` cases, and the leftover
    seats go to whichever partitions are furthest below their running global
    quota. Each partition therefore lands within one whole case of its
    proportional share of *every* cell, while the totals still track the
    requested fractions.

    ``seed`` only chooses *which* member of a cell goes where, never how many.

    Returns 0-based case indices, sorted, keyed by partition name.
    """
    if cases is None:
        cases = load_eval_cases()
    fracs = _validated_fractions(fractions)

    strata: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, case in enumerate(cases):
        strata[stratum_of(case)].append(i)

    rng = random.Random(seed)
    out: dict[str, list[int]] = {part: [] for part in PARTITIONS}
    assigned = dict.fromkeys(PARTITIONS, 0)
    seen = 0

    for stratum in sorted(strata):
        members = list(strata[stratum])
        rng.shuffle(members)
        size = len(members)
        seen += size

        counts = {part: int(size * fracs[part]) for part in PARTITIONS}
        leftover = size - sum(counts.values())
        if leftover:
            # Rank by deficit against the global quota *after* this cell, so
            # the partitions that round up are the ones running short overall.
            # Ties break on the fractional remainder this cell would otherwise
            # drop, then on declaration order -- never on dict iteration order.
            ranked = sorted(
                (
                    (
                        fracs[part] * seen - (assigned[part] + counts[part]),
                        size * fracs[part] - counts[part],
                        -PARTITIONS.index(part),
                        part,
                    )
                    for part in PARTITIONS
                ),
                reverse=True,
            )
            for *_, part in ranked[:leftover]:
                counts[part] += 1

        cursor = 0
        for part in PARTITIONS:
            take = counts[part]
            out[part].extend(members[cursor : cursor + take])
            assigned[part] += take
            cursor += take

    return {part: sorted(idxs) for part, idxs in out.items()}


# ---------------------------------------------------------------------------
# Reading and writing the checked-in file
# ---------------------------------------------------------------------------


def load_partitions(path: str | Path = DEFAULT_PARTITIONS_PATH) -> dict[str, list[int]]:
    """0-based case indices per partition, from the checked-in split.

    Raises if the file is not a partition of the eval set, or if any recorded
    ``id`` disagrees with the id reconstructed from that index — which is what
    catches a re-ordered ``eval_cases.yaml`` before it re-labels a case.
    """
    with open(path) as f:
        raw = yaml.safe_load(f)
    blocks = (raw or {}).get("partitions", {})
    missing = sorted(set(PARTITIONS) - set(blocks))
    if missing:
        raise ValueError(f"{path}: missing partition(s): {', '.join(missing)}")
    unknown = sorted(set(blocks) - set(PARTITIONS))
    if unknown:
        raise ValueError(f"{path}: unknown partition(s): {', '.join(unknown)}")

    ids = case_ids()
    out: dict[str, list[int]] = {}
    for part in PARTITIONS:
        indices = []
        for entry in blocks[part] or []:
            index = int(entry["index"])
            recorded = entry.get("id")
            if recorded is not None and recorded != ids[index]:
                raise ValueError(
                    f"{path}: {part} case_index {index} is recorded as {recorded!r} "
                    f"but eval_cases.yaml says {ids[index]!r} -- the eval set moved "
                    f"under the split"
                )
            indices.append(index)
        out[part] = sorted(indices)

    allocated = sorted(i for part in PARTITIONS for i in out[part])
    if allocated != list(range(len(ids))):
        raise ValueError(
            f"{path}: partitions cover {len(allocated)} case slots "
            f"({len(set(allocated))} distinct); the eval set has {len(ids)}"
        )
    return out


def render_partitions_yaml(
    split: Mapping[str, Sequence[int]],
    *,
    seed: int = SPLIT_SEED,
    fractions: Mapping[str, float] | None = None,
) -> str:
    """Render a split as the checked-in ``partitions.yaml`` text."""
    fracs = _validated_fractions(fractions)
    ids = case_ids()
    sizes = ", ".join(f"{part} {len(split[part])}" for part in PARTITIONS)
    lines = [
        "# Train / validation / test partition of eval_cases.yaml.",
        "#",
        "# GENERATED, AND CHECKED IN ON PURPOSE. Regenerate and audit with:",
        "#   uv run python -m wrangler.core.partitions --write",
        "# Recomputing the split per run would make every campaign a different",
        "# experiment. Changing the seed invalidates comparison with every",
        "# campaign that ran before the change.",
        "#",
        "# Stratified on (tier, category): each partition gets the floor or the",
        "# ceiling of its proportional share of every cell, never more. An",
        "# unstratified split is measurable -- a control arm that cannot overfit",
        "# showed a -0.1146 train-vs-validation gap from composition alone.",
        "#",
        "# index is the 0-based case_index used by eval artifacts; id is the",
        "# 1-based GEPA id used by sampler_config.json. Both are written so the",
        "# loader can reject a file that has drifted from eval_cases.yaml.",
        f"# seed {seed} -- {sizes}",
        "",
        f"seed: {seed}",
        "fractions:",
    ]
    lines += [f"  {part}: {fracs[part]:.6g}" for part in PARTITIONS]
    lines.append("partitions:")
    for part in PARTITIONS:
        lines.append(f"  {part}:")
        lines += [f"    - {{index: {i}, id: {ids[i]}}}" for i in sorted(split[part])]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """Print (or ``--write``) the seeded split."""
    argv = list(sys.argv[1:] if argv is None else argv)
    text = render_partitions_yaml(stratified_split())
    if "--write" in argv:
        DEFAULT_PARTITIONS_PATH.write_text(text)
        print(f"wrote {DEFAULT_PARTITIONS_PATH}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
