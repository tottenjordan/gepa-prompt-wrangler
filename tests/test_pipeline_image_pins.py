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
import sys
from importlib import metadata
from pathlib import Path

import pytest

from wrangler.tools.preflight import TARGET_PYTHON

# The Python every image targets and every pin is resolved for: GEAP's runtime.
# Imported from preflight rather than redefined -- two copies of this constant
# drifting is exactly the class of bug this file exists to catch.
_RUNNING_ON_TARGET = f"{sys.version_info.major}.{sys.version_info.minor}" == TARGET_PYTHON

DOCKERFILE = Path("Dockerfile.pipeline")

# Every other image we build and deploy. The 2026-09-08 sweep found all three
# MCP images still on `fastmcp>=2.0.0` and opentelemetry 1.40.0 -- the same
# unpinned-floor defect as above, just in the images nobody had checked because
# the guard named only Dockerfile.pipeline. fastmcp 4.x moves to the mcp 2.x
# protocol while ADK's client is pinned under mcp<2, so a rebuild would have
# produced servers the agents cannot talk to.
OTHER_DOCKERFILES = sorted(Path("examples/multi_model_agents/mcp_servers").glob("*/Dockerfile"))

# Installed in the image but absent from uv.lock, so it cannot be checked
# against the lock. Pinned anyway -- an unpinned entry is what caused this.
NOT_IN_LOCK = {"google-cloud-secret-manager", "opentelemetry-exporter-otlp-proto-grpc"}

# Images whose base Python differs from the local venv, accepted with evidence.
#
# This is an escape hatch, not a default. An entry means somebody resolved that
# image's pin set *on its own base* and recorded what happened -- not that the
# mismatch was noticed and waved through. `wrangler preflight` produces that
# evidence. LITERAL_EXCEPTIONS in test_models.py is kept empty for the same
# reason and is the precedent: an exemption list nobody defends is worse than
# no test at all.
#
# Dockerfile.pipeline must never appear here -- see
# test_the_pipeline_image_is_never_exempted.
_MCP_314 = (
    "python:3.14-slim via dependabot 2026-09-08. Evidence: the pin set resolves "
    "on 3.14 (uv, 85 packages, mcp 1.30.0 -- still under ADK's mcp<2 cap); the "
    "server module imports on 3.14.6, initialises OTel and registers its tools; "
    "and the deployed rev 00008 Cloud Build log shows `FROM python:3.14-slim` "
    "with fastmcp 3.4.7 / mcp 1.30.0 / opentelemetry 1.42.1 actually installed. "
    "Live-checked after deploy: 8 agent queries -> 8 CallToolRequests, 0 non-2xx."
)
PYTHON_MISMATCH_ACCEPTED: dict[str, str] = {
    "examples/multi_model_agents/mcp_servers/search/Dockerfile": _MCP_314,
    "examples/multi_model_agents/mcp_servers/booking/Dockerfile": _MCP_314,
    "examples/multi_model_agents/mcp_servers/expense/Dockerfile": _MCP_314,
}


def _instructions(path: Path) -> str:
    """A Dockerfile's directives with `#` comments stripped.

    Scanning raw text matched the *comment* explaining that these used to be
    `fastmcp>=2.0.0`, and reported the floor still present. Same trap
    test_models.py walks the AST to avoid: prose may name a version, code may
    not, and a regex cannot tell them apart.
    """
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def _pins(path: Path) -> dict[str, str]:
    """Package -> version from any Dockerfile's pip install block."""
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r'"?([a-z0-9\-]+)(?:\[[^\]]*\])?==([^"\s\\]+)"?', _instructions(path))
    }


def _base_image(path: Path) -> str:
    """The `FROM` tag, e.g. 'python:3.14-slim'."""
    for line in _instructions(path).splitlines():
        if line.strip().upper().startswith("FROM "):
            return line.split()[1]
    raise AssertionError(f"{path} has no FROM line")


def test_there_are_other_dockerfiles_to_check():
    """Guards the glob: if it silently matches nothing, the tests below vacuously pass."""
    assert len(OTHER_DOCKERFILES) == 3, OTHER_DOCKERFILES


