from __future__ import annotations

from datetime import date
from typing import Iterable, List, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_dates_keyboard(
    dates: List[Tuple[date, str]],
    page: int,
    page_size: int,
) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    total = len(dates)
    start = page * page_size
    end = start + page_size
    slice_items = dates[start:end]

    for day, label in slice_items:
        row.append(InlineKeyboardButton(label, callback_data=f"DATE|{day.isoformat()}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if not buttons:
        return InlineKeyboardMarkup([[InlineKeyboardButton("Нет доступных дней", callback_data="IGNORE")]])

    total_pages = max((total + page_size - 1) // page_size, 1)
    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️", callback_data=f"DATES|{page-1}"))
    nav_row.append(
        InlineKeyboardButton(f"{min(page + 1, total_pages)}/{total_pages}", callback_data="IGNORE")
    )
    if end < total:
        nav_row.append(InlineKeyboardButton("▶️", callback_data=f"DATES|{page+1}"))
    buttons.append(nav_row)

    return InlineKeyboardMarkup(buttons)


def build_times_keyboard(date_str: str, times: Iterable[str], back_page: int) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for time_str in times:
        row.append(
            InlineKeyboardButton(
                time_str,
                callback_data=f"TIME|{date_str}|{time_str}",
            )
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    if not buttons:
        buttons = [[InlineKeyboardButton("Нет свободных слотов", callback_data="IGNORE")]]
    buttons.append(
        [
            InlineKeyboardButton(
                "↩️ Выбрать другой день",
                callback_data=f"BACK_TO_DATES|{back_page}",
            )
        ]
    )
    return InlineKeyboardMarkup(buttons)


def build_request_review_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Принять", callback_data=f"REVIEW|{request_id}|approve"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"REVIEW|{request_id}|decline"),
            ]
        ]
    )


def build_decline_reason_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Отклонить без причины",
                    callback_data=f"DECLINE_SKIP|{request_id}",
                )
            ]
        ]
    )


def build_cancel_select_keyboard(options: Iterable[Tuple[str, str]]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for request_id, label in options:
        buttons.append([InlineKeyboardButton(label, callback_data=f"CANCEL|{request_id}")])
    return InlineKeyboardMarkup(buttons or [[InlineKeyboardButton("Нет активных записей", callback_data="IGNORE")]])


def build_cancel_reason_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Отменить без причины",
                    callback_data=f"CANCEL_SKIP|{request_id}",
                )
            ]
        ]
    )


def build_admin_cancel_keyboard(options: Iterable[Tuple[str, str]]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for request_id, label in options:
        buttons.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"ADMIN_CANCEL|{request_id}",
                )
            ]
        )
    if not buttons:
        buttons = [[InlineKeyboardButton("Нет активных записей", callback_data="IGNORE")]]
    return InlineKeyboardMarkup(buttons)


def build_admin_cancel_reason_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Отменить без причины",
                    callback_data=f"ADMIN_CANCEL_SKIP|{request_id}",
                )
            ]
        ]
    )


def build_schedule_manage_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📅 Управление днями", callback_data="SCHEDULE_DAYS")],
            [InlineKeyboardButton("📆 Управление неделями", callback_data="SCHEDULE_WEEKS")],
            [InlineKeyboardButton("⚙️ Настройка рабочего времени", callback_data="SCHEDULE_WORK_SETTINGS")],
            [InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_BACK")],
        ]
    )


