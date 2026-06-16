"""KFP v2 pipeline DAG for GEPA prompt optimization.

Defines the pipeline that chains per-pair components using ParallelFor
with parallelism control for rate limiting.

The ``build_pipeline(image_uri)`` factory creates components with the
pre-built base image baked in so no runtime pip install is needed.
``gepa_pipeline`` is a convenience reference using ``python:3.11`` for
tests and compilation checks only.
"""

from kfp import dsl

from .components import (
    archive_agent_code,
    deploy_single_agent,
    eval_single_agent,
    optimize_single_agent,
    redeploy_single_agent,
    generate_analysis,
)


def _make_heavy_components(image_uri: str):
    """Re-create heavy components with a custom base_image.

    KFP's ``@dsl.component(base_image=...)`` is a compile-time constant.
    To swap the image, we rebuild the component specs from the existing
    function bodies using ``dsl.component()`` as a function call.
    """
    common = dict(
        base_image=image_uri,
        packages_to_install=[],
    )

    return {
        "deploy": dsl.component(**common)(deploy_single_agent.python_func),
        "eval": dsl.component(**common)(eval_single_agent.python_func),
        "optimize": dsl.component(**common)(optimize_single_agent.python_func),
        "redeploy": dsl.component(**common)(redeploy_single_agent.python_func),
        "analysis": dsl.component(**common)(generate_analysis.python_func),
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
        judge_model: str = "gemini-2.5-pro",
        eval_thresholds_json: str = "{}",
        secret_id: str = "",
        max_metric_calls: int = 50,
        cache_bust: str = "",
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
            )
            deploy_task.set_caching_options(enable_caching=True)
            deploy_task.after(archive_task)
            deploy_task.set_display_name(f"Deploy Agents")

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
                cache_bust="",
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
                eval_thresholds_json=eval_thresholds_json,
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

        analysis_task = comps["analysis"](
            project_id=project_id,
            location=location,
            bucket_name=bucket_name,
            run_id=run_id,
            manifest_json=manifest_json,
            cache_bust=cache_bust,
        )
        analysis_task.set_caching_options(enable_caching=True)
        analysis_task.after(eval_after_task)
        analysis_task.set_display_name(f"Generate Analysis")

    return _pipeline


# Convenience reference for tests and local compilation checks.
gepa_pipeline = build_pipeline("python:3.11")
