"""The train / validation / test partition of the eval set.

GEPA trains on ``train``, selects on ``validation``, and must never see
``test`` — that partition exists so a reported number can be measured on cases
no optimizer stage has read. Before it existed, GEPA trained on 49 of the 64
cases while ``eval_before``/``eval_after`` scored all 64, so every published
delta was partly measured on training data.

**TWO SPLITS LIVE HERE AND THEY ARE NOT THE SAME THING.**

* :func:`stratified_split` — the three-way 40/12/12 partition built for the
  held-out-test-set plan, written to ``partitions.yaml``.
* :func:`gepa_split` — GEPA's own two-way 34/30 train/validation split, applied
  to every ``sampler_config.json`` by ``scripts/apply_gepa_split.py``.

**The ``test`` partition is NOT held out from GEPA's validation subset**, and that
is deliberate rather than an oversight. The plan that reserved it was stopped at
its own gate on 2026-09-24: 12 cases give an MDE of 0.2634 against the +0.0952
effect this project reports, and handing that partition all 64 cases still falls
short on 3 of 5 metrics, so no partition size makes it usable. The qualitative
fallback — using it to detect overfitting — is confounded too, since the control
arm, which cannot overfit, showed the largest train/validation gap. Nothing in
the campaign path reads ``partitions.yaml``; the only consumer is
``scripts/partition_mde.py``, the gate that measured it away. Do not re-impose
the reservation without new evidence; it costs cases and buys nothing.

**The three-way split is checked in, not recomputed.** ``examples/multi_model_agents/
eval_data/partitions.yaml`` is the source of truth; :func:`stratified_split` is
how it was made and is seeded so it can be regenerated and audited::

    uv run python -m wrangler.core.partitions            # print
    uv run python -m wrangler.core.partitions --write    # rewrite the file

A split resampled at run time would make every campaign a different
experiment, and cross-campaign comparison is most of what this repo does.

**It is stratified on ``(tier, category)``, and that is not cosmetic.** The old
49/15 split was not stratified — train covered 18 cells, validation 11 — and
that difference is large enough to be read as a result. On campaign 09 the
``safety_v1`` train-versus-validation gap came out **negative on all three
arms**, including the CONTROL arm (-0.1146), which runs no optimize stage and
therefore cannot overfit. The control's gap was not uniquely large: it sits
between the two optimized arms (-0.0333 and -0.1456). That ordering — arms not
ranked by how much optimization they received — is the evidence that the gap is
subset composition rather than overfitting, and **no overfitting was observed
in either direction**. An unstratified test set would put the same composition
artifact underneath every held-out number from here on. Allocation below is
integer apportionment per cell, so each partition gets the floor or the ceiling
of its proportional share of every cell and never more. Measured in
``docs/analysis/2026-09-23-train-validation-composition.md``.

**Two positional id schemes meet here.** Eval result artifacts record
``case_index``, the 0-based position in ``eval_cases.yaml``. GEPA (see
``converter.generate_gepa_evalset``) names the same case ``case_{i+1}_{tier}_
{category}`` — 1-based — and those are the ids in every ``*_opt/
sampler_config.json``. Indices in ``partitions.yaml`` are 0-based; the ``id``
recorded beside each one is the GEPA form, and :func:`load_partitions` refuses
a file where the two disagree.

**What that id check does and does not guarantee.** It catches any change to
``eval_cases.yaml`` that puts a different ``(tier, category)`` at a recorded
position — an insertion, a deletion, a truncation, or a reorder across cells.
It does **not** catch a permutation *within* a cell: swapping two
``low_search`` rows leaves every id intact while silently moving both cases
between partitions. Closing that would take a content digest per entry, which
was considered and rejected — a digest also fires on an in-place edit to a
case's wording, where the partition is still entirely correct, so its common
case would be a false alarm, and the habit that teaches (regenerate to silence
it) would silence the real alarm too.
"""

from __future__ import annotations

