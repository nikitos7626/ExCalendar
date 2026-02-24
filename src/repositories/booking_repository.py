"""Репозиторий бронирований."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..models import BookingRequest
from .base import get_connection


def _time_to_str(val) -> str:
    if hasattr(val, "strftime"):
        return val.strftime("%H:%M")
    if hasattr(val, "total_seconds"):
        total = int(val.total_seconds())
        h, r = divmod(total, 3600)
        m = r // 60
        return f"{h:02d}:{m:02d}"
    return str(val)[:5]


def _row_to_request(row) -> BookingRequest:
    trainer_id = row[9] if len(row) >= 10 else None
    if trainer_id is not None:
        trainer_id = int(trainer_id)
    (req_id, user_id, username, full_name, date_val, time_val, status, created_at, reminder_sent) = row[:9]
    date_str = date_val.strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else str(date_val)[:10]
    time_str = _time_to_str(time_val)
    created_str = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
    return BookingRequest(
        id=str(req_id),
        user_id=int(user_id),
        username=username or "",
        full_name=full_name,
        date=date_str,
        time=time_str,
        status=status,
        created_at=created_str,
        reminder_sent=bool(reminder_sent),
        trainer_id=trainer_id,
    )


_SELECT_REQUEST_COLS = (
    "b.id, c.telegram_id AS user_id, c.username, c.full_name, "
    "b.date, b.time, b.status, b.created_at, b.reminder_sent, b.trainer_id"
)


class BookingRepository:
    """Доступ к таблице bookings."""

    def get_slot_state(
        self,
        trainer_id: int,
        date_str: str,
        time_str: str,
        conn=None,
    ) -> Optional[str]:
        own_conn = conn is None
        if conn is None:
            conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM bookings WHERE trainer_id = %s AND date = %s AND time = %s AND status IN ('pending', 'confirmed') LIMIT 1",
                    (trainer_id, date_str, time_str),
                )
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            if own_conn:
                conn.close()

    def create(
        self,
        conn,
        request_id: str,
        trainer_id: int,
        client_id: int,
        date_str: str,
        time_str: str,
        created_at: datetime,
    ) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bookings (id, trainer_id, client_id, date, time, status, created_at, reminder_sent) VALUES (%s, %s, %s, %s, %s, %s, %s, 0)",
                (request_id, trainer_id, client_id, date_str, time_str, "pending", created_at),
            )

    def update_status(self, request_id: str, status: str) -> None:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE bookings SET status = %s WHERE id = %s", (status, request_id))
                if cur.rowcount == 0:
                    raise KeyError(f"Request {request_id} not found")
            conn.commit()
        finally:
            conn.close()

    def get_by_id(self, request_id: str) -> Optional[BookingRequest]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_REQUEST_COLS} FROM bookings b JOIN clients c ON b.client_id = c.id WHERE b.id = %s",
                    (request_id,),
                )
                row = cur.fetchone()
                return _row_to_request(row) if row else None
        finally:
            conn.close()

    def get_by_id_with_conn(self, conn, request_id: str) -> Optional[BookingRequest]:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_SELECT_REQUEST_COLS} FROM bookings b JOIN clients c ON b.client_id = c.id WHERE b.id = %s",
                (request_id,),
            )
            row = cur.fetchone()
            return _row_to_request(row) if row else None

    def list_by_trainer_id(self, trainer_id: int) -> List[BookingRequest]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_REQUEST_COLS} FROM bookings b JOIN clients c ON b.client_id = c.id WHERE b.trainer_id = %s ORDER BY b.date, b.time",
                    (trainer_id,),
                )
                return [_row_to_request(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def list_all(self) -> List[BookingRequest]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_REQUEST_COLS} FROM bookings b JOIN clients c ON b.client_id = c.id ORDER BY b.date, b.time"
                )
                return [_row_to_request(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def list_by_client_telegram_id(self, user_id: int) -> List[BookingRequest]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_REQUEST_COLS} FROM bookings b JOIN clients c ON b.client_id = c.id WHERE c.telegram_id = %s ORDER BY b.date, b.time",
                    (user_id,),
                )
                return [_row_to_request(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def list_day_states(self, trainer_id: int, date_str: str) -> Dict[str, str]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT time, status FROM bookings WHERE trainer_id = %s AND date = %s",
                    (trainer_id, date_str),
                )
                return {_time_to_str(t): s for t, s in cur.fetchall()}
        finally:
            conn.close()

    def get_confirmed_for_tomorrow(self, tomorrow_date_str: str) -> List[BookingRequest]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_REQUEST_COLS} FROM bookings b JOIN clients c ON b.client_id = c.id WHERE b.status = 'confirmed' AND b.date = %s AND COALESCE(b.day_before_reminder_sent, 0) = 0 ORDER BY b.time",
                    (tomorrow_date_str,),
                )
                return [_row_to_request(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def get_upcoming_confirmed(self, now: datetime) -> List[BookingRequest]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_REQUEST_COLS} FROM bookings b JOIN clients c ON b.client_id = c.id WHERE b.status = 'confirmed' AND COALESCE(b.reminder_sent, FALSE) = FALSE"
                )
                rows = cur.fetchall()
            result = []
            for row in rows:
                br = _row_to_request(row)
                try:
                    slot_dt = datetime.strptime(f"{br.date} {br.time}", "%Y-%m-%d %H:%M")
                    reminder_time = slot_dt - timedelta(hours=2)
                    if now >= reminder_time and now < slot_dt:
                        result.append(br)
                except ValueError:
                    pass
            return result
        finally:
            conn.close()

    def mark_reminder_sent(self, request_id: str) -> None:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE bookings SET reminder_sent = 1 WHERE id = %s", (request_id,))
                if cur.rowcount == 0:
                    raise KeyError(request_id)
            conn.commit()
        finally:
            conn.close()

    def mark_day_before_reminder_sent(self, request_id: str) -> None:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE bookings SET day_before_reminder_sent = 1 WHERE id = %s", (request_id,))
                if cur.rowcount == 0:
                    raise KeyError(request_id)
            conn.commit()
        finally:
            conn.close()

    def get_confirmed_trainer_reminder_unsent(self) -> List[BookingRequest]:
        """Подтверждённые брони, по которым ещё не отправлено напоминание тренеру."""
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_SELECT_REQUEST_COLS} FROM bookings b JOIN clients c ON b.client_id = c.id "
                    "WHERE b.status = 'confirmed' AND COALESCE(b.trainer_reminder_sent, 0) = 0 ORDER BY b.date, b.time"
                )
                return [_row_to_request(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def mark_trainer_reminder_sent(self, request_id: str) -> None:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE bookings SET trainer_reminder_sent = 1 WHERE id = %s", (request_id,))
                if cur.rowcount == 0:
                    raise KeyError(request_id)
            conn.commit()
        finally:
            conn.close()

    def cleanup_expired(self, now: datetime) -> None:
        conn = get_connection()
        try:
            today_str = now.strftime("%Y-%m-%d")
            time_str = now.strftime("%H:%M")
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM bookings WHERE (date < %s) OR (date = %s AND time < %s)",
                    (today_str, today_str, time_str),
                )
            conn.commit()
        finally:
            conn.close()

    def get_slot_bookings_count(self, trainer_id: int, date_str: str, time_str: str) -> int:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM bookings WHERE trainer_id = %s AND date = %s AND time = %s AND status IN ('pending', 'confirmed')",
                    (trainer_id, date_str, time_str),
                )
                (count,) = cur.fetchone()
                return int(count)
        finally:
            conn.close()

    def get_all_unique_clients(self) -> List[dict]:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT c.telegram_id, c.username, c.full_name FROM bookings b JOIN clients c ON b.client_id = c.id WHERE b.status = 'confirmed'"
                )
                return [{"user_id": int(r[0]), "username": r[1] or "", "full_name": r[2] or ""} for r in cur.fetchall()]
        finally:
            conn.close()
