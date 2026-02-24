from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv

WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


@dataclass(slots=True, frozen=True)
class Settings:
    bot_token: str
    instructor_chat_ids: List[int]  # List of admin/instructor chat IDs (можно пустой, если заданы usernames)
    instructor_usernames: List[str]  # Тренеры по @username (без @, lowercase)
    database_url: str | None
    bot_username: str | None
    timezone: str
    workdays: List[int]
    work_start_hour: int
    work_end_hour: int
    slot_duration_minutes: int
    booking_horizon_days: int
    trainer_reminder_minutes: int  # За сколько минут напоминать тренеру о тренировке
    storage_path: Path


def _parse_workdays(raw: str | None) -> List[int]:
    if not raw:
        return [0, 1, 2, 3, 4, 5]
    result: List[int] = []
    for item in raw.split(","):
        key = item.strip().lower()
        if not key:
            continue
        if key not in WEEKDAY_ALIASES:
            raise ValueError(f"Unknown weekday alias: {item}")
        value = WEEKDAY_ALIASES[key]
        if value not in result:
            result.append(value)
    if not result:
        raise ValueError("At least one working day must be provided")
    return result


def load_settings(env_file: str | None = None) -> Settings:
    """Load settings from .env, config.env or environment variables."""
    if env_file:
        load_dotenv(env_file)
    else:
        # load defaults from config.env first, allow .env to override them
        load_dotenv("config.env")
        load_dotenv(".env", override=True)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    instructor_chat_id_raw = os.getenv("INSTRUCTOR_CHAT_ID", "").strip()
    instructor_usernames_raw = os.getenv("INSTRUCTOR_USERNAMES", "").strip()
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    # Тренеры: по chat_id и/или по @username (без символа @)
    instructor_chat_ids: List[int] = []
    for id_str in instructor_chat_id_raw.split(",") if instructor_chat_id_raw else []:
        id_str = id_str.strip()
        if id_str:
            try:
                instructor_chat_ids.append(int(id_str))
            except ValueError:
                raise ValueError(f"Invalid INSTRUCTOR_CHAT_ID: {id_str} is not a valid integer")

    instructor_usernames: List[str] = []
    for u in instructor_usernames_raw.split(",") if instructor_usernames_raw else []:
        u = u.strip().lstrip("@").lower()
        if u and u not in instructor_usernames:
            instructor_usernames.append(u)

    if not instructor_chat_ids and not instructor_usernames:
        raise RuntimeError(
            "Set at least one of INSTRUCTOR_CHAT_ID or INSTRUCTOR_USERNAMES "
            "(e.g. INSTRUCTOR_USERNAMES=nikit,coach)"
        )

    timezone = os.getenv("TIMEZONE", "Europe/Moscow")
    database_url = os.getenv("DATABASE_URL") or None
    bot_username = os.getenv("BOT_USERNAME") or None
    workdays = _parse_workdays(os.getenv("WORKING_DAYS"))
    work_start_hour = int(os.getenv("WORK_START_HOUR", "8"))
    work_end_hour = int(os.getenv("WORK_END_HOUR", "22"))
    booking_horizon_days = min(
        max(int(os.getenv("BOOKING_HORIZON_DAYS", "14")), 1),
        30,
    )
    trainer_reminder_minutes = min(
        max(int(os.getenv("TRAINER_REMINDER_MINUTES", "15")), 1),
        120,
    )

    if work_end_hour <= work_start_hour:
        raise ValueError("WORK_END_HOUR must be greater than WORK_START_HOUR")

    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        bot_token=bot_token,
        instructor_chat_ids=instructor_chat_ids,
        instructor_usernames=instructor_usernames,
        database_url=database_url,
        bot_username=bot_username,
        timezone=timezone,
        workdays=workdays,
        work_start_hour=work_start_hour,
        work_end_hour=work_end_hour,
        slot_duration_minutes=60,
        booking_horizon_days=booking_horizon_days,
        trainer_reminder_minutes=trainer_reminder_minutes,
        storage_path=data_dir / "bookings.json",
    )

