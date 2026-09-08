"""Resolve the dependency sets before a campaign spends twenty minutes on them.

Two sets decide whether a campaign can run at all, and neither is checked by
anything else:

- **agent requirements** (`_SOURCE_REQUIREMENTS` in `core/deploy.py`) are
  written into `_geap_build_pkg/requirements.txt` and resolved by *pip* on the
  GEAP builder.
- **pipeline image pins** (`Dockerfile.pipeline`) are resolved by pip inside
  Cloud Build.

Campaign 07's validation arm died twice in a GEAP build, the second time on::

    Cannot install -r ./_geap_build_pkg/requirements.txt (line 6) and
    google-adk[a2a,agent-identity,eval,mcp]==2.8.0 because these package
    versions have conflicting dependencies.
      google-adk[...] 2.8.0 depends on jinja2<4 and >=3.1.4; extra == "eval"
      litellm 1.96.2 depends on jinja2<4.0 and >=3.1.6

Every check in ``tests/test_pipeline_image_pins.py`` is static -- it compares
declared strings to installed versions -- so a transitive conflict two levels
down is invisible to all of them. The single test that catches this pair is a
tombstone for that incident; the next conflict will be a different pair and
will land exactly the same way.

The cost of the failure was ~20 minutes of build, three retries, a dead arm,
and an error that said only ``Build failed ... or other dependencies``. The
cost of this check is about a second.

**This is not a unit test on purpose.** Resolving needs the network, and the
suite is "green, fast, and hermetic (no network, no GCP)" per
``docs/notes/toolchain-baseline.md`` -- the repo's strongest asset. Tests here
inject a fake resolver; only the CLI path shells out.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# GEAP runs 3.11, and Dockerfile.pipeline is pinned to python:3.11-slim. Both
# sets are therefore checked at 3.11. This is not cosmetic: uv.lock carries two
# litellm entries either side of a python>=3.14 marker, and reading the wrong
# one is what set the floor that killed the campaign.
TARGET_PYTHON = "3.11"

DOCKERFILE = Path("Dockerfile.pipeline")

Resolver = Callable[[list[str], str], tuple[int, str]]


@dataclass(frozen=True)
class PreflightResult:
    name: str
    ok: bool
    detail: str


def _instructions(path: Path) -> str:
    """A Dockerfile's directives with ``#`` comments stripped.

    Scanning raw text matches the *comments* that explain past version
    problems -- this repo has been bitten by that twice. Prose may name a
    version; only a directive counts.
    """
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def agent_requirements() -> list[str]:
    """What GEAP installs for a deployed agent.

    Imported, never copied. A second copy would drift from the real one, and
    the drift would only show up as a failed deploy.
    """
    from ..core.deploy import _SOURCE_REQUIREMENTS

    return list(_SOURCE_REQUIREMENTS)


def image_requirements(path: Path | None = None) -> list[str]:
    """The exact pins installed into the pipeline image."""
    text = _instructions(path or DOCKERFILE)
    return [
        f"{m.group(1)}{m.group(2) or ''}=={m.group(3)}"
        for m in re.finditer(r'"([a-z0-9\-]+)(\[[^\]]*\])?==([^"\s\\]+)"', text)
    ]


def _uv_resolver(reqs: list[str], python_version: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["uv", "pip", "install", "--dry-run", "--python-version", python_version, *reqs],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def check_requirements(
    reqs: list[str],
    python_version: str = TARGET_PYTHON,
    *,
    name: str = "requirements",
    resolver: Resolver | None = None,
) -> PreflightResult:
    """Resolve one requirement set, reporting the resolver's own words.

    The detail is passed through verbatim. GEAP's ``Build failed ... or other
    dependencies`` is useless precisely because it paraphrases; repeating that
    mistake here would defeat the point.
    """
    code, output = (resolver or _uv_resolver)(reqs, python_version)
    return PreflightResult(name=name, ok=code == 0, detail=output)


def run_preflight(
    python_version: str = TARGET_PYTHON,
    *,
    resolver: Resolver | None = None,
) -> list[PreflightResult]:
    """Check both sets. Returns one result per set, in check order."""
    return [
        check_requirements(
            agent_requirements(), python_version, name="agent requirements", resolver=resolver
        ),
        check_requirements(
            image_requirements(), python_version, name="pipeline image pins", resolver=resolver
        ),
    ]


def render(results: list[PreflightResult]) -> list[str]:
    """Human-readable lines for the CLI and the campaign gate."""
    lines = [f"Preflight — resolving both dependency sets on Python {TARGET_PYTHON}", ""]
    for r in results:
        lines.append(
            f"  {'PASS' if r.ok else 'FAIL'}  {r.name} ({len(r.detail.splitlines())} lines)"
        )
        if not r.ok:
            lines.extend(f"        {line}" for line in r.detail.splitlines())
    if all(r.ok for r in results):
        lines += [
            "",
            "  Note: uv resolves this, GEAP uses pip. A pass reduces the risk of a",
            "  build-time ResolutionImpossible; it does not eliminate it.",
        ]
    return lines
