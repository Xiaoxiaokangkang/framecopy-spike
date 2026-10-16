from pathlib import Path

from framecopy.config import Settings
from framecopy.secret_store import LocalSecretStore, WX_YUANBAO_COOKIE


def test_local_secret_store_round_trip_and_delete(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, database_path=tmp_path / "test.db")
    store = LocalSecretStore(settings)
    value = "session=private-value; token=another-private-value"

    store.save(WX_YUANBAO_COOKIE, value)

    target = tmp_path / "secrets" / f"{WX_YUANBAO_COOKIE}.enc"
    assert target.is_file()
    assert value.encode("utf-8") not in target.read_bytes()
    assert store.load(WX_YUANBAO_COOKIE) == value
    assert store.has(WX_YUANBAO_COOKIE)
    assert store.delete(WX_YUANBAO_COOKIE)
    assert store.load(WX_YUANBAO_COOKIE) is None
