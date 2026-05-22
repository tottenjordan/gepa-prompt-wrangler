"""Tests for wrangler.optimizer — wrapper module creation and patch helpers."""

import os
import pytest
from pathlib import Path

from wrangler.optimizer import _create_wrapper_module


class TestCreateWrapperModule:
    def test_creates_init_file(self, tmp_path):
        wrapper_dir = _create_wrapper_module("/some/agent/path", str(tmp_path))
        init_file = Path(wrapper_dir) / "__init__.py"
        assert init_file.exists()

    def test_wrapper_dir_exists(self, tmp_path):
        wrapper_dir = _create_wrapper_module("/some/agent/path", str(tmp_path))
        assert Path(wrapper_dir).is_dir()

    def test_init_contains_import_logic(self, tmp_path):
        wrapper_dir = _create_wrapper_module("/some/agent/path", str(tmp_path))
        content = (Path(wrapper_dir) / "__init__.py").read_text()
        assert "root_agent" in content
        assert "importlib" in content

    def test_patch_adk_is_callable(self):
        from wrangler.optimizer import _patch_adk
        assert callable(_patch_adk)
