from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from framecopy.config import Settings


def _fernet(settings: Settings) -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def save_link_source(settings: Settings, job_dir: Path, url: str) -> None:
    target = job_dir / "input" / "source.enc"
    target.write_bytes(_fernet(settings).encrypt(url.encode("utf-8")))
    os.chmod(target, 0o600)


def save_bilibili_source(settings: Settings, job_dir: Path, payload: dict[str, str]) -> None:
    target = job_dir / "input" / "source.enc"
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    target.write_bytes(_fernet(settings).encrypt(raw))
    os.chmod(target, 0o600)


def load_job_source(settings: Settings, job_dir: Path, source_type: str) -> Path | str | dict[str, str]:
    if source_type == "upload":
        path = (job_dir / "input" / "source.bin").resolve()
        if not path.is_file():
            raise FileNotFoundError("uploaded source is missing")
        return path
    target = job_dir / "input" / "source.enc"
    try:
        raw = _fernet(settings).decrypt(target.read_bytes()).decode("utf-8")
        if source_type == "bilibili_link":
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
                raise ValueError("invalid bilibili source payload")
            return {str(key): str(value) for key, value in payload.items()}
        return raw
    except (FileNotFoundError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("encrypted link source is unavailable") from exc


def delete_link_source(job_dir: Path) -> None:
    target = job_dir / "input" / "source.enc"
    if target.exists():
        target.unlink()
