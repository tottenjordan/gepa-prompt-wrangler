# Vertex Pipeline state codes, and the SDK's sharp edges

Lookup tables. The interpretation that matters is in `SKILL.md`; this is what you consult
when a task prints `state=8` and you need to know whether that is bad.

## Pipeline state

| Code | State | Description |
| --- | --- | --- |
| 1 | QUEUED | Waiting to start |
| 2 | PENDING | Being prepared |
| 3 | RUNNING | Actively executing |
| 4 | SUCCEEDED | Completed successfully |
| 5 | FAILED | Terminated with error |
| 6 | CANCELLING | Cancel in progress |
| 7 | CANCELLED | Cancelled |
| 8 | PAUSED | Paused |
| 9 | SKIPPED | Skipped |

## Task execution state

| Code | State | Notes |
| --- | --- | --- |
| 1 | PENDING | |
| 2 | RUNNING_DRIVER | |
| 3 | DRIVER_SUCCEEDED | Also seen for lightweight tasks that skip the executor |
| 4 | RUNNING_EXECUTOR | |
| 5 | SUCCEEDED | |
| 7 | CANCELLED | Usually an upstream dependency failed |
| 8 | CACHED / SKIPPED | **KFP cache hit** — the task reused a previous output |
| 9 | FAILED | |

**State 8 is the one that misleads.** A cached task did not run, so its "result" is whatever
a previous run produced. On a campaign that is frequently correct and occasionally
catastrophic — see `SKILL.md` on cache busting, and CLAUDE.md's "Pipeline Caching".

## Our DAG, so you know what you are looking at

`wrangler/pipeline/dag.py`. One `ParallelFor` over pairs at `parallelism=1`, deliberately —
it is the rate-limit control for the shared judge.

```
Archive agent Code                       (once, cached)
  └─ ParallelFor(pairs, parallelism=1):
       Deploy Agents                     health-gated, may redeploy several times
       Evaluate Agent (Before)
       [if not skip_optimize]
         Optimize Agent                  ~9-10 h; 87% of the run
         Re-deploy Optimized Agent       health-gated too — it redraws the reach rate
         Evaluate Agent (After)
       Generate Analysis
```

Component functions are `archive_agent_code`, `deploy_single_agent`, `eval_single_agent`,
`optimize_single_agent`, `redeploy_single_agent` in `wrangler/pipeline/components.py`.

## SDK edges that cost time

**`PipelineJob` has no `start_time` or `end_time`.** Use `create_time`, and get real
start/end from Cloud Logging. Duration computed from `create_time` includes queueing.

**`display_name` and `resource_name` differ.** Always search by substring:

```python
from google.cloud import aiplatform
aiplatform.init(project=PROJECT, location=REGION)
runs = aiplatform.PipelineJob.list()
job = next(r for r in runs if "SEARCH_TERM" in (r.display_name or ""))

print(job.state, job.create_time)
for k, v in sorted(job.gca_resource.runtime_config.parameter_values.items()):
    print(f"  {k}: {v}")
for t in job.task_details:
    err = (t.error.message[:150] if t.error and t.error.message else "")
    print(f"{t.task_name}: state={t.state} {err}")
```

**Container start times vary by minutes.** `ParallelFor` containers are scheduled as
resources free up, and startup includes image pull and driver init. When computing per-task
duration, use that container's **first log entry**, not the pipeline's `create_time` —
otherwise every late starter looks slow.

**Never hardcode the project id, bucket, or engine id.** Read them from `.env`
(`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_STAGING_BUCKET`). They must not appear in committed
files.
