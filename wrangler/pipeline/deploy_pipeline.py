"""Pipeline deployment: image build, code packaging, compilation, and Vertex AI submission."""

import datetime
import hashlib
import json
import logging
import os
import subprocess
import tarfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# E402 below is deliberate: load_dotenv() must run before importing modules
# that read GCP config at import time.

from ..core.config import disable_pyopenssl  # noqa: E402
from ..core.models import DEFAULT_JUDGE_MODEL  # noqa: E402

disable_pyopenssl()

logger = logging.getLogger(__name__)

IMAGE_REPO = "gepa-wrangler"
IMAGE_NAME = "pipeline"


def _compute_image_tag(
    pyproject_path: Path,
    lock_path: Path,
    dockerfile_path: Path | None = None,
) -> str:
    """Image tag = md5 of the inputs that determine the image contents, first 12 chars.

    All three matter:

    - `pyproject.toml` holds version *ranges*, so a resolved dependency can move to a
      new version without it changing.
    - `uv.lock` pins those resolutions.
    - `Dockerfile.pipeline` carries its own hardcoded `pip install` list — it copies
      `pyproject.toml` but does not install from it. Editing a pin there changes the
      image while leaving the other two files untouched.

    Miss any of them and two builds can share a tag with different packages inside, at
    which point the stale cached image silently wins.
    """
    h = hashlib.md5(usedforsecurity=False)  # a cache key, not a security primitive
    for path in (pyproject_path, lock_path, dockerfile_path):
        # The separator keeps `(a, b)` from hashing the same as `(ab, "")`.
        h.update(path.read_bytes() if path and path.exists() else b"")
        h.update(b"\0")
    return h.hexdigest()[:12]


def _compute_deps_hash() -> str:
    """Hash everything that determines the pipeline image contents."""
    root = Path(__file__).resolve().parent.parent.parent
    return _compute_image_tag(
        root / "pyproject.toml",
        root / "uv.lock",
        root / "Dockerfile.pipeline",
    )


