from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, List

import psycopg2
from psycopg2.extras import register_default_jsonb

from .config import load_settings


logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DatabaseConfig:
    url: str


_db_config: Optional[DatabaseConfig] = None


def get_db_config() -> DatabaseConfig:
    """
    Read DATABASE_URL from settings/env.

    Phase 1: we only configure connection and run migrations.
    The rest of the code still uses JSON files.
    """
    global _db_config
    if _db_config is not None:
        return _db_config

    settings = load_settings()
    url = settings.database_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Configure it in Railway variables and/or config.env before enabling DB-backed storage."
        )
    _db_config = DatabaseConfig(url=url)
    return _db_config


def get_connection():
    """
    Return a new psycopg2 connection.

    Caller is responsible for closing it.
    """
    cfg = get_db_config()
    # Ensure JSONB is mapped to Python dict
    register_default_jsonb(loads=None, globally=True)
    conn = psycopg2.connect(cfg.url)
    conn.autocommit = False
    return conn


def run_migrations() -> None:
    """
    Create base tables if they don't exist.

    This is Phase 1: schema only, no code is wired to use it yet.
    """
    ddl_statements = [
        # Trainers: all trainers, including main admins.
        """
        CREATE TABLE IF NOT EXISTS trainers (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            username TEXT,
            is_main_admin BOOLEAN NOT NULL DEFAULT FALSE,
            invite_code TEXT UNIQUE
        );
        """,
        # Clients: unique by telegram_id.
        """
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            username TEXT
        );
        """,
        # Bookings: basic structure for future migration.
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id UUID PRIMARY KEY,
            trainer_id INTEGER NOT NULL REFERENCES trainers(id) ON DELETE CASCADE,
            client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            time TIME NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            reminder_sent BOOLEAN NOT NULL DEFAULT FALSE
        );
        """,
        # Schedule settings per trainer.
        """
        CREATE TABLE IF NOT EXISTS schedule_settings (
            trainer_id INTEGER PRIMARY KEY REFERENCES trainers(id) ON DELETE CASCADE,
            workdays TEXT,            -- e.g. '0,1,2,3,4'
            work_start_hour INTEGER,
            work_end_hour INTEGER,
            manual_slots JSONB        -- {"0": ["09:00", ...], ...}
        );
        """,
        # Blocked slots per trainer.
        """
        CREATE TABLE IF NOT EXISTS blocked_slots (
            id SERIAL PRIMARY KEY,
            trainer_id INTEGER NOT NULL REFERENCES trainers(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            time TIME                  -- NULL => whole day
        );
        """,
    ]

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for ddl in ddl_statements:
                    cur.execute(ddl)
        logger.info("Database migrations applied successfully.")
    finally:
        conn.close()


def ensure_initial_trainers() -> None:
    """
    Ensure that trainers table has rows for all INSTRUCTOR_CHAT_ID from config.

    Пока только создаём (если нет) и помечаем первого как is_main_admin = TRUE.
    """
    settings = load_settings()
    instructor_ids: List[int] = settings.instructor_chat_ids
    if not instructor_ids:
        return

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # Ensure each instructor exists as trainer
                for idx, telegram_id in enumerate(instructor_ids):
                    is_main_admin = idx == 0
                    cur.execute(
                        """
                        INSERT INTO trainers (telegram_id, full_name, username, is_main_admin)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (telegram_id) DO UPDATE
                        SET is_main_admin = trainers.is_main_admin OR EXCLUDED.is_main_admin
                        """,
                        (telegram_id, f"Trainer {telegram_id}", None, is_main_admin),
                    )
        logger.info("Initial trainers synced from INSTRUCTOR_CHAT_ID.")
    finally:
        conn.close()



