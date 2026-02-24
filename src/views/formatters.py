"""Форматирование дат, слотов и константы интерфейса."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

# Месяцы и дни недели для отображения
MONTHS_RU_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
MONTHS_RU_SHORT = [
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]
WEEKDAYS_RU_FULL = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]
WEEKDAYS_RU_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def format_day_short(day: date) -> str:
    return f"{day.day:02d} {MONTHS_RU_SHORT[day.month - 1]} ({WEEKDAYS_RU_SHORT[day.weekday()]})"


def format_day_full(day: date) -> str:
    return f"{day.day:02d} {MONTHS_RU_GEN[day.month - 1]} ({WEEKDAYS_RU_FULL[day.weekday()]})"


def humanize_slot(date_str: str, time_str: str, timezone: Any) -> str:
    """Форматирование слота для сообщений (дата · время)."""
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    localized = timezone.localize(dt)
    return f"{format_day_full(localized.date())} · {localized.strftime('%H:%M')}"


STATUS_BADGES = {
    "pending": "⌛ Ожидает подтверждения",
    "confirmed": "✅ Подтверждена",
    "cancelled_by_trainer": "🚫 Отменена тренером",
    "cancelled_by_client": "🚫 Отменена клиентом",
    "declined": "❌ Отклонена",
}

# Кнопки главного меню
BUTTON_BOOK = "📅 Записаться"
BUTTON_MY = "🗓 Мои записи"
BUTTON_TRAINER = "📘 Записи тренера"
BUTTON_MY_CLIENTS = "👥 Мои клиенты"
BUTTON_SCHEDULE = "⚙️ Управление расписанием"
BUTTON_STATS = "📊 Статистика"
MENU_BUTTONS = {BUTTON_BOOK, BUTTON_MY, BUTTON_TRAINER, BUTTON_MY_CLIENTS, BUTTON_SCHEDULE, BUTTON_STATS}

__all__ = [
    "MONTHS_RU_GEN", "MONTHS_RU_SHORT", "WEEKDAYS_RU_FULL", "WEEKDAYS_RU_SHORT",
    "format_day_short", "format_day_full", "humanize_slot",
    "STATUS_BADGES", "BUTTON_BOOK", "BUTTON_MY", "BUTTON_TRAINER", "BUTTON_MY_CLIENTS",
    "BUTTON_SCHEDULE", "BUTTON_STATS", "MENU_BUTTONS",
]
