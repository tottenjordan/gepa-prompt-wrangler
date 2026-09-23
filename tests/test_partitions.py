"""Guards on the three-way train / validation / test partition of the eval set.

WHY THIS EXISTS
---------------
Until this partition landed, GEPA trained on 49 of the 64 eval cases and
validated on the other 15, while ``eval_before``/``eval_after`` scored all 64.
Every published number was therefore measured, in part, on cases the optimizer
had been trained against. The ``test`` partition is the fix: cases no optimizer
stage may ever read.

WHY THE SPLIT MUST BE STRATIFIED
--------------------------------
The old 49/15 split was not. Train covered 18 distinct ``(tier, category)``
cells and validation only 11, and that difference is large enough to be
mistaken for an optimization effect. Measured on a real campaign: the CONTROL
arm — which runs no optimize stage at all and therefore cannot overfit —
showed a train-versus-validation score gap of **-0.1146**, larger than either
optimized arm's gap and in the opposite direction. The whole of that gap was
subset composition. An unstratified test set would put the same artifact
underneath every held-out number this repo publishes from now on.

So the partition is stratified on ``(tier, category)``, the split is seeded and
checked in rather than recomputed per run (a resampled split is a different
experiment every time), and these tests assert:

1. the partition is a partition — every case exactly once, no overlaps;
2. the GEPA case ids reconstructed from the YAML list order are the 1-based
   ids that are actually in the checked-in sampler configs (the off-by-one
   guard: eval artifacts speak 0-based ``case_index``, GEPA speaks
   ``case_<i+1>_...``);
3. no ``(tier, category)`` cell is skewed towards any partition.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest
import yaml

from wrangler.core.partitions import (
    PARTITIONS,
    case_ids,
    load_eval_cases,
    load_partitions,
    render_partitions_yaml,
    stratified_split,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DATA_DIR = REPO_ROOT / "examples" / "multi_model_agents" / "eval_data"
SAMPLER_CONFIGS = sorted(
    (REPO_ROOT / "examples" / "multi_model_agents" / "agents").glob("*_opt/sampler_config.json")
)

N_CASES = 64


# ---------------------------------------------------------------------------
# 1. It is a partition
# ---------------------------------------------------------------------------


def test_every_case_is_in_exactly_one_partition():
    p = load_partitions()
    allocated = [i for part in PARTITIONS for i in p[part]]
    assert sorted(allocated) == list(range(N_CASES))
    assert len(allocated) == len(set(allocated)), "a case appears in two partitions"


def test_partition_names_are_exactly_the_three_declared_ones():
    assert set(load_partitions()) == set(PARTITIONS)
    assert len(PARTITIONS) == 3


def test_partition_sizes_are_close_to_the_intended_40_12_12():
    sizes = {part: len(idxs) for part, idxs in load_partitions().items()}
    assert sum(sizes.values()) == N_CASES
    # Stratification adjusts exact counts; balance across cells matters more
    # than hitting the totals, so allow a couple of cases of slack.
    assert abs(sizes["train"] - 40) <= 2, sizes
    assert abs(sizes["validation"] - 12) <= 2, sizes
    assert abs(sizes["test"] - 12) <= 2, sizes


# ---------------------------------------------------------------------------
# 2. The id scheme
# ---------------------------------------------------------------------------


def test_gepa_ids_are_one_based_and_match_the_converter():
    ids = case_ids()
    assert ids[0].startswith("case_1_")
    assert len(ids) == N_CASES
    assert len(set(ids)) == N_CASES, "case ids are not unique"

    # The real guard against an off-by-one: the ids we reconstruct from YAML
    # list order must be exactly the ids the checked-in sampler configs use.
    assert SAMPLER_CONFIGS, "no sampler_config.json found to check against"
    for path in SAMPLER_CONFIGS:
        cfg = json.loads(path.read_text())
        union = set(cfg["train_eval_case_ids"]) | set(cfg["validation_eval_case_ids"])
        assert union == set(ids), f"{path.parent.name} case ids disagree with the reconstruction"


def test_case_ids_are_positionally_aligned_with_zero_based_case_index():
    # Eval artifacts record case_index (0-based); GEPA records case_<i+1>_...
    # case_ids()[N] must be the id of the case whose case_index is N.
    cases = load_eval_cases()
    ids = case_ids()
    for n, case in enumerate(cases):
        assert ids[n] == f"case_{n + 1}_{case['tier']}_{case['category']}"


def test_case_ids_track_the_yaml_rather_than_being_frozen():
    # Passing a different case list must produce different ids -- i.e. the ids
    # are derived, not a checked-in constant that would rot.
    made_up = [{"tier": "low", "category": "zzz"}, {"tier": "high", "category": "zzz"}]
    assert case_ids(made_up) == ["case_1_low_zzz", "case_2_high_zzz"]


# ---------------------------------------------------------------------------
# 3. Stratification
# ---------------------------------------------------------------------------


def _cells() -> dict[tuple[str, str], list[int]]:
    cells: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, case in enumerate(load_eval_cases()):
        cells[(case["tier"], case["category"])].append(i)
    return dict(cells)


def test_the_split_is_stratified_on_tier_and_category():
    parts = load_partitions()
    where = {i: part for part, idxs in parts.items() for i in idxs}

    # Compare each cell against the split's OWN global shares rather than
    # against the module's configured fractions, so this measures balance and
    # not agreement with a constant that could be wrong in both places.
    global_share = {part: len(idxs) / N_CASES for part, idxs in parts.items()}

    offenders = []
    for cell, members in sorted(_cells().items()):
        counts = Counter(where[i] for i in members)
        for part in PARTITIONS:
            ideal = len(members) * global_share[part]
            actual = counts.get(part, 0)
            # Integer apportionment can only ever be the floor or the ceiling
            # of the proportional share; anything beyond one whole case is a
            # skew, not rounding.
            if abs(actual - ideal) >= 1.0 + 1e-9:
                offenders.append(f"{cell} {part}: {actual} vs proportional {ideal:.2f}")
    assert not offenders, "cells skewed towards a partition:\n" + "\n".join(offenders)


def test_every_multi_case_cell_reaches_more_than_one_partition():
    # A cell big enough to be shared must be shared: the 49/15 split's failure
    # mode was whole cells landing in one side.
    parts = load_partitions()
    where = {i: part for part, idxs in parts.items() for i in idxs}
    for cell, members in sorted(_cells().items()):
        if len(members) >= 4:
            assert len({where[i] for i in members}) > 1, f"cell {cell} is entirely in one partition"


def test_every_shareable_cell_is_represented_in_train():
    # Train is the largest partition; a cell missing from it is a category the
    # optimizer never sees. Singleton cells are exempt -- one case cannot be in
    # three places, and the apportionment only guarantees a train seat from two
    # cases up (floor(2 * 0.625) == 1).
    parts = load_partitions()
    train = set(parts["train"])
    for cell, members in sorted(_cells().items()):
        if len(members) >= 2:
            assert train & set(members), f"cell {cell} is absent from train"


# ---------------------------------------------------------------------------
# The checked-in file is what the seeded function produces
# ---------------------------------------------------------------------------


def test_the_checked_in_file_is_reproducible_from_the_seeded_split():
    # The file is the source of truth; stratified_split is how it was made.
    # If these disagree, one of the two was edited by hand.
    assert stratified_split() == load_partitions()


def test_stratified_split_is_deterministic_and_seed_sensitive():
    assert stratified_split(seed=7) == stratified_split(seed=7)
    assert stratified_split(seed=7) != stratified_split(seed=8)


def test_stratified_split_partitions_an_arbitrary_case_list():
    cases = [{"tier": "low", "category": "a"} for _ in range(10)]
    cases += [{"tier": "high", "category": "b"} for _ in range(6)]
    split = stratified_split(cases)
    allocated = sorted(i for part in PARTITIONS for i in split[part])
    assert allocated == list(range(16))


def test_stratified_split_rejects_fractions_that_do_not_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        stratified_split(fractions={"train": 0.5, "validation": 0.2, "test": 0.2})


def test_stratified_split_rejects_unknown_partition_names():
    with pytest.raises(ValueError, match="holdout"):
        stratified_split(fractions={"train": 0.6, "validation": 0.2, "holdout": 0.2})


# ---------------------------------------------------------------------------
# The file itself
# ---------------------------------------------------------------------------


def test_partitions_file_records_ids_that_agree_with_the_indices():
    # Both are written so a human can read the file; load_partitions() must
    # reject the file if they ever disagree, which is how a re-ordered
    # eval_cases.yaml gets caught instead of silently re-labelling cases.
    raw = yaml.safe_load((EVAL_DATA_DIR / "partitions.yaml").read_text())
    ids = case_ids()
    for part in PARTITIONS:
        for entry in raw["partitions"][part]:
            assert entry["id"] == ids[entry["index"]]


def test_load_partitions_rejects_an_id_index_mismatch(tmp_path):
    raw = yaml.safe_load((EVAL_DATA_DIR / "partitions.yaml").read_text())
    raw["partitions"]["train"][0]["id"] = "case_999_low_bogus"
    bad = tmp_path / "partitions.yaml"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="case_999_low_bogus"):
        load_partitions(bad)


def test_load_partitions_rejects_an_incomplete_partition(tmp_path):
    raw = yaml.safe_load((EVAL_DATA_DIR / "partitions.yaml").read_text())
    raw["partitions"]["test"].pop()
    bad = tmp_path / "partitions.yaml"
    bad.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="63"):
        load_partitions(bad)


def test_rendered_yaml_round_trips_through_load_partitions(tmp_path):
    split = stratified_split(seed=11)
    out = tmp_path / "partitions.yaml"
    out.write_text(render_partitions_yaml(split, seed=11))
    assert load_partitions(out) == split
