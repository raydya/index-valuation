import pytest
from pydantic import ValidationError

from index_valuation.config import Settings


def test_database_url_preserves_special_characters_without_exposing_password(monkeypatch):
    password = "local-test@:/%?#password"
    monkeypatch.setenv("POSTGRES_PASSWORD", password)
    settings = Settings(_env_file=None)

    assert settings.database_url.password == password
    assert settings.database_url.drivername == "postgresql+psycopg"
    assert password not in str(settings.database_url)
    assert password not in repr(settings)


def test_environment_overrides_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("POSTGRES_PORT=5433\nPOSTGRES_PASSWORD=local-test\n")
    monkeypatch.setenv("POSTGRES_PORT", "55432")

    assert Settings(_env_file=dotenv).postgres_port == 55432


@pytest.mark.parametrize("password", [None, ""])
def test_password_is_required(monkeypatch, password):
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    if password is not None:
        monkeypatch.setenv("POSTGRES_PASSWORD", password)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
