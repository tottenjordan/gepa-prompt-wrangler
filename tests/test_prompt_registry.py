"""Tests for wrangler.prompt_registry — saving and loading optimized prompts."""

import importlib
import sys
import pytest
from pathlib import Path

from wrangler.prompt_registry import save_optimized_prompt, list_versions


def _cleanup_prompt_modules():
    """Remove dynamically imported prompt modules from sys.modules."""
    to_remove = [k for k in sys.modules if k.startswith("prompts")]
    for k in to_remove:
        del sys.modules[k]


class TestSaveOptimizedPrompt:
    def test_saves_new_version_to_file(self, prompt_module_dir):
        prompts_dir = prompt_module_dir("test")
        try:
            version = save_optimized_prompt(
                "test", "New optimized prompt text",
                version_name="v1",
                prompts_dir=prompts_dir,
            )
            assert version == "v1"
            content = Path(prompts_dir, "test_prompts.py").read_text()
            assert "New optimized prompt text" in content
        finally:
            _cleanup_prompt_modules()

    def test_auto_generates_version_name(self, prompt_module_dir):
        prompts_dir = prompt_module_dir("test")
        try:
            version = save_optimized_prompt(
                "test", "Prompt text",
                prompts_dir=prompts_dir,
            )
            assert version.startswith("wrangler_v_")
        finally:
            _cleanup_prompt_modules()

    def test_custom_version_name_used(self, prompt_module_dir):
        prompts_dir = prompt_module_dir("test")
        try:
            version = save_optimized_prompt(
                "test", "Prompt text",
                version_name="my_custom_v1",
                prompts_dir=prompts_dir,
            )
            assert version == "my_custom_v1"
        finally:
            _cleanup_prompt_modules()

    def test_raw_text_file_created(self, prompt_module_dir, tmp_path):
        prompts_dir = prompt_module_dir("test")
        try:
            save_optimized_prompt(
                "test", "Prompt for raw file",
                version_name="v1",
                prompts_dir=prompts_dir,
            )
            outputs_dir = Path(prompts_dir).parent.parent / "outputs" / "prompts"
            raw_files = list(outputs_dir.glob("test_v1.txt"))
            assert len(raw_files) == 1
            assert raw_files[0].read_text() == "Prompt for raw file"
        finally:
            _cleanup_prompt_modules()

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            save_optimized_prompt(
                "nonexistent", "Prompt",
                prompts_dir="/nonexistent/path/prompts",
            )

    def test_preserves_existing_versions(self, prompt_module_dir):
        prompts_dir = prompt_module_dir("test")
        try:
            save_optimized_prompt(
                "test", "First prompt",
                version_name="v1",
                prompts_dir=prompts_dir,
            )
            _cleanup_prompt_modules()
            save_optimized_prompt(
                "test", "Second prompt",
                version_name="v2",
                prompts_dir=prompts_dir,
            )
            content = Path(prompts_dir, "test_prompts.py").read_text()
            assert "v1" in content
            assert "v2" in content
            assert "First prompt" in content
            assert "Second prompt" in content
        finally:
            _cleanup_prompt_modules()

    def test_triple_quotes_in_prompt_escaped(self, prompt_module_dir):
        prompts_dir = prompt_module_dir("test")
        try:
            save_optimized_prompt(
                "test", 'Prompt with """triple quotes""" inside',
                version_name="v1",
                prompts_dir=prompts_dir,
            )
            content = Path(prompts_dir, "test_prompts.py").read_text()
            assert '"""' not in content.split("OPTIMIZED")[1].split("'''")[0] or "'''" in content
        finally:
            _cleanup_prompt_modules()


class TestListVersions:
    def test_empty_optimized_returns_empty_dict(self, prompt_module_dir):
        prompts_dir = prompt_module_dir("test")
        try:
            result = list_versions("test", prompts_dir=prompts_dir)
            assert result == {}
        finally:
            _cleanup_prompt_modules()

    def test_returns_existing_versions(self, prompt_module_dir):
        prompts_dir = prompt_module_dir("test")
        try:
            save_optimized_prompt(
                "test", "Saved prompt",
                version_name="v1",
                prompts_dir=prompts_dir,
            )
            _cleanup_prompt_modules()
            result = list_versions("test", prompts_dir=prompts_dir)
            assert "v1" in result
            assert result["v1"]["prompt"] is not None
        finally:
            _cleanup_prompt_modules()

    def test_nonexistent_agent_raises(self, prompt_module_dir):
        prompts_dir = prompt_module_dir("test")
        try:
            with pytest.raises((ModuleNotFoundError, FileNotFoundError)):
                list_versions("nonexistent", prompts_dir=prompts_dir)
        finally:
            _cleanup_prompt_modules()
