"""Tests for GEPA's train/validation split.

Two things are load-bearing here. The split must not disturb the three-way partition the
held-out-test-set gate pins by md5; and the shipped `sampler_config.json` files must actually
carry what `gepa_split()` produces, or the derivation is decorative and the real split is again
whatever someone typed.
"""

from __future__ import annotations

import glob
import json
from collections import Counter
from pathlib import Path

import pytest

from wrangler.core.partitions import (
    GEPA_VALIDATION_SIZE,
    case_ids,
    gepa_split,
    load_eval_cases,
    stratified_split,
    stratum_of,
)

CONFIGS = sorted(glob.glob("examples/multi_model_agents/agents/*_opt/sampler_config.json"))


@pytest.fixture(scope="module")
def cases():
    return load_eval_cases()


@pytest.fixture(scope="module")
def split(cases):
    return gepa_split(cases)


class TestPartitionIntegrity:
    def test_it_is_a_partition(self, split, cases):
        train, val = set(split["train"]), set(split["validation"])
        assert not train & val
        assert train | val == set(range(len(cases)))

    def test_sizes_match_the_declared_constant(self, split):
        assert len(split["validation"]) == GEPA_VALIDATION_SIZE == 30
        assert len(split["train"]) == 34

    def test_it_is_deterministic(self, cases):
        assert gepa_split(cases) == gepa_split(cases)

    def test_a_different_seed_gives_a_different_split(self, cases):
        assert gepa_split(cases, seed=1) != gepa_split(cases, seed=2)


class TestStratumCoverage:
    """The defect this split exists to fix: 7 of 18 strata never appeared in validation."""

    def test_train_keeps_every_stratum(self, split, cases):
        assert {stratum_of(cases[i]) for i in split["train"]} == {stratum_of(c) for c in cases}

    def test_only_singletons_are_absent_from_validation(self, split, cases):
        """Full coverage on both sides is impossible on a disjoint split — four strata hold
        exactly one case. Anything else missing means the apportionment is wrong, not that
        the eval set is small."""
        sizes = Counter(stratum_of(c) for c in cases)
        missing = {stratum_of(c) for c in cases} - {
            stratum_of(cases[i]) for i in split["validation"]
        }
        assert missing == {s for s, n in sizes.items() if n == 1}
        assert len(missing) == 4

    def test_every_multi_case_stratum_appears_on_both_sides(self, split, cases):
        sizes = Counter(stratum_of(c) for c in cases)
        val = {stratum_of(cases[i]) for i in split["validation"]}
        train = {stratum_of(cases[i]) for i in split["train"]}
        for stratum, n in sizes.items():
            if n >= 2:
                assert stratum in val, stratum
                assert stratum in train, stratum

    def test_it_improves_on_the_hand_written_split(self, split, cases):
        """11 of 18 strata before, 14 after. If this ever regresses the change was pointless."""
        val = {stratum_of(cases[i]) for i in split["validation"]}
        assert len(val) == 14


class TestValidationSizeBounds:
    def test_rejects_a_size_that_would_empty_a_stratum_from_train(self, cases):
        with pytest.raises(ValueError, match="outside"):
            gepa_split(cases, validation_size=60)

    def test_rejects_a_size_below_the_coverage_floor(self, cases):
        with pytest.raises(ValueError, match="outside"):
            gepa_split(cases, validation_size=5)

    @pytest.mark.parametrize("size", [14, 20, 30, 40, 46])
    def test_sizes_across_the_legal_range_stay_valid_partitions(self, cases, size):
        sp = gepa_split(cases, validation_size=size)
        assert len(sp["validation"]) == size
        assert not set(sp["train"]) & set(sp["validation"])
        assert {stratum_of(cases[i]) for i in sp["train"]} == {stratum_of(c) for c in cases}


class TestTheOtherSplitIsUntouched:
    def test_the_three_way_partition_is_unchanged(self, cases):
        """`scripts/partition_mde.py` pins its output by md5 and that verdict is published.
        Re-pointing the shared apportionment logic at GEPA's split would move it silently."""
        sp = stratified_split(cases)
        assert {k: len(v) for k, v in sp.items()} == {"train": 40, "validation": 12, "test": 12}

    def test_the_two_splits_are_independent_objects(self, cases):
        assert set(gepa_split(cases)) == {"train", "validation"}
        assert set(stratified_split(cases)) == {"train", "validation", "test"}


class TestShippedConfigsCarryIt:
    def test_there_are_configs_to_check(self):
        assert CONFIGS, "no sampler_config.json found — the glob is wrong"

    @pytest.mark.parametrize("path", CONFIGS)
    def test_each_config_matches_the_derived_split(self, path, cases, split):
        """Without this the derivation is decorative: the file is what GEPA actually reads."""
        ids = case_ids(cases)
        cfg = json.loads(Path(path).read_text())
        assert cfg["train_eval_case_ids"] == [ids[i] for i in split["train"]]
        assert cfg["validation_eval_case_ids"] == [ids[i] for i in split["validation"]]

    def test_all_configs_still_agree_with_each_other(self, cases):
        seen = {
            (
                tuple(json.loads(Path(p).read_text())["train_eval_case_ids"]),
                tuple(json.loads(Path(p).read_text())["validation_eval_case_ids"]),
            )
            for p in CONFIGS
        }
        assert len(seen) == 1
