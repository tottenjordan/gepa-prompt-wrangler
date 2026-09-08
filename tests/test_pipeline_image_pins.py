"""The pipeline image must install exactly what uv.lock resolved.

`Dockerfile.pipeline` installs from its own hardcoded pip list, NOT from
uv.lock -- it copies pyproject.toml but never installs from it. The image tag
is md5(pyproject.toml + uv.lock + Dockerfile.pipeline), so a lockfile change
moves the tag and forces a rebuild, and if the Dockerfile's entries are `>=`
floors that rebuild resolves fresh against PyPI.

On 2026-09-08 a dependabot bump to uv.lock did exactly that: the optimize
container came up on google-adk 2.8.0 while local, CI and deploy.py were all on
2.7.1. Two things broke quietly. All five ADK monkey-patches in
optimize/optimizer.py were running against an unverified version -- CLAUDE.md
requires a per-patch probe before moving ADK, and a dependency moving
underneath the patches violates that as surely as editing them. And
_ToolsetFailureCounter matches a log string 2.8.0 no longer emits, so it
reported zero tool failures while five had occurred, in a live campaign whose
whole output is deltas.

Nothing caught it. The image hash cannot: it hashes a lockfile the image does
not install from. So the check has to be that the two agree.
"""

from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

import pytest

DOCKERFILE = Path("Dockerfile.pipeline")

# Installed in the image but absent from uv.lock, so it cannot be checked
# against the lock. Pinned anyway -- an unpinned entry is what caused this.
NOT_IN_LOCK = {"google-cloud-secret-manager"}


def _dockerfile_pins() -> dict[str, str]:
    """Package -> version from the Dockerfile's pip install block."""
    pins = {}
    for match in re.finditer(r'"([a-z0-9\-]+)(?:\[[^\]]*\])?==([^"]+)"', DOCKERFILE.read_text()):
        pins[match.group(1)] = match.group(2)
    return pins


def test_every_dependency_is_pinned_not_floored():
    """A floor lets a rebuild resolve fresh. That is the whole defect."""
    floors = re.findall(r'"([a-z0-9\-]+)(?:\[[^\]]*\])?>=([^"]+)"', DOCKERFILE.read_text())
    assert not floors, (
        f"{[f[0] for f in floors]} use >= in Dockerfile.pipeline. A lockfile bump "
        f"moves the image tag, forces a rebuild, and pip resolves these fresh — "
        f"which silently upgraded google-adk mid-campaign on 2026-09-08."
    )


def test_the_dockerfile_installs_something():
    """Guards the regex above: a parse failure must not read as 'no floors'."""
    assert len(_dockerfile_pins()) >= 10, _dockerfile_pins()


@pytest.mark.parametrize("package", sorted(_dockerfile_pins()))
def test_each_pin_matches_the_lockfile(package: str):
    """Container and local must run the same code, or local testing proves nothing."""
    if package in NOT_IN_LOCK:
        pytest.skip(f"{package} is not in uv.lock; pinned but unverifiable here")
    pinned = _dockerfile_pins()[package]
    try:
        installed = metadata.version(package)
    except metadata.PackageNotFoundError:
        pytest.skip(f"{package} not installed in this environment")
    assert pinned == installed, (
        f"Dockerfile.pipeline pins {package}=={pinned} but uv.lock resolves "
        f"{installed}. The pipeline container would run different code than "
        f"local and CI test against."
    )


def test_adk_is_pinned_to_the_version_the_patches_were_verified_against():
    """optimize/optimizer.py monkey-patches five ADK internals.

    CLAUDE.md: "Do NOT remove or add patches without re-running the per-patch
    probe in docs/notes/adk-patch-status.md against the installed ADK." Moving
    the dependency under the patches breaks that rule too. Bumping this pin is
    allowed -- with the probe re-run, and this test's expectation updated in
    the same commit, so the two cannot silently diverge.
    """
    assert _dockerfile_pins()["google-adk"] == "2.8.0", (
        "google-adk moved. Re-run the per-patch probe in "
        "docs/notes/adk-patch-status.md before changing this."
    )


def test_the_agent_container_and_the_pipeline_container_agree_on_adk():
    """deploy.py pins ADK for the agent; the Dockerfile pins it for the optimizer.

    They ran different versions on 2026-09-08 and nothing noticed.
    """
    source = Path("wrangler/core/deploy.py").read_text()
    agent_pin = re.search(r'"google-adk\[[^\]]*\]==([^"]+)"', source)
    assert agent_pin, "deploy.py no longer pins google-adk exactly"
    assert agent_pin.group(1) == _dockerfile_pins()["google-adk"], (
        f"agent container pins google-adk=={agent_pin.group(1)} but the pipeline "
        f"image pins {_dockerfile_pins()['google-adk']}"
    )
