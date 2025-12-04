from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, List
import uuid

import psycopg2
from psycopg2 import pool
from psycopg2.extras import register_default_jsonb

from .config import load_settings


logger = logging.getLogger(__name__)

# Connection pool for faster DB access
_connection_pool: Optional[pool.ThreadedConnectionPool] = None


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


def _init_connection_pool() -> None:
    """Initialize connection pool if not already done."""
    global _connection_pool
    if _connection_pool is not None:
        return
    
    cfg = get_db_config()
    # Ensure JSONB is mapped to Python dict
    register_default_jsonb(loads=None, globally=True)
    
    # Create pool: min 2, max 10 connections
    try:
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=cfg.url,
        )
        logger.info("Database connection pool initialized (2-10 connections)")
    except Exception as e:
        logger.error(f"Failed to create connection pool: {e}")
        raise


def get_connection():
    """
    Return a connection from the pool (or create new if pool unavailable).

    Caller MUST return it to pool using conn.close() (which actually returns it to pool).
    """
    global _connection_pool
    
    if _connection_pool is None:
        _init_connection_pool()
    
    try:
        conn = _connection_pool.getconn()
        conn.autocommit = False
        return conn
    except Exception as e:
        logger.error(f"Failed to get connection from pool: {e}")
        # Fallback: create direct connection if pool fails
        cfg = get_db_config()
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

    Автоматически генерирует invite_code для новых тренеров.
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
                    # Check if trainer already exists
                    cur.execute(
                        "SELECT id, invite_code FROM trainers WHERE telegram_id = %s",
                        (telegram_id,),
                    )
                    row = cur.fetchone()
                    
                    if row:
                        # Trainer exists - ensure invite_code if missing
                        trainer_id, existing_code = row
                        if not existing_code:
                            new_code = uuid.uuid4().hex
                            cur.execute(
                                """
                                UPDATE trainers
                                SET invite_code = %s,
                                    is_main_admin = trainers.is_main_admin OR %s
                                WHERE id = %s
                                """,
                                (new_code, is_main_admin, trainer_id),
                            )
                    else:
                        # New trainer - create with invite_code
                        new_code = uuid.uuid4().hex
                        cur.execute(
                            """
                            INSERT INTO trainers (telegram_id, full_name, username, is_main_admin, invite_code)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (telegram_id, f"Trainer {telegram_id}", None, is_main_admin, new_code),
                        )
        logger.info("Initial trainers synced from INSTRUCTOR_CHAT_ID (with invite codes).")
    finally:
        conn.close()


def get_trainer_invite_code(telegram_id: int) -> str | None:
    """
    Get existing invite_code for trainer, if exists.
    
    Returns None if trainer doesn't exist or has no invite_code.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT invite_code
                FROM trainers
                WHERE telegram_id = %s
                """,
                (telegram_id,),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None
    finally:
        conn.close()


def ensure_trainer_with_invite(
    telegram_id: int,
    username: str | None,
    full_name: str | None,
) -> str:
    """
    Ensure trainer row exists for given telegram_id and has invite_code.

    Returns invite_code (existing or newly generated).
    """
    # First try to get existing code
    existing_code = get_trainer_invite_code(telegram_id)
    if existing_code:
        return existing_code
    
    # No code yet - generate and save
    settings = load_settings()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # Check if trainer exists
                cur.execute(
                    """
                    SELECT id
                    FROM trainers
                    WHERE telegram_id = %s
                    """,
                    (telegram_id,),
                )
                row = cur.fetchone()
                
                if row:
                    # Trainer exists but no code - generate and update
                    trainer_id = row[0]
                    invite_code = uuid.uuid4().hex
                    cur.execute(
                        """
                        UPDATE trainers
                        SET invite_code = %s,
                            username = COALESCE(%s, username),
                            full_name = COALESCE(%s, full_name)
                        WHERE id = %s
                        """,
                        (invite_code, username, full_name or f"Trainer {telegram_id}", trainer_id),
                    )
                    return invite_code

                # No trainer yet – create one
                # Mark as main admin if in INSTRUCTOR_CHAT_ID list and is first element
                instructor_ids: List[int] = settings.instructor_chat_ids
                is_main_admin = bool(instructor_ids and telegram_id == instructor_ids[0])
                invite_code = uuid.uuid4().hex
                cur.execute(
                    """
                    INSERT INTO trainers (telegram_id, full_name, username, is_main_admin, invite_code)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING invite_code
                    """,
                    (
                        telegram_id,
                        full_name or f"Trainer {telegram_id}",
                        username,
                        is_main_admin,
                        invite_code,
                    ),
                )
                (invite_code,) = cur.fetchone()
                return invite_code
    finally:
        conn.close()




