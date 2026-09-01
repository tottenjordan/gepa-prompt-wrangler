"""KFP v2 pipeline DAG for GEPA prompt optimization.

Defines the pipeline that chains per-pair components using ParallelFor
with parallelism control for rate limiting.

The ``build_pipeline(image_uri)`` factory creates components with the
pre-built base image baked in so no runtime pip install is needed.
``gepa_pipeline`` is a convenience reference using ``python:3.11`` for
tests and compilation checks only.
"""

from kfp import dsl

from ..core.models import DEFAULT_JUDGE_MODEL
from .components import (
    archive_agent_code,
    deploy_single_agent,
    eval_single_agent,
    generate_analysis,
    optimize_single_agent,
    redeploy_single_agent,
)


def _make_heavy_components(image_uri: str):
    """Re-create heavy components with a custom base_image.

    KFP's ``@dsl.component(base_image=...)`` is a compile-time constant.
    To swap the image, we rebuild the component specs from the existing
    function bodies using ``dsl.component()`` as a function call.
    """

    # Passed explicitly rather than splatted from a dict: a heterogeneous
    # **kwargs dict collapses every parameter to the union of its value types.
    def _rebuild(func):
        return dsl.component(base_image=image_uri, packages_to_install=[])(func)

    return {
        "deploy": _rebuild(deploy_single_agent.python_func),
        "eval": _rebuild(eval_single_agent.python_func),
        "optimize": _rebuild(optimize_single_agent.python_func),
        "redeploy": _rebuild(redeploy_single_agent.python_func),
        "analysis": _rebuild(generate_analysis.python_func),
    }


