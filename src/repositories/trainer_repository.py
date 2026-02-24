"""Репозиторий тренеров."""
from __future__ import annotations

import uuid
from typing import List, Optional

from ..config import load_settings
from .base import get_connection


class TrainerRepository:
    """Доступ к таблице trainers."""

    def get_invite_code(self, telegram_id: int) -> Optional[str]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT invite_code FROM trainers WHERE telegram_id = %s", (telegram_id,))
                row = cur.fetchone()
                return row[0] if row and row[0] else None
        finally:
            conn.close()

    def get_id_by_telegram_id(self, telegram_id: int) -> Optional[int]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM trainers WHERE telegram_id = %s", (telegram_id,))
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()

    def get_telegram_id_by_id(self, trainer_id: int) -> Optional[int]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT telegram_id FROM trainers WHERE id = %s", (trainer_id,))
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()

    def get_id_by_invite_code(self, invite_code: str) -> Optional[int]:
        if not invite_code or not invite_code.strip():
            return None
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM trainers WHERE invite_code = %s", (invite_code.strip(),))
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()

    def ensure_with_invite(
        self,
        telegram_id: int,
        username: Optional[str],
        full_name: Optional[str],
    ) -> str:
        code = self.get_invite_code(telegram_id)
        if code:
            return code
        settings = load_settings()
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM trainers WHERE telegram_id = %s", (telegram_id,))
                row = cur.fetchone()
                invite_code = uuid.uuid4().hex
                if row:
                    trainer_id = row[0]
                    cur.execute(
                        "UPDATE trainers SET invite_code = %s, username = COALESCE(%s, username), full_name = COALESCE(%s, full_name) WHERE id = %s",
                        (invite_code, username, full_name or f"Trainer {telegram_id}", trainer_id),
                    )
                else:
                    instructor_ids = settings.instructor_chat_ids
                    is_main_admin = 1 if (instructor_ids and telegram_id == instructor_ids[0]) else 0
                    cur.execute(
                        "INSERT INTO trainers (telegram_id, full_name, username, is_main_admin, invite_code) VALUES (%s, %s, %s, %s, %s)",
                        (telegram_id, full_name or f"Trainer {telegram_id}", username, is_main_admin, invite_code),
                    )
                conn.commit()
                return invite_code
        finally:
            conn.close()

    def get_default_trainer_id(self) -> int:
        settings = load_settings()
        ids = settings.instructor_chat_ids
        if not ids:
            raise RuntimeError("No INSTRUCTOR_CHAT_ID configured")
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM trainers WHERE telegram_id = %s", (ids[0],))
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("Default trainer not found. Run run_migrations() and ensure_initial_trainers().")
                return row[0]
        finally:
            conn.close()

    def get_clients_referred_by(self, trainer_telegram_id: int) -> List[dict]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM trainers WHERE telegram_id = %s", (trainer_telegram_id,))
                row = cur.fetchone()
                if not row:
                    return []
                trainer_id = row[0]
                cur.execute(
                    "SELECT telegram_id, username, full_name FROM clients WHERE referred_by_trainer_id = %s ORDER BY full_name",
                    (trainer_id,),
                )
                rows = cur.fetchall()
                return [{"user_id": int(r[0]), "username": (r[1] or ""), "full_name": (r[2] or "")} for r in rows]
        finally:
            conn.close()
