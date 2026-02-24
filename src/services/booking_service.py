"""Сервис бронирований: оркестрация репозиториев и бизнес-правила."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import BookingRequest
from ..repositories.base import get_connection
from ..repositories.booking_repository import BookingRepository
from ..repositories.client_repository import ClientRepository
from ..repositories.trainer_repository import TrainerRepository


class BookingService:
    """
    Брони привязаны к тренеру по ссылке (referred_by_trainer_id).
    Запись доступна только клиентам, пришедшим по ссылке тренера.
    """

    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path
        self._lock = threading.Lock()
        self._booking_repo = BookingRepository()
        self._client_repo = ClientRepository()
        self._trainer_repo = TrainerRepository()

    def get_trainer_id_for_client(self, client_telegram_id: int) -> Optional[int]:
        """Тренер, по чьей ссылке пришёл клиент, или None."""
        return self._client_repo.get_trainer_id_for_client(client_telegram_id)

    def get_slot_state(
        self,
        date_str: str,
        time_str: str,
        trainer_id: Optional[int] = None,
    ) -> Optional[str]:
        if trainer_id is None:
            trainer_id = self._trainer_repo.get_default_trainer_id()
        return self._booking_repo.get_slot_state(trainer_id, date_str, time_str)

    def create_request(
        self,
        *,
        user_id: int,
        username: Optional[str],
        full_name: str,
        date_str: str,
        time_str: str,
        is_group_slot: bool = False,
    ) -> BookingRequest:
        trainer_id = self._client_repo.get_trainer_id_for_client(user_id)
        if trainer_id is None:
            raise ValueError(
                "Клиент не привязан к тренеру. Чтобы записаться, откройте бота по ссылке от тренера."
            )
        conn = get_connection()
        try:
            with self._lock, conn:
                if not is_group_slot:
                    if self._booking_repo.get_slot_state(trainer_id, date_str, time_str, conn=conn):
                        raise ValueError("Slot already requested or confirmed")
                client_id = self._client_repo.get_or_create(
                    conn, user_id, username or "", full_name
                )
                req_id = str(uuid.uuid4())
                now = datetime.now()
                self._booking_repo.create(
                    conn, req_id, trainer_id, client_id, date_str, time_str, now
                )
                conn.commit()
                return BookingRequest(
                    id=req_id,
                    user_id=user_id,
                    username=username or "",
                    full_name=full_name,
                    date=date_str,
                    time=time_str,
                    status="pending",
                    created_at=now.isoformat(),
                    reminder_sent=False,
                    trainer_id=trainer_id,
                )
        finally:
            conn.close()

    def update_status(self, request_id: str, status: str) -> BookingRequest:
        self._booking_repo.update_status(request_id, status)
        req = self._booking_repo.get_by_id(request_id)
        if req is None:
            raise KeyError(request_id)
        return req

    def get_request(self, request_id: str) -> BookingRequest:
        req = self._booking_repo.get_by_id(request_id)
        if req is None:
            raise KeyError(request_id)
        return req

    def list_day_states(self, date_str: str, trainer_id: Optional[int] = None) -> Dict[str, str]:
        if trainer_id is None:
            trainer_id = self._trainer_repo.get_default_trainer_id()
        return self._booking_repo.list_day_states(trainer_id, date_str)

    def list_user_requests(self, user_id: int) -> List[BookingRequest]:
        return self._booking_repo.list_by_client_telegram_id(user_id)

    def list_all_requests(self) -> List[BookingRequest]:
        return self._booking_repo.list_all()

    def list_requests_for_trainer(self, trainer_telegram_id: int) -> List[BookingRequest]:
        tid = self._trainer_repo.get_id_by_telegram_id(trainer_telegram_id)
        if tid is None:
            return []
        return self._booking_repo.list_by_trainer_id(tid)

    def get_upcoming_confirmed(self, now: datetime) -> List[BookingRequest]:
        return self._booking_repo.get_upcoming_confirmed(now)

    def get_confirmed_for_tomorrow(self, tomorrow_date_str: str) -> List[BookingRequest]:
        return self._booking_repo.get_confirmed_for_tomorrow(tomorrow_date_str)

    def mark_reminder_sent(self, request_id: str) -> None:
        self._booking_repo.mark_reminder_sent(request_id)

    def mark_day_before_reminder_sent(self, request_id: str) -> None:
        self._booking_repo.mark_day_before_reminder_sent(request_id)

    def get_upcoming_for_trainer_reminder(
        self, now: datetime, minutes_before: int, timezone: Any
    ) -> List[BookingRequest]:
        """Брони, по которым нужно отправить напоминание тренеру (сейчас в окне за N минут до слота)."""
        unsent = self._booking_repo.get_confirmed_trainer_reminder_unsent()
        result = []
        for req in unsent:
            try:
                slot_naive = datetime.strptime(f"{req.date} {req.time}", "%Y-%m-%d %H:%M")
                slot_aware = timezone.localize(slot_naive)
                reminder_trigger = slot_aware - timedelta(minutes=minutes_before)
                if reminder_trigger <= now < slot_aware:
                    result.append(req)
            except (ValueError, Exception):
                continue
        return result

    def mark_trainer_reminder_sent(self, request_id: str) -> None:
        self._booking_repo.mark_trainer_reminder_sent(request_id)

    def cleanup_expired(self, now: datetime) -> None:
        self._booking_repo.cleanup_expired(now)

    def get_slot_bookings_count(
        self,
        date_str: str,
        time_str: str,
        trainer_id: Optional[int] = None,
    ) -> int:
        if trainer_id is None:
            trainer_id = self._trainer_repo.get_default_trainer_id()
        return self._booking_repo.get_slot_bookings_count(trainer_id, date_str, time_str)

    def get_all_unique_clients(self) -> List[dict]:
        return self._booking_repo.get_all_unique_clients()