def _image_exists(image_uri: str) -> bool:
    """Check if the image already exists in Artifact Registry."""
    try:
        result = subprocess.run(
            [
                "gcloud",
                "artifacts",
                "docker",
                "images",
                "describe",
                image_uri,
                "--format=value(image_summary.digest)",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def build_pipeline_image(
    project_id: str,
    location: str = "us-central1",
    force: bool = False,
) -> str:
    """Build and push the pipeline base image via Cloud Build.

    Skips the build if an image with the same dependency hash already exists
    in Artifact Registry. Returns the full image URI.
    """
    deps_hash = _compute_deps_hash()
    region = (
        location.split("-", maxsplit=1)[0] + "-" + location.split("-")[1]
        if "-" in location
        else "us"
    )
    ar_host = f"{region}-docker.pkg.dev"
    image_uri = f"{ar_host}/{project_id}/{IMAGE_REPO}/{IMAGE_NAME}:{deps_hash}"

    if not force and _image_exists(image_uri):
        logger.info(f"Image already exists: {image_uri} (deps hash: {deps_hash})")
        return image_uri

    logger.info(f"Building pipeline image: {image_uri}")
    project_root = Path(__file__).resolve().parent.parent.parent

    # Ensure Artifact Registry repo exists
    subprocess.run(
        [
            "gcloud",
            "artifacts",
            "repositories",
            "describe",
            IMAGE_REPO,
            "--location",
            region,
            "--project",
            project_id,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    repo_check = subprocess.run(
        [
            "gcloud",
            "artifacts",
            "repositories",
            "describe",
            IMAGE_REPO,
            "--location",
            region,
            "--project",
            project_id,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if repo_check.returncode != 0:
        logger.info(f"Creating Artifact Registry repo: {IMAGE_REPO}")
        subprocess.run(
            [
                "gcloud",
                "artifacts",
                "repositories",
                "create",
                IMAGE_REPO,
                "--repository-format=docker",
                "--location",
                region,
                "--project",
                project_id,
                "--labels=solution=promp-wrangler",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    cloudbuild_config = {
        "steps": [
            {
                "name": "gcr.io/cloud-builders/docker",
                "args": ["build", "-t", image_uri, "-f", "Dockerfile.pipeline", "."],
            }
        ],
        "images": [image_uri],
        "timeout": "1200s",
    }
    import yaml

    cb_path = project_root / "cloudbuild_pipeline.yaml"
    cb_path.write_text(yaml.dump(cloudbuild_config))

    result = subprocess.run(
        [
            "gcloud",
            "builds",
            "submit",
            "--project",
            project_id,
            "--region",
            location,
            "--config",
            str(cb_path),
            str(project_root),
        ],
        capture_output=True,
        text=True,
        timeout=1500,
        check=False,
    )
    cb_path.unlink(missing_ok=True)
    if result.returncode != 0:
        logger.error(f"Cloud Build failed:\n{result.stderr[-2000:]}")
        raise RuntimeError(f"Cloud Build failed: {result.stderr[-500:]}")

    logger.info(f"Image built: {image_uri}")
    return image_uri


def package_and_upload_code(
    bucket_name: str,
    run_id: str,
    project_id: str,
) -> str:
    """Package the project source and upload to GCS. Returns the GCS URI."""
    from google.cloud import storage

    project_root = Path(__file__).resolve().parent.parent.parent
    tarball_path = "/tmp/wrangler_code.tar.gz"

    excludes = {
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
    }

    with tarfile.open(tarball_path, "w:gz") as tar:
        for item in os.listdir(str(project_root)):
            if item in excludes or item.startswith("."):
                continue
            full_path = project_root / item
            tar.add(str(full_path), arcname=item)

    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    blob_path = f"pipeline-runs/{run_id}/code.tar.gz"
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(tarball_path)

    size_kb = os.path.getsize(tarball_path) / 1024
    os.remove(tarball_path)
    logger.info(f"Uploaded {size_kb:.0f} KB to gs://{bucket_name}/{blob_path}")
    return f"gs://{bucket_name}/{blob_path}"


def deploy_pipeline(
    manifest_path: str,
    run_id: str | None = None,
    num_runs: int = 1,
    quick_test: bool = False,
) -> dict:
    """Build image, compile, and submit the GEPA pipeline to Vertex AI.

    Returns dict with dashboard_uri, job_id, run_name.
    """
    from google.cloud import aiplatform
    from kfp import compiler

    from ..core.factory import PairFactory

    manifest = PairFactory.load(manifest_path)
    pipeline_config = {}

    manifest_path_p = Path(manifest_path)
    if manifest_path_p.exists():
        import yaml

        with open(manifest_path_p) as f:
            raw = yaml.safe_load(f)
        pipeline_config = raw.get("pipeline", {})

    project_id = os.getenv("GCP_PROJECT_ID", "")
    location = pipeline_config.get("region", os.getenv("GCP_REGION", "us-central1"))
    bucket_name = pipeline_config.get(
        "bucket", os.getenv("GCP_STAGING_BUCKET", f"{project_id}-wrangler-staging")
    )
    service_account = pipeline_config.get(
        "service_account", os.getenv("PIPELINE_SERVICE_ACCOUNT", "")
    )
    secret_id = pipeline_config.get("secret_id", "")

    # run_id is deterministic from inputs so KFP caching works across submissions.
    # job_id gets a unique timestamp suffix so Vertex AI doesn't reject duplicates.
    if not run_id:
        import hashlib

        cache_key = hashlib.md5(
            f"{manifest.name}:{manifest.agent_module}:{manifest.eval_data}:"
            f"{','.join(p.id for p in manifest.enabled_pairs)}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:10]
        run_id = f"run-{cache_key}"

    num_runs = pipeline_config.get("num_runs", num_runs)
    judge_model = manifest.eval_config.get("judge_model", DEFAULT_JUDGE_MODEL)
    max_metric_calls = pipeline_config.get("max_metric_calls", 50)
    cache_bust = pipeline_config.get("cache_bust", "")

    manifest_dict = {
        "name": manifest.name,
        "description": manifest.description,
        "agent_module": manifest.agent_module,
        "eval_data": manifest.eval_data,
        "eval_config": manifest.eval_config,
        "pairs": [
            {
                "id": p.id,
                "model": p.model,
                "system_prompt": p.system_prompt,
                "description": p.description,
                "engine_id": p.engine_id,
                "agent_module": p.agent_module,
                "costs": p.costs,
            }
            for p in manifest.enabled_pairs
        ],
    }
    manifest_json = json.dumps(manifest_dict)

    pairs_json = [
        {
            "id": p.id,
            "model": p.model,
            "system_prompt": p.system_prompt,
            "engine_id": p.engine_id,
            "agent_module": p.agent_module,
            "costs": p.costs,
        }
        for p in manifest.enabled_pairs
    ]

    # Step 1: Build pipeline base image (skips if deps unchanged)
    logger.info("Building pipeline image (if needed)...")
    image_uri = build_pipeline_image(project_id, location)

    # Step 2: Package code and upload to GCS (including manifest)
    logger.info("Packaging code and uploading to GCS...")
    package_and_upload_code(bucket_name, run_id, project_id)

    from google.cloud import storage as _storage

    _gcs = _storage.Client(project=project_id)
    _gcs.bucket(bucket_name).blob(f"pipeline-runs/{run_id}/manifest.json").upload_from_string(
        manifest_json, content_type="application/json"
    )

    # Step 3: Compile pipeline with the built image
    from .dag import build_pipeline

    pipeline_func = build_pipeline(image_uri)

    pipeline_yaml = f"/tmp/gepa_pipeline_{run_id}.yaml"
    logger.info(f"Compiling pipeline to {pipeline_yaml}...")
    compiler.Compiler().compile(
        pipeline_func=pipeline_func,
        package_path=pipeline_yaml,
    )

    # Step 4: Submit to Vertex AI
    logger.info(f"Initializing Vertex AI: project={project_id}, location={location}")
    aiplatform.init(project=project_id, location=location)

    pipeline_root = f"gs://{bucket_name}/pipeline-runs/{run_id}/pipeline_root"
    timestamp = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d-%H%M%S")
    job_id = f"gepa-{run_id}-{timestamp}"
    display_name = f"gepa-{manifest.name}-{run_id}"

    logger.info("=" * 60)
    logger.info("DEPLOYMENT SUMMARY")
    logger.info(f"  Experiment:     {manifest.name}")
    logger.info(f"  Run ID:         {run_id}")
    logger.info(f"  Image:          {image_uri}")
    logger.info(f"  Pipeline Root:  {pipeline_root}")
    n_disabled = len(manifest.pairs) - len(manifest.enabled_pairs)
    disabled_note = f" ({n_disabled} disabled)" if n_disabled else ""
    logger.info(f"  Pairs:          {len(manifest.enabled_pairs)}{disabled_note}")
    logger.info(f"  Num Runs:       {num_runs}")
    logger.info(f"  Judge Model:    {judge_model}")
    logger.info(f"  Bucket:         {bucket_name}")
    logger.info(f"  Secret:         {secret_id or '(none)'}")
    logger.info("=" * 60)

    job = aiplatform.PipelineJob(
        display_name=display_name,
        job_id=job_id,
        template_path=pipeline_yaml,
        pipeline_root=pipeline_root,
        parameter_values={
            "project_id": project_id,
            "location": location,
            "bucket_name": bucket_name,
            "manifest_json": manifest_json,
            "pairs_json": pairs_json,
            "run_id": run_id,
            "agent_module": manifest.agent_module,
            "eval_data_path": manifest.eval_data,
            "num_runs": num_runs,
            "judge_model": judge_model,
            "secret_id": secret_id,
            "max_metric_calls": max_metric_calls,
            "cache_bust": cache_bust,
            # A manifest can turn the optimize->redeploy->eval_after chain off
            # entirely, which is what makes a control arm expressible and what
            # lets this pipeline be used for characterisation rather than only
            # for optimization runs.
            "skip_optimize": bool((manifest.pipeline or {}).get("skip_optimize", False)),
        },
        labels={"solution": "promp-wrangler"},
    )

    submit_kwargs = {}
    if service_account:
        submit_kwargs["service_account"] = service_account

    logger.info("Submitting pipeline job...")
    job.submit(**submit_kwargs)

    dashboard_url = (
        f"https://console.cloud.google.com/vertex-ai/locations/{location}"
        f"/pipelines/runs/{job.name}?project={project_id}"
    )
    logger.info(f"Pipeline submitted! Dashboard: {dashboard_url}")

    os.remove(pipeline_yaml)

    return {
        "dashboard_uri": dashboard_url,
        "job_id": job_id,
        "run_id": run_id,
        "experiment": manifest.name,
    }
