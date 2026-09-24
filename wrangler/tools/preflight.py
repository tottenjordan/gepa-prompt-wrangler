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

from ..reporting.inference import VarianceSource, campaign_09_variance, mde_for_design

# GEAP runs 3.11, and Dockerfile.pipeline is pinned to python:3.11-slim. Both
# sets are therefore checked at 3.11. This is not cosmetic: uv.lock carries two
# litellm entries either side of a python>=3.14 marker, and reading the wrong
# one is what set the floor that killed the campaign.
TARGET_PYTHON = "3.11"

DOCKERFILE = Path("Dockerfile.pipeline")

Resolver = Callable[[list[str], str], tuple[int, str]]


#: The third check's name, exported so a caller can find it among the results
#: without matching on prose.
MDE_CHECK_NAME = "design sensitivity"


@dataclass(frozen=True)
class PreflightResult:
    name: str
    ok: bool
    detail: str
    #: An advisory result reports numbers rather than a verdict, and its ``ok``
    #: is always True. `render` prints its detail whether it passed or not --
    #: the existing two checks print detail only on failure, which for a
    #: warning would mean printing nothing, ever.
    advisory: bool = False


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


def manifest_design(manifest_path: str | Path) -> tuple[int, int]:
    """``(eval cases, num_runs)`` -- the design a manifest describes.

    Read through `PairFactory.load` rather than by re-parsing the YAML, so a
    manifest this repo can run is a manifest this check can read, and the two
    can never disagree about what `num_runs` a campaign will use.

    ``num_runs`` defaults to 1, matching `deploy_pipeline.submit`'s own default
    for a manifest with no ``pipeline.num_runs``. Defaulting to campaign 09's 2
    would quietly describe a better design than the one about to run.
    """
    from ..core.converter import load_eval_file
    from ..core.factory import PairFactory

    manifest = PairFactory.load(manifest_path)
    # Same two-base search as `orchestration.stages._resolve_eval_path`: a
    # manifest's eval_data is written relative to the repo root, but a manifest
    # kept beside its own eval set must also work.
    eval_data = Path(manifest.eval_data)
    for base in (Path(), Path(manifest_path).parent):
        if (base / eval_data).exists():
            eval_data = base / eval_data
            break
    return len(load_eval_file(eval_data)), int(manifest.pipeline.get("num_runs") or 1)


def design_sensitivity(
    manifest_path: str | Path, *, variance_source: VarianceSource | None = None
) -> PreflightResult:
    """What effect this manifest's design could detect. **Warn-only, always.**

    ``ok`` is True whatever the arithmetic says. Every campaign this repo has
    run would trip this check -- campaign 09's `safety_v1` MDE (0.1031 over its
    64 cases at `num_runs: 2`) exceeds the +0.0952 effect it went looking for,
    which is why it spent forty hours to be filed UNRESOLVED. A check that
    blocked on day one would be switched off rather than heeded, so the numbers
    go in ``detail`` and the exit code is left alone.

    It also never fails on its own account: if the design cannot be read, the
    reason is reported and preflight continues. The dependency resolution this
    rides on is what actually stops a campaign dying in a GEAP build, and a
    broken thermometer must not take it down.
    """
    try:
        n_cases, num_runs = manifest_design(manifest_path)
        design = mde_for_design(
            n_cases=n_cases,
            num_runs=num_runs,
            variance_source=variance_source or campaign_09_variance(),
        )
        detail = "\n".join(design.render())
    except (OSError, ValueError, KeyError) as exc:
        detail = (
            f"not measured: {exc}\n"
            "Reported rather than raised: this warning must not stop the dependency "
            "resolution a campaign actually depends on."
        )
    return PreflightResult(name=MDE_CHECK_NAME, ok=True, detail=detail, advisory=True)


def run_preflight(
    python_version: str = TARGET_PYTHON,
    *,
    resolver: Resolver | None = None,
    manifest: str | Path | None = None,
) -> list[PreflightResult]:
    """Check both dependency sets, plus the manifest's design if one is given.

    **No manifest, no MDE.** The design check needs a case count and a
    `num_runs`; without a manifest there is neither, and a warning computed
    over a guessed design would be a confident number with nothing behind it.
    """
    results = [
        check_requirements(
            agent_requirements(), python_version, name="agent requirements", resolver=resolver
        ),
        check_requirements(
            image_requirements(), python_version, name="pipeline image pins", resolver=resolver
        ),
    ]
    if manifest:
        results.append(design_sensitivity(manifest))
    return results


def render(results: list[PreflightResult]) -> list[str]:
    """Human-readable lines for the CLI and the campaign gate."""
    lines = [f"Preflight — resolving both dependency sets on Python {TARGET_PYTHON}", ""]
    for r in results:
        if r.advisory:
            # Not PASS/FAIL: this one reports numbers, and its detail is the
            # whole point, so it prints whether or not anything is wrong.
            lines.append(f"  NOTE  {r.name} (advisory — never blocks)")
            lines.extend(f"        {line}".rstrip() for line in r.detail.splitlines())
            lines.append("")
            continue
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
