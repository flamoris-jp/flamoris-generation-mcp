from pathlib import Path

from flamoris_generation_mcp.config import Settings


def test_facade_inherits_optional_music_output_env(monkeypatch):
    monkeypatch.setenv("FLAMORIS_YUE2_OUTPUT_ROOT", "/data/music-output")
    monkeypatch.setenv("FLAMORIS_OUTPUT_DIR", "/data/outputs")
    monkeypatch.setenv("FLAMORIS_WORKFLOW_DIR", "/data/workflows")
    settings = Settings.from_env()
    assert settings.yue2_output_root == Path("/data/music-output")
    assert settings.output_dir == Path("/data/outputs")
    assert settings.workflow_dir == Path("/data/workflows")
    assert settings.yue2_config is None


def test_publication_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FLAMORIS_YUE2_OUTPUT_ROOT", raising=False)
    assert Settings.from_env().yue2_output_root is None
