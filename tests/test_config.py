from pathlib import Path

from framecopy.config import Settings


def test_provider_command_can_be_loaded_from_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FRAMECOPY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FRAMECOPY_WX_PROVIDER_COMMAND", "python scripts/provider.py")
    loaded = Settings.from_env()
    assert loaded.wx_provider_command == "python scripts/provider.py"


def test_ocr_provider_defaults_and_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FRAMECOPY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FRAMECOPY_OCR_PROVIDER", "paddleocr")
    monkeypatch.setenv("FRAMECOPY_OCR_FALLBACK_ENABLED", "false")
    loaded = Settings.from_env()
    assert loaded.ocr_provider == "paddleocr"
    assert loaded.ocr_fallback_enabled is False