def build_pipeline(image_uri: str):
    """Build a pipeline function with heavy components using the given image."""

    comps = _make_heavy_components(image_uri)

    @dsl.pipeline(name="gepa-prompt-optimization")
    def _pipeline(
        project_id: str,
        location: str,
        bucket_name: str,
        manifest_json: str,
        pairs_json: list,
        run_id: str,
        agent_module: str,
        eval_data_path: str,
        num_runs: int = 1,
        judge_model: str = DEFAULT_JUDGE_MODEL,
        secret_id: str = "",
        max_metric_calls: int = 50,
        cache_bust: str = "",
        skip_optimize: bool = False,
        health_gate_json: str = "",
    ):
        archive_task = archive_agent_code(
            project_id=project_id,
            bucket_name=bucket_name,
            run_id=run_id,
            manifest_json=manifest_json,
        )
        archive_task.set_caching_options(enable_caching=True)
        archive_task.set_display_name("Archive agent Code")

        with dsl.ParallelFor(pairs_json, parallelism=1) as pair_config:
            deploy_task = comps["deploy"](
                project_id=project_id,
                location=location,
                bucket_name=bucket_name,
                run_id=run_id,
                pair_json=pair_config,
                agent_module=agent_module,
                secret_id=secret_id,
                cache_bust=cache_bust,
                health_gate_json=health_gate_json,
            )
            deploy_task.set_caching_options(enable_caching=True)
            deploy_task.after(archive_task)
            deploy_task.set_display_name("Deploy Agents")

        with dsl.ParallelFor(pairs_json, parallelism=1) as pair_config:
            eval_before_task = comps["eval"](
                project_id=project_id,
                location=location,
                bucket_name=bucket_name,
                run_id=run_id,
                pair_json=pair_config,
                eval_data_path=eval_data_path,
                phase="before",
                num_runs=num_runs,
                judge_model=judge_model,
                redeploy_output="",
                cache_bust=cache_bust,
            )
            eval_before_task.set_cpu_limit("4")
            eval_before_task.set_memory_limit("16G")
            eval_before_task.set_caching_options(enable_caching=True)
            eval_before_task.after(deploy_task)
            eval_before_task.set_display_name("Evaluate Agent (Before)")

        # Optimize → redeploy → eval_after in ONE ParallelFor block so
        # optimize's output flows as a data dependency to redeploy and
        # eval_after. This ensures KFP caching works correctly — if
        # optimize produces a new prompt, redeploy and eval_after re-run.
        #
        # The whole block is conditional. Without `skip_optimize` there is no
        # way to express a control arm — the same prompt evaluated twice with
        # nothing between — and no way to use this pipeline for
        # characterisation, since every run drags in ~10h of GEPA per pair.
        with dsl.If(skip_optimize == False, name="optimize-enabled"):  # noqa: E712
            with dsl.ParallelFor(pairs_json, parallelism=1) as pair_config:
                optimize_task = comps["optimize"](
                    project_id=project_id,
                    location=location,
                    bucket_name=bucket_name,
                    run_id=run_id,
                    pair_json=pair_config,
                    eval_data_path=eval_data_path,
                    agent_module=agent_module,
                    judge_model=judge_model,
                    secret_id=secret_id,
                    max_metric_calls=max_metric_calls,
                    cache_bust=cache_bust,
                )
                optimize_task.set_cpu_limit("8")
                optimize_task.set_memory_limit("32G")
                optimize_task.set_caching_options(enable_caching=True)
                optimize_task.after(eval_before_task)
                optimize_task.set_display_name("Optimize Agent")

                redeploy_task = comps["redeploy"](
                    project_id=project_id,
                    location=location,
                    bucket_name=bucket_name,
                    run_id=run_id,
                    pair_json=pair_config,
                    agent_module=agent_module,
                    secret_id=secret_id,
                    optimize_output=optimize_task.outputs["Output"],
                    cache_bust=cache_bust,
                )
                redeploy_task.set_caching_options(enable_caching=True)
                redeploy_task.set_display_name("Re-deploy Optimized Agent")

                eval_after_task = comps["eval"](
                    project_id=project_id,
                    location=location,
                    bucket_name=bucket_name,
                    run_id=run_id,
                    pair_json=pair_config,
                    eval_data_path=eval_data_path,
                    phase="after",
                    num_runs=num_runs,
                    judge_model=judge_model,
                    redeploy_output=redeploy_task.outputs["Output"],
                    cache_bust=cache_bust,
                )
                eval_after_task.set_cpu_limit("4")
                eval_after_task.set_memory_limit("16G")
                eval_after_task.set_caching_options(enable_caching=True)
                eval_after_task.set_display_name("Evaluate Agent (After)")

            # Analysis lives inside each branch rather than after them: a task
            # outside a dsl.If cannot depend on one inside it.
            optimized_analysis = comps["analysis"](
                project_id=project_id,
                location=location,
                bucket_name=bucket_name,
                run_id=run_id,
                manifest_json=manifest_json,
                cache_bust=cache_bust,
            )
            optimized_analysis.set_caching_options(enable_caching=True)
            optimized_analysis.after(eval_after_task)
            optimized_analysis.set_display_name("Generate Analysis")

        with dsl.Else():
            # A control arm still needs BOTH evaluations -- the floor is the
            # delta between two evals of an UNCHANGED prompt, so one eval
            # measures nothing. What is skipped is optimize and redeploy, not
            # the second eval. `redeploy_output=""` keeps it pointed at the
            # engine eval_before used.
            with dsl.ParallelFor(pairs_json, parallelism=1) as pair_config:
                control_after_task = comps["eval"](
                    project_id=project_id,
                    location=location,
                    bucket_name=bucket_name,
                    run_id=run_id,
                    pair_json=pair_config,
                    eval_data_path=eval_data_path,
                    phase="after",
                    num_runs=num_runs,
                    judge_model=judge_model,
                    redeploy_output="",
                    cache_bust=cache_bust,
                )
                control_after_task.set_cpu_limit("4")
                control_after_task.set_memory_limit("16G")
                # Caching OFF, deliberately. The inputs are byte-identical to
                # eval_before, so a cache hit would return that exact result and
                # the "floor" would come out as precisely zero -- a measurement
                # of the cache, not of the noise.
                control_after_task.set_caching_options(enable_caching=False)
                control_after_task.after(eval_before_task)
                control_after_task.set_display_name("Evaluate Agent (control, no optimization)")

            control_analysis = comps["analysis"](
                project_id=project_id,
                location=location,
                bucket_name=bucket_name,
                run_id=run_id,
                manifest_json=manifest_json,
                cache_bust=cache_bust,
            )
            control_analysis.set_caching_options(enable_caching=True)
            control_analysis.after(control_after_task)
            control_analysis.set_display_name("Generate Analysis (eval only)")

    return _pipeline


# Convenience reference for tests and local compilation checks.
gepa_pipeline = build_pipeline("python:3.11")
