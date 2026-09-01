"""Tests for keeping .env engine ids in step with what is deployed."""

from pathlib import Path

from wrangler.core import env_ids


def _env(tmp_path, body):
    p = tmp_path / ".env"
    p.write_text(body)
    return p


class TestUpdateEngineId:
    def test_an_existing_id_is_replaced_in_place(self, tmp_path):
        p = _env(tmp_path, "A=1\nSONNET_ENGINE_ID=111\nB=2\n")
        env_ids.set_engine_id("sonnet", "222", path=str(p))
        assert p.read_text() == "A=1\nSONNET_ENGINE_ID=222\nB=2\n"

    def test_surrounding_lines_and_order_are_untouched(self, tmp_path):
        """A .env rewrite that reorders or drops keys is worse than the drift."""
        body = "# comment\nGCP_PROJECT_ID=x\nLITE_ENGINE_ID=1\n\nOPUS_ENGINE_ID=2\n"
        p = _env(tmp_path, body)
        env_ids.set_engine_id("lite", "9", path=str(p))
        assert p.read_text() == body.replace("LITE_ENGINE_ID=1", "LITE_ENGINE_ID=9")

    def test_a_missing_key_is_appended(self, tmp_path):
        p = _env(tmp_path, "A=1\n")
        env_ids.set_engine_id("pro", "7", path=str(p))
        assert "PRO_ENGINE_ID=7" in p.read_text()
        assert p.read_text().startswith("A=1\n")

    def test_a_commented_out_key_is_not_treated_as_the_value(self, tmp_path):
        p = _env(tmp_path, "# SONNET_ENGINE_ID=old\nSONNET_ENGINE_ID=111\n")
        env_ids.set_engine_id("sonnet", "222", path=str(p))
        text = p.read_text()
        assert "# SONNET_ENGINE_ID=old" in text
        assert "SONNET_ENGINE_ID=222" in text
        assert "111" not in text

    def test_an_unchanged_id_is_a_no_op(self, tmp_path):
        p = _env(tmp_path, "SONNET_ENGINE_ID=111\n")
        assert env_ids.set_engine_id("sonnet", "111", path=str(p)) is False

    def test_a_changed_id_reports_true(self, tmp_path):
        p = _env(tmp_path, "SONNET_ENGINE_ID=111\n")
        assert env_ids.set_engine_id("sonnet", "222", path=str(p)) is True

    def test_a_missing_file_is_created(self, tmp_path):
        p = tmp_path / "nope" / ".env"
        env_ids.set_engine_id("flash", "5", path=str(p))
        assert "FLASH_ENGINE_ID=5" in Path(p).read_text()


class TestReadEngineIds:
    def test_reads_the_known_labels(self, tmp_path):
        p = _env(tmp_path, "LITE_ENGINE_ID=1\nOPUS_ENGINE_ID=2\nOTHER=3\n")
        assert env_ids.read_engine_ids(path=str(p)) == {"lite": "1", "opus": "2"}

    def test_an_empty_value_is_absent_not_blank(self, tmp_path):
        p = _env(tmp_path, "LITE_ENGINE_ID=\nOPUS_ENGINE_ID=2\n")
        assert env_ids.read_engine_ids(path=str(p)) == {"opus": "2"}