def test_every_image_declares_a_python_base():
    for path in [DOCKERFILE, *OTHER_DOCKERFILES]:
        assert _base_image(path).startswith("python:"), path


def test_images_are_built_on_the_deployment_python():
    """Every image must target TARGET_PYTHON, or be exempted with evidence.

    The comparison is against the *project's* Python, not `sys.version_info`.
    A first version of this test used the running interpreter and passed on
    3.11 while failing the 3.12 and 3.13 CI matrix entries, where
    Dockerfile.pipeline's correct 3.11 base looked like a mismatch. Two
    different invariants were being conflated: what the images should target
    (fixed) and whether this environment can verify their pins (variable).

    Why it matters that images agree on a Python: uv.lock holds two litellm
    entries, 1.85.7 for python<3.14 and 1.96.2 for >=3.14, and
    google-cloud-aiplatform[evaluation] flips its litellm range at exactly
    that boundary. An image whose base moved is an image whose correct pins
    may have moved.
    """
    mismatched = {
        path.as_posix(): _base_image(path)
        for path in [DOCKERFILE, *OTHER_DOCKERFILES]
        if not _base_image(path).startswith(f"python:{TARGET_PYTHON}")
        and path.as_posix() not in PYTHON_MISMATCH_ACCEPTED
    }
    assert not mismatched, (
        f"{mismatched} do not build on python:{TARGET_PYTHON}, the version GEAP "
        f"runs and the one every pin here is resolved for. Either align the base "
        f"image, or add the path to PYTHON_MISMATCH_ACCEPTED with a reason and "
        f"evidence that the pin set resolves on its own base."
    )


def test_the_pin_comparisons_know_when_they_are_not_evidence():
    """Guards the skip below against silently disabling everything.

    `test_each_pin_matches_the_lockfile` reads `metadata.version()` from the
    running venv, so it is only evidence when that venv is TARGET_PYTHON. On
    the other matrix entries it skips rather than asserting something false --
    but if TARGET_PYTHON ever stopped being a version CI actually runs, the
    pin checks would skip everywhere and nobody would notice.
    """
    versions = re.findall(r'"3\.\d+"', Path(".github/workflows/ci.yml").read_text())
    assert f'"{TARGET_PYTHON}"' in versions, (
        f"CI no longer runs {TARGET_PYTHON}, so every pin comparison would skip. "
        f"CI matrix: {versions}"
    )


def test_the_pipeline_image_is_never_exempted():
    """Dockerfile.pipeline runs the five ADK monkey-patches.

    CLAUDE.md requires the per-patch probe to be re-run whenever ADK moves
    under those patches; moving the *interpreter* under them is no different,
    and it must not be waivable by adding a dict entry.
    """
    assert DOCKERFILE.as_posix() not in PYTHON_MISMATCH_ACCEPTED


@pytest.mark.parametrize("path", OTHER_DOCKERFILES, ids=lambda p: p.parent.name)
def test_other_images_pin_every_dependency(path: Path):
    floors = re.findall(r'"?([a-z0-9\-]+)(?:\[[^\]]*\])?>=([^"\s\\]+)"?', _instructions(path))
    assert not floors, (
        f"{[f[0] for f in floors]} use >= in {path}. Cloud Run rebuilds resolve "
        f"these fresh, so the deployed MCP server drifts from the fastmcp the "
        f"pipeline container and the ADK client were tested against."
    )


@pytest.mark.parametrize(
    ("path", "package"),
    [(p, pkg) for p in OTHER_DOCKERFILES for pkg in sorted(_pins(p))],
    ids=lambda v: v.parent.name if isinstance(v, Path) else v,
)
@pytest.mark.skipif(
    not _RUNNING_ON_TARGET,
    reason=f"pins are resolved for python {TARGET_PYTHON}; this venv is not it",
)
def test_other_image_pins_match_the_lockfile(path: Path, package: str):
    """The MCP servers must speak the protocol version our client was tested on."""
    if package in NOT_IN_LOCK:
        pytest.skip(f"{package} is not in uv.lock; pinned but unverifiable here")
    try:
        installed = metadata.version(package)
    except metadata.PackageNotFoundError:
        pytest.skip(f"{package} not installed in this environment")
    assert _pins(path)[package] == installed, (
        f"{path} pins {package}=={_pins(path)[package]} but uv.lock resolves {installed}."
    )


