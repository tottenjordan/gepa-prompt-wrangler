"""The one Vertex client factory.

`google-cloud-aiplatform` 2.1.0 deprecates `vertexai.Client`:

    FutureWarning: The vertexai.Client class is deprecated.
    Please use agentplatform.Client instead.

It fired on our own code, not inside a library, so it was ours to fix. Seven
call sites across `core/deploy.py`, `eval/evaluator.py`, `eval/online_monitors.py`
and `scripts/diagnose_tooluse.py` each constructed their own client; they all
come through here now, so the next deprecation is one edit rather than seven.

`agentplatform` is **not a separate PyPI package** -- it ships vendored inside
`google-cloud-aiplatform` 2.1.0. `importlib.metadata.version("agentplatform")`
raises `PackageNotFoundError` while `import agentplatform` succeeds, so do not
use the former to decide whether it is available.

What is deliberately *not* migrated: the generated build package still emits
`from vertexai.agent_engines import AdkApp` (see `deploy.py`'s app template).
That code runs on the GEAP builder, emits no deprecation warning, and is where
a mistake costs a whole campaign. See docs/notes/vertex-sdk-surfaces.md.
"""

from __future__ import annotations

import os
from typing import Any

from .config import GCP_PROJECT_ID, GCP_REGION


def agent_client(project: str | None = None, location: str | None = None) -> Any:
    """Return an `agentplatform.Client` for the Agent Engine / eval APIs.

    `project` and `location` are parameters rather than module reads because
    callers already hold their own module-level `GCP_PROJECT_ID` / `GCP_REGION`,
    and the test suite patches those *per module* (`wrangler.core.deploy.
    GCP_PROJECT_ID`, and so on). A factory that read the environment directly
    would quietly ignore those patches and reach the real project.

    When neither is passed, the environment is read **at call time** rather
    than trusting the import-time constants: the KFP components set
    `GCP_PROJECT_ID` and `GCP_REGION` inside the component body, after the
    tarball is extracted, so an import-time bind can capture a stale project.
    Same rule `core/models.py` follows.
    """
    import agentplatform

    return agentplatform.Client(
        project=project or os.environ.get("GCP_PROJECT_ID") or GCP_PROJECT_ID,
        location=location or os.environ.get("GCP_REGION") or GCP_REGION,
    )
