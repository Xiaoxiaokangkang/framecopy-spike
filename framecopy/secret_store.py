from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from framecopy.config import Settings


WX_YUANBAO_COOKIE = "wx-yuanbao-cookie"
_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class LocalSecretStore:
    """Small encrypted local store for operator-provided credentials.

    The encryption key and ciphertext are both limited to the current OS user.
    This protects against accidental disclosure and source-control inclusion; it
    is not intended to defend against another process running as the same user.
    """

    def __init__(self, settings: Settings):
        self.root = settings.data_dir / "secrets"
        self.key_path = self.root / ".key"

    def _path(self, name: str) -> Path:
        if not _VALID_NAME.fullmatch(name):
            raise ValueError("invalid secret name")
        return self.root / f"{name}.enc"

    def _fernet(self, *, create: bool) -> Fernet | None:
        if not self.key_path.is_file():
            if not create:
                return None
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(Fernet.generate_key())
        try:
            key = self.key_path.read_bytes().strip()
            os.chmod(self.key_path, 0o600)
            return Fernet(key)
        except (OSError, ValueError):
            return None

    def save(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("secret cannot be empty")
        cipher = self._fernet(create=True)
        if cipher is None:
            raise RuntimeError("cannot initialize local secret store")
        target = self._path(name)
        self.root.mkdir(parents=True, exist_ok=True)
        encrypted = cipher.encrypt(value.encode("utf-8"))
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}-", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, name: str) -> str | None:
        target = self._path(name)
        cipher = self._fernet(create=False)
        if cipher is None or not target.is_file():
            return None
        try:
            return cipher.decrypt(target.read_bytes()).decode("utf-8")
        except (OSError, InvalidToken, UnicodeDecodeError):
            return None

    def has(self, name: str) -> bool:
        return self.load(name) is not None

    def delete(self, name: str) -> bool:
        target = self._path(name)
        if not target.exists():
            return False
        target.unlink()
        return True
