"""Run a KFP component *body* in-process, so its logic can be tested.

`components.py` is the code that runs a campaign, and it sat at **2% coverage** because
every test of it read `inspect.getsource(...)` and asserted on substrings. Source assertions
do not execute a line, so they buy no coverage -- and they break on a rename while passing
through a real defect. Four of them were replaced during the 2026-09-21 audit for exactly
that reason.

**A `@dsl.component` is an ordinary function plus a wrapper.** `comp.python_func` is the
undecorated function, so it can be called directly with fakes. KFP's isolation rule does not
apply here: it governs what is available *inside the container at runtime*, not what a test
in this repo may call.

What the harness fakes, and why each one is unavoidable:

| faked | why |
| --- | --- |
| `google.cloud.storage.Client` | every component downloads the code tarball and writes a stage artifact |
| `tarfile.open` | the body extracts to `/app`, which must not happen on a dev machine |
| `sys.path` / `os.environ` | bodies mutate both globally; without restoration one test bleeds into the next |
| `Output[Metrics]` / `Output[Markdown]` | KFP injects these; they are just `.path` plus `.log_metric` |

`FakeGcs` keeps blobs in a dict keyed by their full path, so a test can assert **where** an
artifact was written as well as what it contains. Stage paths are a real interface -- the
eval component reads the deploy component's artifact by path -- so a typo there is a
production bug that no amount of return-value checking would catch.
"""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest import mock

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class FakeBlob:
    """One GCS object, backed by the parent `FakeGcs`'s dict."""

    def __init__(self, store: dict[str, str], path: str) -> None:
        self._store = store
        self.path = path
        self.size: int | None = None

    def exists(self) -> bool:
        return self.path in self._store

    def reload(self) -> None:
        self.size = len(self._store.get(self.path, ""))

    def download_as_text(self) -> str:
        if self.path not in self._store:
            msg = f"404: no such blob {self.path}"
            raise RuntimeError(msg)
        return self._store[self.path]

    def download_to_filename(self, dest: str) -> None:
        """The code tarball. Written as a real file so `tarfile.open` has something to
        open if a test chooses not to fake it."""
        if self.path not in self._store:
            msg = f"404: no such blob {self.path}"
            raise RuntimeError(msg)
        with open(dest, "w") as fh:
            fh.write(self._store[self.path])

    def upload_from_string(self, data: str, content_type: str | None = None) -> None:
        self._store[self.path] = data
        self.content_type = content_type

    def upload_from_filename(self, src: str) -> None:
        """How the optimize component ships GEPA's `run_dir`, file by file."""
        with open(src) as fh:
            self._store[self.path] = fh.read()


class FakeBucket:
    def __init__(self, store: dict[str, str], name: str) -> None:
        self._store = store
        self.name = name

    def blob(self, path: str) -> FakeBlob:
        return FakeBlob(self._store, path)


@dataclass
class FakeGcs:
    """A whole GCS client, one dict of `path -> contents`.

    Not keyed by bucket: every component in a run uses one bucket, and flattening keeps
    assertions readable (`gcs.blobs["pipeline-runs/r/stages/deploy/x.json"]`).
    """

    blobs: dict[str, str] = field(default_factory=dict)
    project: str | None = None

    def bucket(self, name: str) -> FakeBucket:
        return FakeBucket(self.blobs, name)

    def seed(self, path: str, contents: str) -> None:
        self.blobs[path] = contents

    def written(self, suffix: str) -> str:
        """The single blob whose path ends with `suffix`, or a clear failure.

        Asserting on a suffix rather than the full path keeps a test from restating the
        `pipeline-runs/{run_id}/` prefix, while still failing if the stage directory is
        wrong -- which is the part that actually breaks between components.
        """
        hits = [p for p in self.blobs if p.endswith(suffix)]
        if len(hits) != 1:
            msg = f"expected exactly one blob ending {suffix!r}, found {sorted(hits)}"
            raise AssertionError(msg)
        return self.blobs[hits[0]]


class FakeMetrics:
    """KFP's `Output[Metrics]`: a `.path` plus `.log_metric`."""

    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.logged: dict[str, Any] = {}

    def log_metric(self, key: str, value: Any) -> None:
        self.logged[key] = value


class FakeMarkdown:
    """KFP's `Output[Markdown]`: the body opens `.path` and writes to it."""

    def __init__(self, path: Path) -> None:
        self.path = str(path)

    def read(self) -> str:
        try:
            with open(self.path) as fh:
                return fh.read()
        except FileNotFoundError:
            return ""


@dataclass
class ComponentIO:
    """Everything a component body needs handed to it, plus the fakes to assert on."""

    gcs: FakeGcs
    metrics: FakeMetrics
    summary: FakeMarkdown
    agent_prompt: FakeMarkdown
    tar: mock.MagicMock

    @property
    def outputs(self) -> dict[str, Any]:
        """Splat into the call: `comp.python_func(..., **io.outputs)`."""
        return {
            "metrics": self.metrics,
            "summary": self.summary,
            "agent_prompt": self.agent_prompt,
        }

    def extract_kwargs(self) -> dict[str, Any]:
        """The kwargs the body passed to `tar.extractall`.

        Used to pin `filter="data"`, which is what stops a malicious tarball writing
        outside `/app` via an absolute path or a `..` escape.
        """
        ctx = self.tar.return_value.__enter__.return_value
        if not ctx.extractall.call_args_list:
            return {}
        return ctx.extractall.call_args.kwargs


@contextlib.contextmanager
def component_io(tmp_path: Path, *, seed: dict[str, str] | None = None) -> Iterator[ComponentIO]:
    """Run a component body against fakes, leaving no global state behind.

    `sys.path` and `os.environ` are snapshotted and restored: every body does
    `sys.path.insert(0, "/app")` and sets six env vars, so without this the first test to
    run would silently configure the rest of the suite.
    """
    gcs = FakeGcs()
    # Every body downloads this first; seeding it keeps each test from restating it.
    gcs.seed("pipeline-runs/test-run/code.tar.gz", "not-a-real-tarball")
    for path, contents in (seed or {}).items():
        gcs.seed(path, contents)

    saved_path = list(sys.path)
    saved_env = dict(os.environ)

    with (
        mock.patch("google.cloud.storage.Client", return_value=gcs),
        mock.patch("tarfile.open") as tar,
    ):
        try:
            yield ComponentIO(
                gcs=gcs,
                metrics=FakeMetrics(tmp_path / "metrics.json"),
                summary=FakeMarkdown(tmp_path / "summary.md"),
                agent_prompt=FakeMarkdown(tmp_path / "prompt.md"),
                tar=tar,
            )
        finally:
            sys.path[:] = saved_path
            os.environ.clear()
            os.environ.update(saved_env)
