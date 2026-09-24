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
mistaken for an optimization effect. Measured on campaign 09: the
train-versus-validation ``safety_v1`` gap came out **negative on all three
arms**, including the CONTROL arm (**-0.1146**), which runs no optimize stage
at all and therefore cannot overfit. The control's gap was *not* uniquely
large — it sits between the two optimized arms (-0.0333 and -0.1456), so the
arms are not ordered by how much optimization they received. **No overfitting
was observed in either direction**; the gap is subset composition, and an
unstratified test set would put the same artifact underneath every held-out
number this repo publishes from now on. Measured in
``docs/analysis/2026-09-23-train-validation-composition.md``.

So the partition is stratified on ``(tier, category)``, the split is seeded and
checked in rather than recomputed per run (a resampled split is a different
experiment every time), and these tests assert:

1. the partition is a partition — every case exactly once, no overlaps;
2. the GEPA case ids this module builds are the ids ``converter`` builds for
   the same cases, and are the 1-based ids actually in the checked-in sampler
   configs (the off-by-one guard: eval artifacts speak 0-based ``case_index``,
   GEPA speaks ``case_<i+1>_...``);
3. no ``(tier, category)`` cell is skewed towards any partition;
4. every way ``partitions.yaml`` can disagree with ``eval_cases.yaml`` is
   rejected with a sentence naming the file, not a bare ``IndexError``.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest
import yaml

from wrangler.core.converter import generate_gepa_evalset
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
    # sorted(allocated) == range(N) already entails distinctness, so there is
    # deliberately no second "no duplicates" assertion here -- it could not
    # fail. Duplicates are covered by the rejection test further down, which
    # builds a file that actually contains one.
    p = load_partitions()
    allocated = [i for part in PARTITIONS for i in p[part]]
    assert sorted(allocated) == list(range(N_CASES))


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


# Every shape the two id builders have to agree on. The `complexity` row is
# the one that mattered: `converter.py` reads `tier or complexity` and this
# module read `tier` only, so a `complexity`-only eval set -- which
# `converter._sample_balanced` explicitly supports and buckets on -- produced
# `case_2_low_search` from the converter and `case_2` here.
PARITY_CASES = [
    {"prompt": "tier and category", "tier": "low", "category": "search"},
    {"prompt": "complexity instead of tier", "complexity": "low", "category": "search"},
    {"prompt": "tier, no category", "tier": "high"},
    {"prompt": "complexity, no category", "complexity": "medium"},
    {"prompt": "category, no tier", "category": "booking"},
    {"prompt": "bare"},
]


def test_case_ids_agree_with_the_converter_on_the_same_cases(tmp_path):
    """A divergent id silently re-labels cases between GEPA and the split.

    Runs one case list through BOTH builders and compares the output, rather
    than comparing either against a checked-in artifact: the sampler configs
    are frozen, so they keep agreeing with whatever generated them while
    `converter.py` drifts. This is the `test_shared_source_drift.py` pattern --
    compare behaviour, not source, because the two are deliberately not
    textually identical.

    `balanced=False` and `count=len(cases)` because the converter enumerates
    the cases it *selected*: under its own defaults (count=15, balanced=True)
    its `i` counts positions in a sampled subset while `case_ids()` counts
    positions in the file, and the two are only comparable unsampled.
    """
    written = generate_gepa_evalset(
        PARITY_CASES,
        tmp_path,
        count=len(PARITY_CASES),
        balanced=False,
    )
    from_converter = [c["eval_id"] for c in json.loads(Path(written).read_text())["eval_cases"]]

    assert case_ids(PARITY_CASES) == from_converter
    # Pin the shapes too, so a change that breaks BOTH sides identically still
    # shows up as a diff a reviewer has to approve.
    assert from_converter == [
        "case_1_low_search",
        "case_2_low_search",
        "case_3_high",
        "case_4_medium",
        "case_5",
        "case_6",
    ]


def test_case_ids_agree_with_the_converter_on_the_real_eval_set(tmp_path):
    """The parity above, on the 64 cases that actually ship."""
    cases = load_eval_cases()
    written = generate_gepa_evalset(cases, tmp_path, count=len(cases), balanced=False)
    from_converter = [c["eval_id"] for c in json.loads(Path(written).read_text())["eval_cases"]]
    assert case_ids(cases) == from_converter


