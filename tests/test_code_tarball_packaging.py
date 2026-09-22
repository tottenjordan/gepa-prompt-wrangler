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


def _members_nested(project_root: Path) -> set[str]:
    """Every member path, not just the top-level name -- for asserting about depth."""
    captured: set[str] = set()

    def capture(path: str) -> None:
        with tarfile.open(path, "r:gz") as tar:
            captured.update(tar.getnames())

    client = mock.MagicMock()
    client.bucket.return_value.blob.return_value.upload_from_filename.side_effect = capture
    with mock.patch("google.cloud.storage.Client", return_value=client):
        package_and_upload_code("bucket", "run-1", "project", project_root=project_root)
    return captured


@pytest.fixture
def fake_repo(tmp_path):
    """A project tree holding one of everything the rules have an opinion about.

    Includes **nested** copies of the excluded names, because the rule used to be applied
    only to `os.listdir(project_root)` while `tar.add()` recursed -- so a `__pycache__` one
    level down shipped.
    """
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
        "docs",
    ):
        (tmp_path / name).mkdir()
        (tmp_path / name / "file.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / ".env").write_text("SECRET=shh\n")

    # The same names, one and two levels down inside a directory that IS shipped.
    nested = tmp_path / "examples" / "multi_model_agents"
    (nested / "__pycache__").mkdir(parents=True)
    (nested / "__pycache__" / "config.cpython-311.pyc").write_text("bytecode")
    (nested / "outputs").mkdir()
    (nested / "outputs" / "stale.json").write_text("{}")
    # Contents are irrelevant -- the assertion is that the file never reaches the tarball.
    # Kept deliberately low-entropy so the detect-secrets hook has nothing to flag.
    (nested / ".env").write_text("GCP_PROJECT_ID=fake\n")
    (nested / ".env.example").write_text("GCP_PROJECT_ID=\n")
    (nested / "config.py").write_text("x = 1\n")
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


class TestTheRulesApplyAtEveryDepthNotJustTheTopLevel:
    """`tar.add()` recurses, so a rule checked only against `os.listdir(project_root)`
    excluded `outputs/` at the root and shipped every `outputs/` nested inside an included
    directory. Measured on this repo before the fix: **475 `__pycache__` members (455
    `.pyc`)**, 18 nested `outputs/` entries, and a real credentials file.
    """

    def test_nested_bytecode_is_not_shipped(self, fake_repo):
        """455 `.pyc` files went into every tarball, compiled by whichever interpreter last
        ran locally -- 3.11 here, while the MCP images are 3.14."""
        members = _members_nested(fake_repo)

        assert not [m for m in members if "__pycache__" in m]
        assert not [m for m in members if m.endswith(".pyc")]

    def test_nested_outputs_are_not_shipped(self, fake_repo):
        members = _members_nested(fake_repo)

        assert "examples/multi_model_agents/outputs" not in members
        assert not [m for m in members if "/outputs/" in m]

    def test_the_directory_around_them_is_still_shipped(self, fake_repo):
        """The exclusion must be surgical -- dropping the parent instead would take
        `config.py` with it, and `build_source_package` copies that into every agent."""
        members = _members_nested(fake_repo)

        assert "examples/multi_model_agents/config.py" in members


class TestNoEnvFileIsShippedAtAnyDepth:
    """The pipeline-tarball half of `test_deploy.py::test_no_env_file_shipped`.

    `build_source_package` stopped copying `.env` into the agent build package -- it
    "uploaded a secrets file to a server that never reads it". **The same file was still
    going up in the code tarball**, because the dotfile rule was only applied to the top
    level: `examples/multi_model_agents/.env` is a real, gitignored, 63-line file holding
    `GCP_PROJECT_ID`, `PROJECT_NUMBER` and `GCP_STAGING_BUCKET`, and it was uploaded to GCS
    and extracted at `/app` in every stage.

    Neither `.gitignore` nor the detect-secrets hook can catch this: both guard what reaches
    *git*, and this file never does. A test is the only thing that can.
    """

    def test_a_nested_env_file_is_excluded(self, fake_repo):
        members = _members_nested(fake_repo)

        assert "examples/multi_model_agents/.env" not in members
        assert not [m for m in members if m.split("/")[-1] == ".env"]

    def test_env_templates_go_too(self, fake_repo):
        """`.env.example` is a template nothing reads at runtime. It is excluded by the same
        dotfile clause, and keeping it would mean special-casing a prefix match on `.env`.
        """
        assert "examples/multi_model_agents/.env.example" not in _members_nested(fake_repo)


class TestDocumentationDoesNotRideAlong:
    def test_docs_are_excluded(self, fake_repo):
        """9.1 MB and 71% of the tarball, of which 6 MB is the README banner PNG --
        re-uploaded and re-extracted for every stage of every arm. No stage imports it;
        every `docs/` reference in shipped code is a comment or a URL."""
        assert "docs" not in _members(fake_repo)


class TestTheRealRepoStillPackagesTheAgentsCode:
    """One test against the actual tree, because a synthetic one cannot catch a real
    directory being added to the exclude list.

    **One test, not several.** Packaging the real repo costs ~4.5 s, so the assertions share
    a single run rather than paying it once each.
    """

    def test_the_real_tree_ships_the_code_and_nothing_it_should_not(self):
        members = _members_nested(None)
        top = {m.split("/")[0] for m in members}

        assert {"wrangler", "examples", "manifests"} <= top, (
            "a directory the pipeline stages import is missing from the code tarball; "
            "this fails minutes into a run, after submission has already succeeded"
        )
        assert "wrangler/pipeline/components.py" in members
        assert "examples/multi_model_agents/config.py" in members

        assert ".venv" not in top, "the single most expensive mistake available here"
        assert ".git" not in top
        assert "docs" not in top

        # The real repo genuinely contains all three of these; before the recursive filter
        # every one of them shipped.
        assert not [m for m in members if m.endswith(".pyc")]
        assert not [m for m in members if m.split("/")[-1] == ".env"], (
            "a real credentials file is being uploaded to GCS and extracted at /app"
        )
        assert not [m for m in members if "/outputs/" in m]
