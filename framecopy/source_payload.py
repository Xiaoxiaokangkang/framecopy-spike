from __future__ import annotations

import base64
import hashlib
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


def load_job_source(settings: Settings, job_dir: Path, source_type: str) -> Path | str:
    if source_type == "upload":
        path = (job_dir / "input" / "source.bin").resolve()
        if not path.is_file():
            raise FileNotFoundError("uploaded source is missing")
        return path
    target = job_dir / "input" / "source.enc"
    try:
        return _fernet(settings).decrypt(target.read_bytes()).decode("utf-8")
    except (FileNotFoundError, InvalidToken, UnicodeDecodeError) as exc:
        raise ValueError("encrypted link source is unavailable") from exc


def delete_link_source(job_dir: Path) -> None:
    target = job_dir / "input" / "source.enc"
    if target.exists():
        target.unlink()