def test_gepa_ids_are_one_based_and_match_the_checked_in_sampler_configs():
    """The off-by-one guard, against real artifacts rather than the converter.

    Distinct from the parity test above: these configs are frozen files whose
    ids were generated at some point in the past, so they catch `eval_cases.
    yaml` drifting out from under the ids GEPA is already configured with --
    something running both builders today cannot see.
    """
    ids = case_ids()
    assert ids[0].startswith("case_1_")
    assert len(ids) == N_CASES
    assert len(set(ids)) == N_CASES, "case ids are not unique"

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
            # of the proportional share, so a legitimate deviation is strictly
            # LESS than one whole case. The epsilon is subtracted, not added:
            # `>= 1.0 + 1e-9` admitted a deviation of exactly 1.0, which for a
            # cell whose proportional share is a whole number (say 8 members,
            # ideal 1.5/1.5... or 16 members, ideal exactly 3) is a real
            # one-case skew, not rounding.
            if abs(actual - ideal) > 1.0 - 1e-9:
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


def test_stratified_split_honours_a_complexity_only_case_list():
    """`complexity` is the converter's alternative spelling of `tier`.

    Without the fallback in `stratum_of`, all sixteen cases here collapse into
    one `("", "a")` cell and the split stops being stratified on tier at all
    -- silently, because every assertion about totals still passes. Asserted
    as an equivalence against the `tier` spelling rather than by inspecting
    the result, so it cannot pass by luck of the shuffle.
    """
    spelt_complexity = [{"complexity": t, "category": "a"} for t in ["low"] * 8 + ["high"] * 8]
    spelt_tier = [{"tier": c["complexity"], "category": "a"} for c in spelt_complexity]
    assert stratified_split(spelt_complexity) == stratified_split(spelt_tier)
    # ...and that split really is per-tier, not one 16-case cell.
    assert stratified_split(spelt_tier) != stratified_split([{"category": "a"} for _ in spelt_tier])


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


# ---------------------------------------------------------------------------
# Every way the file can disagree with the eval set is rejected, with a
# sentence. This is where the module's guarantee actually lives: a partition
# that silently mis-reads is worse than no partition, because a held-out
# number computed over the wrong case set still looks like a number.
# ---------------------------------------------------------------------------


def _corrupt(tmp_path, mutate) -> Path:
    """The real partitions.yaml with one thing broken."""
    raw = yaml.safe_load((EVAL_DATA_DIR / "partitions.yaml").read_text())
    mutate(raw)
    bad = tmp_path / "partitions.yaml"
    bad.write_text(yaml.safe_dump(raw))
    return bad


def test_load_partitions_rejects_an_id_index_mismatch(tmp_path):
    """A recorded id that no longer matches its position: the eval set moved."""

    def mutate(raw):
        raw["partitions"]["train"][0]["id"] = "case_999_low_bogus"

    with pytest.raises(ValueError, match="case_999_low_bogus"):
        load_partitions(_corrupt(tmp_path, mutate))


def test_load_partitions_names_the_missing_case_when_one_is_dropped(tmp_path):
    """ "63 distinct, the eval set has 64" left the reader to find the gap."""
    raw = yaml.safe_load((EVAL_DATA_DIR / "partitions.yaml").read_text())
    dropped = raw["partitions"]["test"][-1]["index"]

    def mutate(r):
        r["partitions"]["test"].pop()

    with pytest.raises(ValueError, match=rf"case_index \[{dropped}\] are in no partition"):
        load_partitions(_corrupt(tmp_path, mutate))


def test_load_partitions_names_a_case_that_is_in_two_partitions(tmp_path):
    """The old message read "64 case slots ... the eval set has 64" -- which
    names two equal numbers and looks like agreement."""
    raw = yaml.safe_load((EVAL_DATA_DIR / "partitions.yaml").read_text())
    shared = raw["partitions"]["test"][0]

    def mutate(r):
        r["partitions"]["train"].append(dict(shared))

    with pytest.raises(
        ValueError,
        match=rf"case_index \[{shared['index']}\] appear in more than one partition",
    ):
        load_partitions(_corrupt(tmp_path, mutate))


def test_load_partitions_rejects_an_unknown_partition_name(tmp_path):
    """A typo'd or invented partition would otherwise be silently ignored,
    and its cases would read as missing from the split rather than misfiled."""

    def mutate(raw):
        raw["partitions"]["holdout"] = [{"index": 0, "id": case_ids()[0]}]

    with pytest.raises(ValueError, match=r"unknown partition\(s\): holdout"):
        load_partitions(_corrupt(tmp_path, mutate))


