import pytest


@pytest.fixture(autouse=True)
def _isolated_backup_root(tmp_path_factory, monkeypatch):
    """Keep `--apply` pre-write backups out of the real ~/.thauthi during tests."""
    monkeypatch.setenv("THAUTHI_BACKUP_DIR", str(tmp_path_factory.mktemp("thauthi_backups")))