def test_kfp_runtime_installs_are_pinned():
    """`packages_to_install` is a pip install at pipeline runtime.

    dag.py rebuilds the five heavy components onto the pre-built image with
    `packages_to_install=[]`, but `archive_agent_code` is not in that list --
    it runs on stock python:3.11 and resolves its own dependencies fresh on
    every run. A floor there is the same defect as a floor in a Dockerfile.
    """
    source = Path("wrangler/pipeline/components.py").read_text()
    floors = re.findall(r"packages_to_install=\[([^\]]*)\]", source)
    bad = [spec for group in floors for spec in group.split(",") if ">=" in spec]
    assert not bad, f"unpinned KFP runtime installs: {bad}"


def _dockerfile_pins() -> dict[str, str]:
    """Package -> version from the Dockerfile's pip install block."""
    return _pins(DOCKERFILE)


def test_every_dependency_is_pinned_not_floored():
    """A floor lets a rebuild resolve fresh. That is the whole defect."""
    floors = re.findall(r'"?([a-z0-9\-]+)(?:\[[^\]]*\])?>=([^"\s\\]+)"?', _instructions(DOCKERFILE))
    assert not floors, (
        f"{[f[0] for f in floors]} use >= in Dockerfile.pipeline. A lockfile bump "
        f"moves the image tag, forces a rebuild, and pip resolves these fresh — "
        f"which silently upgraded google-adk mid-campaign on 2026-09-08."
    )


def test_the_dockerfile_installs_something():
    """Guards the regex above: a parse failure must not read as 'no floors'."""
    assert len(_dockerfile_pins()) >= 10, _dockerfile_pins()


@pytest.mark.parametrize("package", sorted(_dockerfile_pins()))
@pytest.mark.skipif(
    not _RUNNING_ON_TARGET,
    reason=f"pins are resolved for python {TARGET_PYTHON}; this venv is not it, so "
    "metadata.version() here is not evidence about the container",
)
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


def _source_requirements() -> list[str]:
    from wrangler.core.deploy import _SOURCE_REQUIREMENTS

    return list(_SOURCE_REQUIREMENTS)


def test_no_agent_requirement_floors_above_the_validated_version():
    """A floor higher than what we run is a version nothing has ever tested.

    These are floors on purpose -- GEAP resolves them itself, so the deployed
    agent may be newer than local. But the floor itself must be a version that
    exists in this environment, or it asserts confidence in something the suite
    never exercised.

    Campaign 07 died on exactly this. `litellm>=1.96.2` was read off uv.lock
    without noticing the entry sits behind a python>=3.14 marker; GEAP runs
    3.11 and validates against 1.85.7. litellm 1.96.2 needs jinja2>=3.1.6,
    google-adk[eval] resolves jinja2 3.1.5, and the GEAP build died with
    ResolutionImpossible -- surfaced to the caller only as "Build failed ... or
    other dependencies", three attempts, whole campaign lost.
    """
    from packaging.version import Version

    bad = []
    for spec in _source_requirements():
        m = re.match(r"([a-z0-9\-]+)(?:\[[^\]]*\])?>=([0-9][^,\s]*)", spec)
        if not m:
            continue
        name, floor = m.group(1), m.group(2)
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
        if Version(floor) > Version(installed):
            bad.append(f"{name}>={floor} but this environment validates {installed}")
    assert not bad, "floors above the validated version: " + "; ".join(bad)


def test_litellm_stays_capped_below_the_jinja2_conflict():
    """Guards the specific fix, not just the general rule above.

    The general test passes at any floor <= installed, including an uncapped
    `litellm>=1.85.7` -- which GEAP would happily resolve to 1.96.2 and break
    again. The cap is the thing that actually holds.
    """
    spec = next((s for s in _source_requirements() if s.startswith("litellm")), None)
    assert spec is not None, "litellm vanished from _SOURCE_REQUIREMENTS"
    assert "<1.86" in spec, (
        f"litellm must stay capped below 1.86 in _SOURCE_REQUIREMENTS; got {spec!r}. "
        "Above that it needs jinja2>=3.1.6 and google-adk[eval] resolves 3.1.5."
    )