def test_load_partitions_rejects_a_file_missing_a_whole_partition(tmp_path):
    """Without `test:` there is no held-out set, which is the point of this
    module -- it must not degrade to a two-way split."""

    def mutate(raw):
        del raw["partitions"]["test"]

    with pytest.raises(ValueError, match=r"missing partition\(s\): test"):
        load_partitions(_corrupt(tmp_path, mutate))


def test_load_partitions_explains_itself_when_the_eval_set_shrinks(tmp_path):
    """THE headline failure mode: eval_cases.yaml loses cases.

    `ids[index]` used to raise a bare `IndexError: list index out of range`
    from inside the loop -- no file named, no explanation -- for the exact
    accident this module was written to catch. pytest.raises(ValueError) does
    not catch IndexError, so this test fails if that regresses.
    """
    smaller = tmp_path / "eval_cases.yaml"
    smaller.write_text(yaml.safe_dump(load_eval_cases()[:10]))
    with pytest.raises(ValueError, match=r"holds 10 cases .*eval set moved under the split"):
        load_partitions(cases_path=smaller)


def test_load_partitions_rejects_a_yaml_file_that_is_not_a_mapping(tmp_path):
    """A bare list used to raise `AttributeError: 'list' object has no
    attribute 'get'`, which reads like a bug in the loader."""
    bad = tmp_path / "partitions.yaml"
    bad.write_text("- {index: 0}\n- {index: 1}\n")
    with pytest.raises(ValueError, match="not a partitions file"):
        load_partitions(bad)


def test_load_partitions_rejects_a_partitions_block_that_is_not_a_mapping(tmp_path):
    bad = tmp_path / "partitions.yaml"
    bad.write_text("partitions:\n  - train\n  - validation\n  - test\n")
    with pytest.raises(ValueError, match="must map each partition name"):
        load_partitions(bad)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("case_2_low_search", "needs an 'index:' key"),
        ({"id": "case_2_low_search"}, "needs an 'index:' key"),
        ({"index": None}, "not an integer case_index"),
        ({"index": "two"}, "not an integer case_index"),
        ({"index": [2]}, "not an integer case_index"),
        ({"index": -1}, "eval set moved under the split"),
    ],
)
def test_load_partitions_rejects_a_malformed_entry(tmp_path, entry, expected):
    """`entry["index"]` used to raise KeyError/ValueError with no file path."""

    def mutate(raw):
        raw["partitions"]["train"][0] = entry

    with pytest.raises(ValueError, match=expected):
        load_partitions(_corrupt(tmp_path, mutate))


def test_every_rejection_names_the_offending_file(tmp_path):
    """A message without the path is useless when three eval sets are in play."""

    def mutate(raw):
        raw["partitions"]["train"][0]["id"] = "case_999_low_bogus"

    bad = _corrupt(tmp_path, mutate)
    with pytest.raises(ValueError, match="moved under the split") as exc:
        load_partitions(bad)
    assert str(bad) in str(exc.value)


# ---------------------------------------------------------------------------
# Round trips
# ---------------------------------------------------------------------------


def test_rendered_yaml_round_trips_through_load_partitions(tmp_path):
    split = stratified_split(seed=11)
    out = tmp_path / "partitions.yaml"
    out.write_text(render_partitions_yaml(split, seed=11))
    assert load_partitions(out) == split


def test_a_partitions_file_for_a_different_eval_set_can_be_validated(tmp_path):
    """What `cases_path` is for.

    Without it the loader always reconstructs ids from the repo's own
    eval_cases.yaml, so a partitions file written for any other eval set
    cannot be checked at all -- and checking it against the wrong eval set
    reports drift that is really just the wrong pairing.
    """
    cases = [{"prompt": f"q{i}", "tier": "low", "category": "a"} for i in range(8)]
    cases += [{"prompt": f"q{i}", "tier": "high", "category": "b"} for i in range(8)]
    evalset = tmp_path / "eval_cases.yaml"
    evalset.write_text(yaml.safe_dump(cases))

    split = stratified_split(cases)
    out = tmp_path / "partitions.yaml"
    out.write_text(render_partitions_yaml(split, cases_path=evalset))

    assert load_partitions(out, cases_path=evalset) == split
    # And pairing it with the wrong eval set must be rejected, not accepted.
    with pytest.raises(ValueError, match="moved under the split"):
        load_partitions(out)
