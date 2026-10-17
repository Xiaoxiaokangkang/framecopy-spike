from pathlib import Path

import pytest

from scripts.prepare_speech_model import validate_snapshot


def test_validate_snapshot_accepts_safetensors(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text('{"n_mels": 128}', encoding="utf-8")
    weights = tmp_path / "weights.safetensors"
    weights.write_bytes(b"weights")

    assert validate_snapshot(tmp_path) == weights


def test_validate_snapshot_rejects_missing_weights(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text('{"n_mels": 128}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="权重"):
        validate_snapshot(tmp_path)


def test_validate_snapshot_rejects_invalid_config(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text("not-json", encoding="utf-8")
    (tmp_path / "weights.npz").write_bytes(b"weights")

    with pytest.raises(RuntimeError, match="配置"):
        validate_snapshot(tmp_path)
