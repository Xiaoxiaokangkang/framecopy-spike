from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_local_env() -> None:
    """Load the project's simple .env file without adding a runtime dependency."""
    env_path = Path.cwd() / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key.startswith("FRAMECOPY_"):
            os.environ.setdefault(key, value)


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    max_upload_bytes: int = 200 * 1024 * 1024
    max_duration_seconds: int = 30 * 60
    max_resolution_pixels: int = 3840 * 2160
    retention_hours: int = 7 * 24
    input_retention_hours: int = 24
    queue_backend: str = "local"
    redis_url: str = "redis://127.0.0.1:6379/0"
    wx_provider_command: str = ""
    secret_key: str = "change-me-for-public-deployment"
    ocr_provider: str = "auto"
    ocr_fallback_enabled: bool = True
    ocr_timeout_seconds: int = 45
    apple_vision_languages: tuple[str, ...] = ("zh-Hans", "zh-Hant", "en-US")
    speech_provider: str = "mlx_whisper"
    speech_model: str = "mlx-community/whisper-large-v3-turbo"
    speech_python: str = ""
    speech_timeout_seconds: int = 45 * 60

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = _path(os.getenv("FRAMECOPY_DATA_DIR", "./data"))
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "framecopy.db",
            max_upload_bytes=int(os.getenv("FRAMECOPY_MAX_UPLOAD_BYTES", str(200 * 1024 * 1024))),
            max_duration_seconds=int(os.getenv("FRAMECOPY_MAX_DURATION_SECONDS", "1800")),
            retention_hours=int(os.getenv("FRAMECOPY_RETENTION_HOURS", "168")),
            input_retention_hours=int(os.getenv("FRAMECOPY_INPUT_RETENTION_HOURS", "24")),
            queue_backend=os.getenv("FRAMECOPY_QUEUE_BACKEND", "local"),
            redis_url=os.getenv("FRAMECOPY_REDIS_URL", "redis://127.0.0.1:6379/0"),
            wx_provider_command=os.getenv("FRAMECOPY_WX_PROVIDER_COMMAND", "").strip(),
            secret_key=os.getenv("FRAMECOPY_SECRET_KEY", "change-me-for-public-deployment"),
            ocr_provider=os.getenv("FRAMECOPY_OCR_PROVIDER", "auto").strip().lower(),
            ocr_fallback_enabled=os.getenv("FRAMECOPY_OCR_FALLBACK_ENABLED", "true").strip().lower()
            not in {"0", "false", "no", "off"},
            ocr_timeout_seconds=max(5, int(os.getenv("FRAMECOPY_OCR_TIMEOUT_SECONDS", "45"))),
            apple_vision_languages=tuple(
                item.strip() for item in os.getenv(
                    "FRAMECOPY_APPLE_VISION_LANGUAGES", "zh-Hans,zh-Hant,en-US"
                ).split(",") if item.strip()
            ),
            speech_provider=os.getenv("FRAMECOPY_SPEECH_PROVIDER", "mlx_whisper").strip().lower(),
            speech_model=os.getenv(
                "FRAMECOPY_SPEECH_MODEL", "mlx-community/whisper-large-v3-turbo"
            ).strip(),
            speech_python=os.getenv("FRAMECOPY_SPEECH_PYTHON", "").strip(),
            speech_timeout_seconds=max(
                60, int(os.getenv("FRAMECOPY_SPEECH_TIMEOUT_SECONDS", str(45 * 60)))
            ),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "jobs").mkdir(parents=True, exist_ok=True)


_load_local_env()
settings = Settings.from_env()
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(settings.data_dir / "models" / "paddlex"))
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

