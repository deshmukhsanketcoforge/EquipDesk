"""Run Alembic migrations from code (tests, scripts)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]


def _config(database_url: str | None = None) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    if database_url is not None:
        # configparser treats % as interpolation; escape it.
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_to_head(database_url: str) -> None:
    command.upgrade(_config(database_url), "head")


def head_revision() -> str:
    head = ScriptDirectory.from_config(_config()).get_current_head()
    assert head is not None, "no migrations found"
    return head