def build_schedule_days_keyboard(
    dates: List[Tuple[date, str]], page: int, page_size: int
) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    total = len(dates)
    start = page * page_size
    end = start + page_size
    slice_items = dates[start:end]

    for day, label in slice_items:
        row.append(InlineKeyboardButton(label, callback_data=f"SCHEDULE_DAY|{day.isoformat()}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    if not buttons:
        buttons = [[InlineKeyboardButton("Нет доступных дней", callback_data="IGNORE")]]

    total_pages = max((total + page_size - 1) // page_size, 1)
    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️", callback_data=f"SCHEDULE_DAYS_PAGE|{page-1}"))
    nav_row.append(
        InlineKeyboardButton(f"{min(page + 1, total_pages)}/{total_pages}", callback_data="IGNORE")
    )
    if end < total:
        nav_row.append(InlineKeyboardButton("▶️", callback_data=f"SCHEDULE_DAYS_PAGE|{page+1}"))
    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_MENU")])

    return InlineKeyboardMarkup(buttons)


def build_schedule_day_times_keyboard(
    date_str: str, times: List[str], blocked_times: List[str]
) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for time_str in times:
        is_blocked = time_str in blocked_times
        emoji = "🔒" if is_blocked else "🔓"
        action = "unblock" if is_blocked else "block"
        row.append(
            InlineKeyboardButton(
                f"{emoji} {time_str}",
                callback_data=f"SCHEDULE_SLOT|{date_str}|{time_str}|{action}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(
        [
            InlineKeyboardButton("🔒 Закрыть весь день", callback_data=f"SCHEDULE_DAY_BLOCK|{date_str}"),
            InlineKeyboardButton("🔓 Открыть весь день", callback_data=f"SCHEDULE_DAY_UNBLOCK|{date_str}"),
        ]
    )
    buttons.append([InlineKeyboardButton("↩️ Назад к дням", callback_data="SCHEDULE_DAYS")])
    return InlineKeyboardMarkup(buttons)


def build_schedule_weeks_keyboard(
    weeks: List[Tuple[date, str]], page: int, page_size: int
) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for week_start, label in weeks[page * page_size : (page + 1) * page_size]:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🔒 Закрыть {label}",
                    callback_data=f"SCHEDULE_WEEK_BLOCK|{week_start.isoformat()}",
                ),
                InlineKeyboardButton(
                    f"🔓 Открыть {label}",
                    callback_data=f"SCHEDULE_WEEK_UNBLOCK|{week_start.isoformat()}",
                ),
            ]
        )

    if not buttons:
        buttons = [[InlineKeyboardButton("Нет доступных недель", callback_data="IGNORE")]]

    total = len(weeks)
    total_pages = max((total + page_size - 1) // page_size, 1)
    nav_row: List[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️", callback_data=f"SCHEDULE_WEEKS_PAGE|{page-1}"))
    nav_row.append(
        InlineKeyboardButton(f"{min(page + 1, total_pages)}/{total_pages}", callback_data="IGNORE")
    )
    if (page + 1) * page_size < total:
        nav_row.append(InlineKeyboardButton("▶️", callback_data=f"SCHEDULE_WEEKS_PAGE|{page+1}"))
    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_MENU")])

    return InlineKeyboardMarkup(buttons)


def build_work_settings_keyboard(has_custom: bool) -> InlineKeyboardMarkup:
    """Build main work settings keyboard."""
    buttons = [
        [InlineKeyboardButton("📅 Настроить дни недели", callback_data="WORK_SETTINGS_DAYS")],
        [InlineKeyboardButton("🕐 Настроить часы работы", callback_data="WORK_SETTINGS_HOURS")],
    ]
    if has_custom:
        buttons.append([InlineKeyboardButton("🔄 Сбросить к настройкам по умолчанию", callback_data="WORK_SETTINGS_RESET")])
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_MENU")])
    return InlineKeyboardMarkup(buttons)


def build_work_days_keyboard(selected_days: List[int]) -> InlineKeyboardMarkup:
    """Build keyboard for selecting work days."""
    weekdays = [
        ("Пн", 0),
        ("Вт", 1),
        ("Ср", 2),
        ("Чт", 3),
        ("Пт", 4),
        ("Сб", 5),
        ("Вс", 6),
    ]
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for label, day_num in weekdays:
        emoji = "✅" if day_num in selected_days else "❌"
        row.append(
            InlineKeyboardButton(
                f"{emoji} {label}",
                callback_data=f"WORK_DAY_TOGGLE|{day_num}",
            )
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✅ Применить", callback_data="WORK_DAYS_APPLY")])
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_WORK_SETTINGS")])
    return InlineKeyboardMarkup(buttons)


def build_work_hours_keyboard(start_hour: int, end_hour: int) -> InlineKeyboardMarkup:
    """Build keyboard for selecting work hours."""
    buttons: List[List[InlineKeyboardButton]] = []
    
    # Start hour selection
    start_row: List[InlineKeyboardButton] = []
    for h in range(6, 13):
        emoji = "✅" if h == start_hour else "⚪"
        start_row.append(
            InlineKeyboardButton(f"{emoji} {h:02d}:00", callback_data=f"WORK_START|{h}")
        )
        if len(start_row) == 3:
            buttons.append(start_row)
            start_row = []
    if start_row:
        buttons.append(start_row)
    
    buttons.append([InlineKeyboardButton("─", callback_data="IGNORE")])
    
    # End hour selection
    end_row: List[InlineKeyboardButton] = []
    for h in range(13, 24):
        emoji = "✅" if h == end_hour else "⚪"
        end_row.append(
            InlineKeyboardButton(f"{emoji} {h:02d}:00", callback_data=f"WORK_END|{h}")
        )
        if len(end_row) == 3:
            buttons.append(end_row)
            end_row = []
    if end_row:
        buttons.append(end_row)
    
    buttons.append([InlineKeyboardButton("✅ Применить", callback_data="WORK_HOURS_APPLY")])
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_WORK_SETTINGS")])
    return InlineKeyboardMarkup(buttons)