import random
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "DEFAULT_FRACTIONS",
    "GEPA_SPLIT_SEED",
    "GEPA_VALIDATION_SIZE",
    "PARTITIONS",
    "SPLIT_SEED",
    "case_ids",
    "gepa_split",
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

#: GEPA's train/validation split is separate from the three-way partition above, and so is
#: its seed: re-rolling one must not silently re-roll the other, because they invalidate
#: different things -- the MDE gate's pinned verdict, and every campaign's comparability.
GEPA_SPLIT_SEED = 20260924

#: Validation cases for GEPA's candidate selection. Raised from the hand-written 15 on
#: 2026-09-24. 15 gave the selection signal 1/15 resolution and left 7 of 18 strata
#: unrepresented; two of three archived runs saturated it outright. 30 was chosen over 25 by
#: Jordan, trading a train pool of 34 for the resolution.
GEPA_VALIDATION_SIZE = 30

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
    """The ``(tier, category)`` cell a case belongs to.

    ``complexity`` is the accepted alternative spelling of ``tier`` — see
    ``converter.generate_gepa_evalset``, which reads
    ``case.get("tier") or case.get("complexity")``, and
    ``converter._sample_balanced``, which buckets on ``complexity``. Every
    case in ``eval_cases.yaml`` currently sets ``tier``, so the fallback is
    inert today; without it a ``complexity``-only eval set would stratify into
    one giant ``("", category)`` cell here while GEPA named the same cases
    ``case_N_low_search``.
    """
    tier = case.get("tier", "") or case.get("complexity", "")
    return (str(tier), str(case.get("category", "")))


