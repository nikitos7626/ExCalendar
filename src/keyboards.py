from __future__ import annotations

from datetime import date
from typing import Dict, Iterable, List, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_dates_keyboard(
    dates: List[Tuple[date, str]],
    page: int,
    page_size: int,
    group_dates: set[str] | None = None,
) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    total = len(dates)
    start = page * page_size
    end = start + page_size
    slice_items = dates[start:end]

    group_dates = group_dates or set()
    for day, label in slice_items:
        # Mark group slots with emoji
        if day.isoformat() in group_dates:
            display_label = f"👥 {label}"
        else:
            display_label = label
        row.append(InlineKeyboardButton(display_label, callback_data=f"DATE|{day.isoformat()}"))
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


def build_times_keyboard(date_str: str, times: Iterable[str], back_page: int, group_times: set[str] | None = None) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    group_times = group_times or set()
    for time_str in times:
        # Mark group slots with emoji
        if time_str in group_times:
            display_time = f"👥 {time_str}"
        else:
            display_time = time_str
        row.append(
            InlineKeyboardButton(
                display_time,
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


def build_request_review_keyboard(
    request_id: str, 
    client_user_id: int | None = None, 
    is_main_admin: bool = False,
    is_client_trainer: bool = False,
) -> InlineKeyboardMarkup:
    """Build keyboard for reviewing request. Shows 'Add as trainer' button for main admin if client is not already a trainer."""
    buttons = [
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"REVIEW|{request_id}|approve"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"REVIEW|{request_id}|decline"),
        ]
    ]
    # Add "Add as trainer" button for main admin if client_user_id provided and client is not already a trainer
    if is_main_admin and client_user_id is not None and not is_client_trainer:
        buttons.append([
            InlineKeyboardButton("👤 Добавить как тренера", callback_data=f"ADD_TR_REQ|{request_id}|{client_user_id}"),
        ])
    return InlineKeyboardMarkup(buttons)


def build_pending_requests_keyboard(options: Iterable[Tuple[str, str]]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []
    for request_id, label in options:
        buttons.append(
            [
                InlineKeyboardButton(f"✅ {label}", callback_data=f"REVIEW|{request_id}|approve"),
                InlineKeyboardButton("❌", callback_data=f"REVIEW|{request_id}|decline"),
            ]
        )
    if not buttons:
        buttons = [[InlineKeyboardButton("Нет заявок", callback_data="IGNORE")]]
    return InlineKeyboardMarkup(buttons)


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


def build_schedule_manage_keyboard(user_id: int | None = None, is_main_admin: bool = False) -> InlineKeyboardMarkup:
    """Build schedule management keyboard. Shows trainer management only for main admin."""
    buttons = [
        [InlineKeyboardButton("📅 Управление днями", callback_data="SCHEDULE_DAYS")],
        [InlineKeyboardButton("📆 Управление неделями", callback_data="SCHEDULE_WEEKS")],
        [InlineKeyboardButton("⚙️ Настройка рабочего времени", callback_data="SCHEDULE_WORK_SETTINGS")],
        [InlineKeyboardButton("👥 Постоянные клиенты", callback_data="REGULAR_CLIENTS")],
        [InlineKeyboardButton("👥 Групповые слоты", callback_data="GROUP_SLOTS")],
    ]
    # Only show trainer management for main admin
    if is_main_admin:
        buttons.append([InlineKeyboardButton("👤 Управление тренерами", callback_data="MANAGE_TRAINERS")])
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_BACK")])
    return InlineKeyboardMarkup(buttons)


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


def build_regular_clients_keyboard(clients: List[Dict]) -> InlineKeyboardMarkup:
    """Build keyboard for managing regular clients."""
    buttons: List[List[InlineKeyboardButton]] = []
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    for client in clients:
        label = f"{client.get('full_name', 'Unknown')} (@{client.get('username', 'no_username')})"
        weekday = client.get("weekday")
        time_str = client.get("time")
        if weekday is not None and time_str:
            slot_info = f" · {weekdays[weekday]} {time_str}"
        else:
            slot_info = " · не назначено"
        buttons.append([
            InlineKeyboardButton(
                f"📅 {label}{slot_info}",
                callback_data=f"REGULAR_CLIENT_SLOT|{client['user_id']}",
            )
        ])
        buttons.append([
            InlineKeyboardButton(
                f"❌ Удалить",
                callback_data=f"REMOVE_CLIENT|{client['user_id']}",
            )
        ])
    if not buttons:
        buttons = [[InlineKeyboardButton("Нет постоянных клиентов", callback_data="IGNORE")]]
    buttons.append([InlineKeyboardButton("➕ Добавить клиента", callback_data="ADD_REGULAR_CLIENT")])
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_MENU")])
    return InlineKeyboardMarkup(buttons)


def build_add_regular_client_keyboard(clients: List[Dict]) -> InlineKeyboardMarkup:
    """Build keyboard for selecting clients to add as regular."""
    buttons: List[List[InlineKeyboardButton]] = []
    for client in clients:
        label = f"{client.get('full_name', 'Unknown')} (@{client.get('username', 'no_username')})"
        buttons.append([
            InlineKeyboardButton(
                f"➕ {label}",
                callback_data=f"ADD_CLIENT|{client['user_id']}",
            )
        ])
    if not buttons:
        buttons = [[InlineKeyboardButton("Нет доступных клиентов", callback_data="IGNORE")]]
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="REGULAR_CLIENTS")])
    return InlineKeyboardMarkup(buttons)


