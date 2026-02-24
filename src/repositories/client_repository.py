"""Репозиторий клиентов."""
from __future__ import annotations

from typing import Optional

from .base import get_connection
from .trainer_repository import TrainerRepository


class ClientRepository:
    """Доступ к таблице clients."""

    def __init__(self) -> None:
        self._trainer_repo = TrainerRepository()

    def get_or_create(
        self,
        conn,
        telegram_id: int,
        username: str,
        full_name: str,
    ) -> int:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM clients WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
            if row:
                client_id = row[0]
                cur.execute(
                    "UPDATE clients SET username = %s, full_name = %s WHERE id = %s",
                    (username, full_name, client_id),
                )
                return client_id
            cur.execute(
                "INSERT INTO clients (telegram_id, full_name, username) VALUES (%s, %s, %s)",
                (telegram_id, full_name, username),
            )
            return cur.lastrowid

    def get_trainer_id_for_client(self, client_telegram_id: int) -> Optional[int]:
        """Тренер, по чьей ссылке пришёл клиент, или None."""
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT referred_by_trainer_id FROM clients WHERE telegram_id = %s",
                    (client_telegram_id,),
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    return row[0]
                return None
        finally:
            conn.close()

    def ensure_referred_by(
        self,
        telegram_id: int,
        full_name: str,
        username: Optional[str],
        invite_code: str,
    ) -> None:
        trainer_id = self._trainer_repo.get_id_by_invite_code(invite_code)
        if trainer_id is None:
            return
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO clients (telegram_id, full_name, username, referred_by_trainer_id)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        full_name = VALUES(full_name),
                        username = VALUES(username),
                        referred_by_trainer_id = COALESCE(clients.referred_by_trainer_id, VALUES(referred_by_trainer_id))
                    """,
                    (telegram_id, full_name or "", username or "", trainer_id),
                )
            conn.commit()
        finally:
            conn.close()
