"""Модель заявки на тренировку."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class BookingRequest:
    """Заявка на бронирование слота у тренера."""
    id: str
    user_id: int
    username: str
    full_name: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    status: str  # pending|confirmed|declined|cancelled_by_client|cancelled_by_trainer
    created_at: str
    reminder_sent: bool = False
    trainer_id: Optional[int] = None  # id в таблице trainers (для уведомлений и фильтра)