def build_group_slots_keyboard(group_slots: List[Dict], times: List[str]) -> InlineKeyboardMarkup:
    """Build keyboard for managing group slots."""
    buttons: List[List[InlineKeyboardButton]] = []
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    
    # Show existing group slots
    for slot in group_slots:
        weekday = slot["weekday"]
        time_str = slot["time"]
        label = f"{weekdays[weekday]} {time_str}"
        buttons.append([
            InlineKeyboardButton(
                f"❌ {label}",
                callback_data=f"REMOVE_GROUP_SLOT|{weekday}|{time_str}",
            )
        ])
    
    if not buttons:
        buttons = [[InlineKeyboardButton("Нет групповых слотов", callback_data="IGNORE")]]
    
    buttons.append([InlineKeyboardButton("➕ Добавить групповой слот", callback_data="ADD_GROUP_SLOT")])
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_MENU")])
    return InlineKeyboardMarkup(buttons)


def build_add_group_slot_weekday_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for selecting weekday for group slot."""
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
        row.append(
            InlineKeyboardButton(
                label,
                callback_data=f"GROUP_SLOT_WEEKDAY|{day_num}",
            )
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="GROUP_SLOTS")])
    return InlineKeyboardMarkup(buttons)


def build_add_group_slot_time_keyboard(weekday: int, times: List[str]) -> InlineKeyboardMarkup:
    """Build keyboard for selecting time for group slot."""
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for time_str in times:
        row.append(
            InlineKeyboardButton(
                time_str,
                callback_data=f"GROUP_SLOT_TIME|{weekday}|{time_str}",
            )
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="ADD_GROUP_SLOT")])
    return InlineKeyboardMarkup(buttons)


def build_regular_client_slot_keyboard(user_id: int, weekday: int | None, time_str: str | None) -> InlineKeyboardMarkup:
    """Build keyboard for managing regular client slot."""
    buttons: List[List[InlineKeyboardButton]] = []
    if weekday is not None and time_str:
        weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        buttons.append([
            InlineKeyboardButton(
                f"📅 Текущий слот: {weekdays[weekday]} {time_str}",
                callback_data="IGNORE",
            )
        ])
        buttons.append([
            InlineKeyboardButton(
                "🔄 Изменить слот",
                callback_data=f"REGULAR_CLIENT_SET_WEEKDAY|{user_id}",
            )
        ])
        buttons.append([
            InlineKeyboardButton(
                "❌ Удалить слот",
                callback_data=f"REGULAR_CLIENT_CLEAR_SLOT|{user_id}",
            )
        ])
    else:
        buttons.append([
            InlineKeyboardButton(
                "➕ Назначить слот",
                callback_data=f"REGULAR_CLIENT_SET_WEEKDAY|{user_id}",
            )
        ])
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="REGULAR_CLIENTS")])
    return InlineKeyboardMarkup(buttons)


def build_regular_client_weekday_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build keyboard for selecting weekday for regular client slot."""
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
        row.append(
            InlineKeyboardButton(
                label,
                callback_data=f"REGULAR_CLIENT_WEEKDAY|{user_id}|{day_num}",
            )
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data=f"REGULAR_CLIENT_SLOT|{user_id}")])
    return InlineKeyboardMarkup(buttons)


def build_regular_client_time_keyboard(user_id: int, weekday: int, times: List[str]) -> InlineKeyboardMarkup:
    """Build keyboard for selecting time for regular client slot."""
    buttons: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for time_str in times:
        row.append(
            InlineKeyboardButton(
                time_str,
                callback_data=f"REGULAR_CLIENT_TIME|{user_id}|{weekday}|{time_str}",
            )
        )
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data=f"REGULAR_CLIENT_SET_WEEKDAY|{user_id}")])
    return InlineKeyboardMarkup(buttons)


def build_trainers_keyboard(trainers: List[Dict]) -> InlineKeyboardMarkup:
    """Build keyboard for managing trainers."""
    buttons: List[List[InlineKeyboardButton]] = []
    for trainer in trainers:
        label = f"{trainer.get('full_name', 'Unknown')} (@{trainer.get('username', 'no_username')})"
        buttons.append([
            InlineKeyboardButton(
                f"❌ {label}",
                callback_data=f"REMOVE_TRAINER|{trainer['user_id']}",
            )
        ])
    if not buttons:
        buttons = [[InlineKeyboardButton("Нет добавленных тренеров", callback_data="IGNORE")]]
    buttons.append([InlineKeyboardButton("➕ Добавить тренера", callback_data="ADD_TRAINER")])
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data="SCHEDULE_MENU")])
    return InlineKeyboardMarkup(buttons)


def build_confirm_add_trainer_keyboard(request_id: str, user_id: int) -> InlineKeyboardMarkup:
    """Build keyboard for confirming trainer addition from request."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, добавить", callback_data=f"CFM_TR_REQ|{request_id}|{user_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"CNL_TR_REQ|{request_id}"),
        ]
    ])


def build_confirm_add_trainer_by_id_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build keyboard for confirming trainer addition by ID."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, добавить", callback_data=f"CFM_TR_ID|{user_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data="MANAGE_TRAINERS"),
        ]
    ])

