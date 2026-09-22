"""What goes into the code tarball, and what must not.

`package_and_upload_code` builds the archive every pipeline stage unpacks at `/app`. It had
**no test at all**, and CLAUDE.md records why that matters: *"Missing directories have caused
multiple pipeline failures."* The failure mode is expensive and late -- the tarball uploads
fine, the pipeline submits fine, and a stage dies minutes in because the agent's package is
not there.

The rules are cheap to state and easy to get wrong in both directions:

- **too little** -- a directory the agents import is absent, and the stage fails on import
- **too much** -- `.venv` or `outputs` goes in, and a ~200 KB archive becomes hundreds of MB

Two kinds of test here, because they catch different mistakes:

1. **Synthetic tree** (fast): the exclusion rules themselves, driven through a tmp directory
   so a test can assert on a `__pycache__` it created.
2. **The real repo** (one test, ~5 s): that the directories the agents actually need are
   still included. A synthetic tree cannot catch someone adding `agents/` to the exclude
   list, because the synthetic tree's `agents/` is not the one that matters.
"""

from __future__ import annotations

import tarfile
from typing import TYPE_CHECKING
from unittest import mock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from wrangler.pipeline.deploy_pipeline import package_and_upload_code


def _members(project_root: Path | None = None) -> set[str]:
    """Package, and return the top-level names that made it into the archive.

    The tarball is read inside the `upload_from_filename` mock because the function deletes
    it immediately afterwards -- by the time it returns there is nothing left to inspect.
    """
    captured: set[str] = set()

    def capture(path: str) -> None:
        with tarfile.open(path, "r:gz") as tar:
            captured.update(name.split("/")[0] for name in tar.getnames())

    client = mock.MagicMock()
    client.bucket.return_value.blob.return_value.upload_from_filename.side_effect = capture
    with mock.patch("google.cloud.storage.Client", return_value=client):
        package_and_upload_code("bucket", "run-1", "project", project_root=project_root)
    return captured


@pytest.fixture
def fake_repo(tmp_path):
    """A project tree holding one of everything the rules have an opinion about."""
    for name in (
        "wrangler",
        "agents",
        "examples",
        "manifests",
        "data",
        "scripts",
        ".venv",
        ".git",
        ".claude",
        "__pycache__",
        ".pytest_cache",
        "outputs",
        "experiments",
        "node_modules",
        ".mypy_cache",
        "_geap_build_pkg",
    ):
        (tmp_path / name).mkdir()
        (tmp_path / name / "file.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / ".env").write_text("SECRET=shh\n")
    return tmp_path


class TestTheTarballCarriesWhatTheStagesNeed:
    def test_source_directories_are_included(self, fake_repo):
        assert {"wrangler", "agents", "examples", "manifests", "data", "scripts"} <= _members(
            fake_repo
        )

    def test_top_level_files_are_included(self, fake_repo):
        """Every stage runs `sys.path.insert(0, "/app")`; `pyproject.toml` travels with it."""
        assert "pyproject.toml" in _members(fake_repo)


class TestTheTarballExcludesWhatWouldBloatOrLeak:
    @pytest.mark.parametrize(
        "name",
        [
            ".venv",
            ".git",
            ".claude",
            "__pycache__",
            ".pytest_cache",
            "outputs",
            "experiments",
            "node_modules",
            ".mypy_cache",
            "_geap_build_pkg",
        ],
    )
    def test_the_excluded_directory_is_absent(self, fake_repo, name):
        """`.venv` alone is hundreds of MB, and `_geap_build_pkg` is a transient build
        artifact that would ship a stale agent package into every stage."""
        assert name not in _members(fake_repo)

    def test_dotfiles_are_excluded_even_when_not_named(self, fake_repo):
        """The rule is `item in excludes or item.startswith(".")`. `.env` holds real
        credentials and is caught by the second clause, not the list -- so a test that only
        checked the named excludes would not notice the clause disappearing."""
        assert ".env" not in _members(fake_repo)


class TestTheRealRepoStillPackagesTheAgentsCode:
    """One test against the actual tree, because a synthetic one cannot catch a real
    directory being added to the exclude list.

    **One test, not several.** Packaging the real repo costs ~4.5 s, so the assertions share
    a single run rather than paying it once each.
    """

    def test_the_real_tree_ships_the_code_and_not_the_venv(self):
        members = _members()

        assert {"wrangler", "examples", "manifests"} <= members, (
            "a directory the pipeline stages import is missing from the code tarball; "
            "this fails minutes into a run, after submission has already succeeded"
        )
        assert ".venv" not in members, "the single most expensive mistake available here"
        assert ".git" not in members
