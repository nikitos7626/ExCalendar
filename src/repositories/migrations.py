"""Миграции БД: создание и обновление таблиц."""
from __future__ import annotations

import logging
from ..config import load_settings
from .base import get_connection

logger = logging.getLogger(__name__)


def run_migrations() -> None:
    conn = get_connection()
    try:
        for ddl in _DDL_STATEMENTS:
            with conn.cursor() as cur:
                cur.execute(ddl)
        conn.commit()
        _run_client_migration(conn)
        _run_bookings_day_before_migration(conn)
        _run_trainer_reminder_sent_migration(conn)
        logger.info("MySQL migrations applied successfully.")
    finally:
        conn.close()


def _run_client_migration(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clients'
              AND COLUMN_NAME = 'referred_by_trainer_id'
            """
        )
        (has_col,) = cur.fetchone()
    if not has_col:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE clients ADD COLUMN referred_by_trainer_id INT NULL, "
                "ADD INDEX idx_referred_by (referred_by_trainer_id), "
                "ADD CONSTRAINT fk_clients_referred_by FOREIGN KEY (referred_by_trainer_id) "
                "REFERENCES trainers(id) ON DELETE SET NULL"
            )
        conn.commit()
        logger.info("Migration: added referred_by_trainer_id to clients.")


def _run_bookings_day_before_migration(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'bookings'
              AND COLUMN_NAME = 'day_before_reminder_sent'
            """
        )
        (has_col,) = cur.fetchone()
    if not has_col:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE bookings ADD COLUMN day_before_reminder_sent TINYINT(1) NOT NULL DEFAULT 0"
            )
        conn.commit()
        logger.info("Migration: added day_before_reminder_sent to bookings.")


def _run_trainer_reminder_sent_migration(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'bookings'
              AND COLUMN_NAME = 'trainer_reminder_sent'
            """
        )
        (has_col,) = cur.fetchone()
    if not has_col:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE bookings ADD COLUMN trainer_reminder_sent TINYINT(1) NOT NULL DEFAULT 0"
            )
        conn.commit()
        logger.info("Migration: added trainer_reminder_sent to bookings.")


def ensure_initial_trainers() -> None:
    import uuid
    settings = load_settings()
    instructor_ids = settings.instructor_chat_ids
    if not instructor_ids:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for idx, telegram_id in enumerate(instructor_ids):
                is_main_admin = 1 if idx == 0 else 0
                cur.execute(
                    "SELECT id, invite_code FROM trainers WHERE telegram_id = %s",
                    (telegram_id,),
                )
                row = cur.fetchone()
                if row:
                    trainer_id, existing_code = row
                    if not existing_code:
                        new_code = uuid.uuid4().hex
                        cur.execute(
                            "UPDATE trainers SET invite_code = %s, is_main_admin = GREATEST(is_main_admin, %s) WHERE id = %s",
                            (new_code, is_main_admin, trainer_id),
                        )
                else:
                    new_code = uuid.uuid4().hex
                    cur.execute(
                        "INSERT INTO trainers (telegram_id, full_name, username, is_main_admin, invite_code) VALUES (%s, %s, %s, %s, %s)",
                        (telegram_id, f"Trainer {telegram_id}", None, is_main_admin, new_code),
                    )
        conn.commit()
        logger.info("Initial trainers synced from INSTRUCTOR_CHAT_ID.")
    finally:
        conn.close()


_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS trainers (
        id INT AUTO_INCREMENT PRIMARY KEY,
        telegram_id BIGINT NOT NULL UNIQUE,
        full_name VARCHAR(255) NOT NULL,
        username VARCHAR(255) NULL,
        is_main_admin TINYINT(1) NOT NULL DEFAULT 0,
        invite_code VARCHAR(64) NULL UNIQUE,
        INDEX idx_telegram_id (telegram_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS clients (
        id INT AUTO_INCREMENT PRIMARY KEY,
        telegram_id BIGINT NOT NULL UNIQUE,
        full_name VARCHAR(255) NOT NULL,
        username VARCHAR(255) NULL,
        referred_by_trainer_id INT NULL,
        INDEX idx_telegram_id (telegram_id),
        INDEX idx_referred_by (referred_by_trainer_id),
        FOREIGN KEY (referred_by_trainer_id) REFERENCES trainers(id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS bookings (
        id CHAR(36) PRIMARY KEY,
        trainer_id INT NOT NULL,
        client_id INT NOT NULL,
        date DATE NOT NULL,
        time TIME NOT NULL,
        status VARCHAR(32) NOT NULL,
        created_at DATETIME(6) NOT NULL,
        reminder_sent TINYINT(1) NOT NULL DEFAULT 0,
        day_before_reminder_sent TINYINT(1) NOT NULL DEFAULT 0,
        trainer_reminder_sent TINYINT(1) NOT NULL DEFAULT 0,
        INDEX idx_trainer_date_time (trainer_id, date, time),
        INDEX idx_client (client_id),
        INDEX idx_status_reminder (status, reminder_sent),
        FOREIGN KEY (trainer_id) REFERENCES trainers(id) ON DELETE CASCADE,
        FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS schedule_settings (
        trainer_id INT PRIMARY KEY,
        workdays VARCHAR(64) NULL,
        work_start_hour INT NULL,
        work_end_hour INT NULL,
        manual_slots JSON NULL,
        FOREIGN KEY (trainer_id) REFERENCES trainers(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS blocked_slots (
        id INT AUTO_INCREMENT PRIMARY KEY,
        trainer_id INT NOT NULL,
        date DATE NOT NULL,
        time TIME NULL,
        INDEX idx_trainer_date (trainer_id, date),
        FOREIGN KEY (trainer_id) REFERENCES trainers(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]
