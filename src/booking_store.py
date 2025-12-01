from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .config import load_settings
from .db import get_connection

# Cache settings at module level to avoid re-reading config on every call
_settings_cache = None
_settings_lock = threading.Lock()


def _get_cached_settings():
    """Get settings, caching them after first load."""
    global _settings_cache
    if _settings_cache is None:
        with _settings_lock:
            if _settings_cache is None:
                _settings_cache = load_settings()
    return _settings_cache


@dataclass(slots=True)
class BookingRequest:
    id: str
    user_id: int
    username: str
    full_name: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    status: str  # pending|confirmed|declined|cancelled_by_client|cancelled_by_trainer
    created_at: str
    reminder_sent: bool = False  # Whether 2-hour reminder was sent


class BookingStore:
    """
    Booking storage backed by PostgreSQL (table 'bookings' + 'clients' + 'trainers').

    Phase 2: replaces JSON-based storage. Interface совместим с предыдущей версией.
    Пока что все брони привязаны к "дефолтному" тренеру (главному администратору).
    """

    def __init__(self, storage_path: Path):
        # storage_path сохраняем только для совместимости, фактически не используем.
        self.storage_path = storage_path
        self._lock = threading.Lock()

    # --- internal helpers -------------------------------------------------

    def _get_default_trainer_id(self, conn) -> int:
        """
        Возвращает id тренера, к которому сейчас привязываем все брони.
        Пока что это первый INSTRUCTOR_CHAT_ID из настроек.
        """
        main_admin_ids = _get_cached_settings().instructor_chat_ids
        if not main_admin_ids:
            raise RuntimeError("No INSTRUCTOR_CHAT_ID configured")
        main_admin_telegram_id = main_admin_ids[0]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM trainers WHERE telegram_id = %s
                """,
                (main_admin_telegram_id,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(
                    "Default trainer not found in DB. "
                    "Ensure run_migrations()/initial trainer sync executed successfully."
                )
            return row[0]

    def _get_or_create_client(self, conn, telegram_id: int, username: str, full_name: str) -> int:
        """Возвращает id клиента по telegram_id, создавая запись при необходимости."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM clients WHERE telegram_id = %s",
                (telegram_id,),
            )
            row = cur.fetchone()
            if row:
                client_id = row[0]
                # Обновим имя/username на всякий случай
                cur.execute(
                    """
                    UPDATE clients
                    SET username = %s, full_name = %s
                    WHERE id = %s
                    """,
                    (username or "", full_name, client_id),
                )
                return client_id

            cur.execute(
                """
                INSERT INTO clients (telegram_id, full_name, username)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (telegram_id, full_name, username or ""),
            )
            client_id = cur.fetchone()[0]
            return client_id

    def _row_to_request(self, row) -> BookingRequest:
        """
        Преобразовать строку SELECT в BookingRequest.
        Ожидаемый порядок колонок:
        id, client_telegram_id, client_username, client_full_name,
        date, time, status, created_at, reminder_sent
        """
        (
            req_id,
            user_id,
            username,
            full_name,
            date_val,
            time_val,
            status,
            created_at,
            reminder_sent,
        ) = row
        return BookingRequest(
            id=str(req_id),
            user_id=int(user_id),
            username=username or "",
            full_name=full_name,
            date=date_val.strftime("%Y-%m-%d"),
            time=time_val.strftime("%H:%M"),
            status=status,
            created_at=created_at.isoformat(),
            reminder_sent=bool(reminder_sent),
        )

    # --- public API -------------------------------------------------------

    def get_slot_state(self, date_str: str, time_str: str) -> Optional[str]:
        """
        Вернуть статус слота (pending/confirmed) для дефолтного тренера или None.
        """
        conn = get_connection()
        try:
            trainer_id = self._get_default_trainer_id(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status
                    FROM bookings
                    WHERE trainer_id = %s
                      AND date = %s
                      AND time = %s
                      AND status IN ('pending', 'confirmed')
                    LIMIT 1
                    """,
                    (trainer_id, date_str, time_str),
                )
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()

    def create_request(
        self,
        *,
        user_id: int,
        username: str | None,
        full_name: str,
        date_str: str,
        time_str: str,
        is_group_slot: bool = False,
    ) -> BookingRequest:
        """
        Создать новую заявку.

        Для групповых слотов разрешаем несколько записей на одно время,
        для обычных — проверяем, что слот не занят.
        """
        conn = get_connection()
        try:
            with self._lock, conn:
                trainer_id = self._get_default_trainer_id(conn)
                # Проверка занятости слота (только для одиночных слотов)
                if not is_group_slot:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT 1
                            FROM bookings
                            WHERE trainer_id = %s
                              AND date = %s
                              AND time = %s
                              AND status IN ('pending', 'confirmed')
                            LIMIT 1
                            """,
                            (trainer_id, date_str, time_str),
                        )
                        if cur.fetchone():
                            raise ValueError("Slot already requested or confirmed")

                client_id = self._get_or_create_client(
                    conn, telegram_id=user_id, username=username or "", full_name=full_name
                )
                req_id = uuid.uuid4()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO bookings (
                            id, trainer_id, client_id, date, time,
                            status, created_at, reminder_sent
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, NOW(), FALSE)
                        RETURNING id, %s AS user_id, %s AS username, %s AS full_name,
                                  date, time, status, created_at, reminder_sent
                        """,
                        (
                            req_id,
                            trainer_id,
                            client_id,
                            date_str,
                            time_str,
                            "pending",
                            user_id,
                            username or "",
                            full_name,
                        ),
                    )
                    row = cur.fetchone()
                conn.commit()
                return self._row_to_request(row)
        finally:
            conn.close()

    def update_status(self, request_id: str, status: str) -> BookingRequest:
        conn = get_connection()
        try:
            with self._lock, conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE bookings
                        SET status = %s
                        WHERE id = %s::uuid
                        RETURNING
                            id,
                            c.telegram_id AS user_id,
                            c.username,
                            c.full_name,
                            date,
                            time,
                            status,
                            created_at,
                            reminder_sent
                        FROM clients c
                        WHERE bookings.client_id = c.id
                          AND bookings.id = %s::uuid
                        """,
                        (status, request_id, request_id),
                    )
                    row = cur.fetchone()
                if not row:
                    raise KeyError(f"Request {request_id} not found")
                conn.commit()
                return self._row_to_request(row)
        finally:
            conn.close()

    def get_request(self, request_id: str) -> BookingRequest:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        b.id,
                        c.telegram_id AS user_id,
                        c.username,
                        c.full_name,
                        b.date,
                        b.time,
                        b.status,
                        b.created_at,
                        b.reminder_sent
                    FROM bookings b
                    JOIN clients c ON b.client_id = c.id
                    WHERE b.id = %s::uuid
                    """,
                    (request_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise KeyError(request_id)
                return self._row_to_request(row)
        finally:
            conn.close()

    def list_day_states(self, date_str: str) -> Dict[str, str]:
        """Return map time -> status for slots with any activity (для дефолтного тренера)."""
        conn = get_connection()
        try:
            trainer_id = self._get_default_trainer_id(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT time, status
                    FROM bookings
                    WHERE trainer_id = %s
                      AND date = %s
                    """,
                    (trainer_id, date_str),
                )
                result: Dict[str, str] = {}
                for time_val, status in cur.fetchall():
                    result[time_val.strftime("%H:%M")] = status
                return result
        finally:
            conn.close()

    def list_user_requests(self, user_id: int) -> List[BookingRequest]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        b.id,
                        c.telegram_id AS user_id,
                        c.username,
                        c.full_name,
                        b.date,
                        b.time,
                        b.status,
                        b.created_at,
                        b.reminder_sent
                    FROM bookings b
                    JOIN clients c ON b.client_id = c.id
                    WHERE c.telegram_id = %s
                    ORDER BY b.date, b.time
                    """,
                    (user_id,),
                )
                rows = cur.fetchall()
                return [self._row_to_request(r) for r in rows]
        finally:
            conn.close()

    def list_all_requests(self) -> List[BookingRequest]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        b.id,
                        c.telegram_id AS user_id,
                        c.username,
                        c.full_name,
                        b.date,
                        b.time,
                        b.status,
                        b.created_at,
                        b.reminder_sent
                    FROM bookings b
                    JOIN clients c ON b.client_id = c.id
                    ORDER BY b.date, b.time
                    """
                )
                rows = cur.fetchall()
                return [self._row_to_request(r) for r in rows]
        finally:
            conn.close()

    def get_upcoming_confirmed(self, now: datetime) -> List[BookingRequest]:
        """Get confirmed requests that need reminder (2 hours before start)."""
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        b.id,
                        c.telegram_id AS user_id,
                        c.username,
                        c.full_name,
                        b.date,
                        b.time,
                        b.status,
                        b.created_at,
                        b.reminder_sent
                    FROM bookings b
                    JOIN clients c ON b.client_id = c.id
                    WHERE b.status = 'confirmed'
                      AND COALESCE(b.reminder_sent, FALSE) = FALSE
                    """
                )
                rows = cur.fetchall()
                result: List[BookingRequest] = []
                for row in rows:
                    br = self._row_to_request(row)
                    try:
                        slot_dt = datetime.strptime(
                            f"{br.date} {br.time}", "%Y-%m-%d %H:%M"
                        )
                        reminder_time = slot_dt - timedelta(hours=2)
                        if now >= reminder_time and now < slot_dt:
                            result.append(br)
                    except ValueError:
                        continue
                return result
        finally:
            conn.close()

    def mark_reminder_sent(self, request_id: str) -> None:
        """Mark reminder as sent for a request."""
        conn = get_connection()
        try:
            with self._lock, conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE bookings
                        SET reminder_sent = TRUE
                        WHERE id = %s::uuid
                        """,
                        (request_id,),
                    )
                if cur.rowcount == 0:
                    raise KeyError(f"Request {request_id} not found")
                conn.commit()
        finally:
            conn.close()

    def cleanup_expired(self, now: datetime) -> None:
        """Remove requests that finished before 'now'."""
        conn = get_connection()
        try:
            with self._lock, conn:
                today_str = now.strftime("%Y-%m-%d")
                time_str = now.strftime("%H:%M")
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        DELETE FROM bookings
                        WHERE (date < %s)
                           OR (date = %s AND time < %s::time)
                        """,
                        (today_str, today_str, time_str),
                    )
                conn.commit()
        finally:
            conn.close()

    def get_slot_bookings_count(self, date_str: str, time_str: str) -> int:
        """Get count of confirmed/pending bookings for a slot (дефолтный тренер)."""
        conn = get_connection()
        try:
            trainer_id = self._get_default_trainer_id(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM bookings
                    WHERE trainer_id = %s
                      AND date = %s
                      AND time = %s
                      AND status IN ('pending', 'confirmed')
                    """,
                    (trainer_id, date_str, time_str),
                )
                (count,) = cur.fetchone()
                return int(count)
        finally:
            conn.close()

    def get_all_unique_clients(self) -> List[Dict]:
        """Get all unique clients (user_id, username, full_name) from confirmed requests."""
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT
                        c.telegram_id AS user_id,
                        c.username,
                        c.full_name
                    FROM bookings b
                    JOIN clients c ON b.client_id = c.id
                    WHERE b.status = 'confirmed'
                    """
                )
                rows = cur.fetchall()
                result: List[Dict] = []
                for user_id, username, full_name in rows:
                    result.append(
                        {
                            "user_id": int(user_id),
                            "username": username or "",
                            "full_name": full_name,
                        }
                    )
                return result
        finally:
            conn.close()