def case_ids(cases: Sequence[Case] | None = None) -> list[str]:
    """The GEPA-style, **1-based** case ids in eval-file order.

    ``case_ids()[n]`` is the id of the case whose 0-based ``case_index`` is
    ``n``, and the ids are the ones in the checked-in sampler configs.

    Built by the same rule as ``converter.generate_gepa_evalset``, and
    ``tests/test_partitions.py`` runs the two over one case list to keep them
    in step. They agree **on a full, unsampled case list only**: the converter
    enumerates the cases it *selected*, and it defaults to ``count=15,
    balanced=True`` — the call in ``optimizer.py`` takes those defaults — so
    under sampling its ``i`` counts positions in the subset while ours counts
    positions in the file.
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


def stratified_split(
    cases: Sequence[Case] | None = None,
    *,
    seed: int = SPLIT_SEED,
) -> dict[str, list[int]]:
    """Partition case indices across ``PARTITIONS``, balanced per stratum.

    Every ``(tier, category)`` cell is apportioned independently: each
    partition receives ``floor(cell_size * fraction)`` cases, and the leftover
    seats go to whichever partitions are furthest below their running global
    quota. Each partition therefore lands within one whole case of its
    proportional share of *every* cell, while the totals still track
    :data:`DEFAULT_FRACTIONS`.

    The fractions are fixed rather than a parameter on purpose: a second set
    of them is a second split, and a second split is a different experiment
    that cannot be compared with any campaign that ran before it. Change
    :data:`DEFAULT_FRACTIONS` and regenerate, so the change lands in a commit.

    ``seed`` only chooses *which* member of a cell goes where, never how many.

    Returns 0-based case indices, sorted, keyed by partition name.
    """
    if cases is None:
        cases = load_eval_cases()
    fracs = DEFAULT_FRACTIONS

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


def gepa_split(
    cases: Sequence[Case] | None = None,
    *,
    validation_size: int = GEPA_VALIDATION_SIZE,
    seed: int = GEPA_SPLIT_SEED,
) -> dict[str, list[int]]:
    """GEPA's own train/validation split — a *different* split from :func:`stratified_split`.

    **Why a second function rather than new fractions.** :func:`stratified_split` produces the
    three-way 40/12/12 partition built for the held-out-test-set plan, and
    ``scripts/partition_mde.py`` pins its output by md5. Re-pointing ``DEFAULT_FRACTIONS`` at
    GEPA's split would silently move that published NO-GO verdict. The two splits answer
    different questions and are kept apart on purpose.

    **What this fixes.** The hand-written split in ``sampler_config.json`` was 49 train / 15
    validation and unstratified: 7 of 18 ``(tier, category)`` cells never appeared in validation,
    so no candidate was ever *selected* on them. See
    ``docs/analysis/2026-09-24-validation-subset-scoping.md``.

    **Full coverage on both sides is impossible here, and the shortfall is the eval set's.**
    Four strata contain exactly one case (``low/expense``, ``medium/error_handling``,
    ``high/booking``, ``high/cancellation``). On a disjoint split that case is either reflected
    on or selected on, never both. Singletons are left in **train**, because a lone case is a
    real diagnostic for reflection and only ~1/30 of a validation score. Every stratum with two
    or more cases is guaranteed at least one case on **each** side, so both sides see 14 of 18.

    Returns 0-based case indices, sorted, keyed by partition name.
    """
    if cases is None:
        cases = load_eval_cases()

    strata: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, case in enumerate(cases):
        strata[stratum_of(case)].append(i)

    rng = random.Random(seed)
    order = sorted(strata)

    # Floor: one validation seat for every stratum that can spare one.
    floor = {s: (1 if len(strata[s]) >= 2 else 0) for s in order}
    # Ceiling: never empty a stratum out of train.
    ceiling = {s: max(len(strata[s]) - 1, 0) for s in order}

    low, high = sum(floor.values()), sum(ceiling.values())
    if not low <= validation_size <= high:
        raise ValueError(
            f"validation_size={validation_size} is outside [{low}, {high}] for this eval set: "
            f"{len(order)} strata, {len(cases)} cases, and every stratum with 2+ cases must "
            f"keep at least one case on each side."
        )

    counts = dict(floor)
    # Largest-remainder over the seats left after the floor, proportional to the
    # spare capacity of each stratum so big cells absorb most of the remainder.
    spare = {s: ceiling[s] - floor[s] for s in order}
    total_spare = sum(spare.values())
    remaining = validation_size - low
    if remaining and total_spare:
        exact = {s: remaining * spare[s] / total_spare for s in order}
        for s in order:
            counts[s] += int(exact[s])
        short = validation_size - sum(counts.values())
        # Ties break on stratum name, never on dict iteration order.
        ranked = sorted(order, key=lambda s: (-(exact[s] - int(exact[s])), s))
        for s in ranked:
            if short <= 0:
                break
            if counts[s] < ceiling[s]:
                counts[s] += 1
                short -= 1

    out: dict[str, list[int]] = {"train": [], "validation": []}
    for s in order:
        members = list(strata[s])
        rng.shuffle(members)
        take = counts[s]
        out["validation"].extend(members[:take])
        out["train"].extend(members[take:])
    return {part: sorted(idxs) for part, idxs in out.items()}


# ---------------------------------------------------------------------------
# Reading and writing the checked-in file
# ---------------------------------------------------------------------------


def load_partitions(
    path: str | Path = DEFAULT_PARTITIONS_PATH,
    *,
    cases_path: str | Path = DEFAULT_EVAL_CASES_PATH,
) -> dict[str, list[int]]:
    """0-based case indices per partition, from the checked-in split.

    Raises if the file is not a partition of ``cases_path``, or if any
    recorded ``id`` disagrees with the id reconstructed from that index — see
    the module docstring for exactly which drifts that does and does not
    catch.

    ``cases_path`` is what makes a non-default ``path`` usable: a partitions
    file describes one eval set, and validating it against a different one
    would report drift that is really just the wrong pairing.

    Every rejection names the file and says what moved. The alternative is a
    bare ``IndexError`` or ``KeyError`` from inside the loop, and "the eval set
    moved under the split" is the exact accident this module exists to catch,
    so it is the last place that should fail without a sentence.
    """
    with open(path) as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError(  # noqa: TRY004  (file content, not a call argument)
            f"{path}: expected a mapping with a 'partitions:' block, got "
            f"{type(raw).__name__} -- this is not a partitions file"
        )
    blocks = raw.get("partitions") or {}
    if not isinstance(blocks, Mapping):
        raise ValueError(  # noqa: TRY004
            f"{path}: 'partitions' must map each partition name to a list of "
            f"cases, got {type(blocks).__name__}"
        )
    missing = sorted(set(PARTITIONS) - set(blocks))
    if missing:
        raise ValueError(f"{path}: missing partition(s): {', '.join(missing)}")
    unknown = sorted(set(blocks) - set(PARTITIONS))
    if unknown:
        raise ValueError(f"{path}: unknown partition(s): {', '.join(unknown)}")

    ids = case_ids(load_eval_cases(cases_path))
    out: dict[str, list[int]] = {}
    for part in PARTITIONS:
        indices = []
        for position, entry in enumerate(blocks[part] or []):
            if not isinstance(entry, Mapping) or "index" not in entry:
                raise ValueError(
                    f"{path}: {part}[{position}] is {entry!r}; every entry needs an "
                    f"'index:' key holding a 0-based case_index"
                )
            try:
                index = int(entry["index"])
            except (TypeError, ValueError):
                raise ValueError(
                    f"{path}: {part}[{position}] has index {entry['index']!r}, "
                    f"which is not an integer case_index"
                ) from None
            if not 0 <= index < len(ids):
                raise ValueError(
                    f"{path}: {part}[{position}] refers to case_index {index}, but "
                    f"{cases_path} holds {len(ids)} cases (0..{len(ids) - 1}) -- the "
                    f"eval set moved under the split"
                )
            recorded = entry.get("id")
            if recorded is not None and recorded != ids[index]:
                raise ValueError(
                    f"{path}: {part} case_index {index} is recorded as {recorded!r} "
                    f"but {cases_path} says {ids[index]!r} -- the eval set moved "
                    f"under the split"
                )
            indices.append(index)
        out[part] = sorted(indices)

    allocated = sorted(i for part in PARTITIONS for i in out[part])
    if allocated != list(range(len(ids))):
        duplicated = sorted(i for i, n in Counter(allocated).items() if n > 1)
        absent = sorted(set(range(len(ids))) - set(allocated))
        faults = []
        if duplicated:
            faults.append(f"case_index {duplicated} appear in more than one partition")
        if absent:
            faults.append(f"case_index {absent} are in no partition")
        raise ValueError(
            f"{path}: not a partition of the {len(ids)} cases in {cases_path}: " + "; ".join(faults)
        )
    return out


def render_partitions_yaml(
    split: Mapping[str, Sequence[int]],
    *,
    seed: int = SPLIT_SEED,
    cases_path: str | Path = DEFAULT_EVAL_CASES_PATH,
) -> str:
    """Render a split as the checked-in ``partitions.yaml`` text.

    ``cases_path`` must be the eval set ``split`` was computed over; the ids
    written beside each index come from it.
    """
    ids = case_ids(load_eval_cases(cases_path))
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
        "# unstratified split is measurable -- on campaign 09 the train-vs-",
        "# validation safety_v1 gap was negative on ALL THREE arms, including",
        "# the control (-0.1146), which runs no optimize stage and so cannot",
        "# overfit. The control's gap was not the largest; it sits between the",
        "# two optimized arms (-0.0333, -0.1456). No overfitting was observed --",
        "# the gap is subset composition. Measured in",
        "# docs/analysis/2026-09-23-train-validation-composition.md.",
        "#",
        "# index is the 0-based case_index used by eval artifacts; id is the",
        "# 1-based GEPA id used by sampler_config.json. Both are written so the",
        "# loader can reject a file that has drifted from eval_cases.yaml.",
        "#",
        "# seed and fractions below RECORD how this file was made; the loader",
        "# does not read them back. They are provenance, not configuration --",
        "# to change either, edit wrangler/core/partitions.py and regenerate.",
        f"# seed {seed} -- {sizes}",
        "",
        f"seed: {seed}",
        "fractions:",
    ]
    lines += [f"  {part}: {DEFAULT_FRACTIONS[part]:.6g}" for part in PARTITIONS]
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
