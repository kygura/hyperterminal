from __future__ import annotations

import os
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = (BACKEND_ROOT / "data.db").resolve()


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _first_env(*names: str) -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return ""


def resolve_primary_db_path() -> Path:
    override = _first_env("DATABASE_PATH", "SIGNAL_DB_PATH", "BRANCHES_DB_PATH", "NEWS_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def resolve_signal_db_path(config_path: str | None = None) -> Path:
    override = _first_env("SIGNAL_DB_PATH", "DATABASE_PATH")
    if override:
        return Path(override)
    if config_path:
        path = Path(config_path)
        return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()
    return DEFAULT_DB_PATH


def resolve_path_from_env(env_name: str) -> Path:
    override = _env(env_name)
    if override:
        return Path(override)
    return resolve_primary_db_path()


def resolve_sqlalchemy_sqlite_url() -> str:
    db_path = resolve_primary_db_path()
    return f"sqlite:///{db_path.resolve().as_posix()}"
