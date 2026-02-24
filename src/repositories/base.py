"""Подключение к MySQL и конфиг БД."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, unquote

import mysql.connector

from ..config import load_settings


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


_db_config: Optional[DatabaseConfig] = None


def _parse_database_url(url: str) -> DatabaseConfig:
    parsed = urlparse(url)
    if parsed.scheme not in ("mysql", "mysql+mysqlconnector"):
        raise ValueError(f"Unsupported database URL scheme: {parsed.scheme}")
    port = parsed.port or 3306
    path = (parsed.path or "/").strip("/")
    database = path or "excalendar"
    return DatabaseConfig(
        host=parsed.hostname or "localhost",
        port=port,
        user=unquote(parsed.username or "root"),
        password=unquote(parsed.password or ""),
        database=database,
    )


def get_db_config() -> DatabaseConfig:
    global _db_config
    if _db_config is not None:
        return _db_config
    settings = load_settings()
    url = settings.database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Configure it in config.env (e.g. mysql://user:password@host:3306/excalendar)."
        )
    _db_config = _parse_database_url(url)
    return _db_config


def get_connection():
    """Новое соединение с MySQL. Вызывающий обязан вызвать conn.close()."""
    cfg = get_db_config()
    return mysql.connector.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        autocommit=False,
    )
