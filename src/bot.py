from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Dict, Iterable, List, Tuple

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    JobQueue,
    MessageHandler,
    filters,
)

from .booking_store import BookingStore, BookingRequest
from .config import Settings, load_settings
from .schedule_manager import ScheduleManager
from .keyboards import (
    build_admin_cancel_keyboard,
    build_admin_cancel_reason_keyboard,
    build_add_group_slot_time_keyboard,
    build_add_group_slot_weekday_keyboard,
    build_add_regular_client_keyboard,
    build_cancel_reason_keyboard,
    build_cancel_select_keyboard,
    build_confirm_add_trainer_by_id_keyboard,
    build_confirm_add_trainer_keyboard,
    build_dates_keyboard,
    build_decline_reason_keyboard,
    build_group_slots_keyboard,
    build_pending_requests_keyboard,
    build_regular_client_slot_keyboard,
    build_regular_client_time_keyboard,
    build_regular_client_weekday_keyboard,
    build_regular_clients_keyboard,
    build_request_review_keyboard,
    build_schedule_day_times_keyboard,
    build_schedule_days_keyboard,
    build_schedule_manage_keyboard,
    build_schedule_weeks_keyboard,
    build_times_keyboard,
    build_trainers_keyboard,
    build_work_days_keyboard,
    build_work_hours_keyboard,
    build_work_settings_keyboard,
    build_manual_schedule_keyboard,
    build_manual_day_times_keyboard,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


settings: Settings = load_settings()
store = BookingStore(settings.storage_path)
schedule_manager = ScheduleManager(settings.storage_path.parent / "schedule_blocks.json")
timezone = pytz.timezone(settings.timezone)
daily_slots = []
CLIENT_CANCELABLE_STATUSES = {"pending", "confirmed"}
TRAINER_CANCELABLE_STATUSES = {"pending", "confirmed"}
STATUS_BADGES = {
    "pending": "⌛ Ожидает подтверждения",
    "confirmed": "✅ Подтверждена",
    "cancelled_by_trainer": "🚫 Отменена тренером",
    "cancelled_by_client": "🚫 Отменена клиентом",
    "declined": "❌ Отклонена",
}
BUTTON_BOOK = "📅 Записаться"
BUTTON_MY = "🗓 Мои записи"
BUTTON_TRAINER = "📘 Записи тренера"
BUTTON_SCHEDULE = "⚙️ Управление расписанием"
BUTTON_STATS = "📊 Статистика"
MENU_BUTTONS = {BUTTON_BOOK, BUTTON_MY, BUTTON_TRAINER, BUTTON_SCHEDULE, BUTTON_STATS}
DATES_PAGE_SIZE = 6
MANUAL_TIME_OPTIONS = [f"{hour:02d}:00" for hour in range(0, 24)]


def is_instructor(user_id: int) -> bool:
    """Check if user is an instructor (from config or dynamically added)."""
    if user_id in settings.instructor_chat_ids:
        return True
    # Check dynamically added trainers
    dynamic_trainer_ids = schedule_manager.get_all_trainer_ids()
    return user_id in dynamic_trainer_ids


def is_main_admin(user_id: int) -> bool:
    """Check if user is the main administrator (first ID from config.env)."""
    if not settings.instructor_chat_ids:
        return False
    return user_id == settings.instructor_chat_ids[0]


async def send_to_all_instructors(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
) -> None:
    """Send message to all instructors/admins (from config + dynamically added)."""
    all_instructor_ids = set(settings.instructor_chat_ids)
    # Add dynamically added trainers
    dynamic_trainer_ids = schedule_manager.get_all_trainer_ids()
    all_instructor_ids.update(dynamic_trainer_ids)
    
    for chat_id in all_instructor_ids:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning(f"Failed to send message to instructor {chat_id}: {e}")


def build_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    if is_instructor(user_id):
        if is_main_admin(user_id):
            rows = [[BUTTON_TRAINER], [BUTTON_SCHEDULE], [BUTTON_STATS]]
        else:
            rows = [[BUTTON_TRAINER], [BUTTON_SCHEDULE]]
    else:
        rows = [[BUTTON_BOOK, BUTTON_MY]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

MONTHS_RU_GEN = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]
MONTHS_RU_SHORT = [
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
]
WEEKDAYS_RU_FULL = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
]
WEEKDAYS_RU_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def get_effective_work_settings() -> Tuple[List[int], int, int]:
    """Get work settings from schedule_manager or fallback to config."""
    custom = schedule_manager.get_work_settings()
    if custom:
        return (
            custom["workdays"],
            custom["work_start_hour"],
            custom["work_end_hour"],
        )
    return settings.workdays, settings.work_start_hour, settings.work_end_hour


def get_manual_schedule() -> Dict[int, List[str]]:
    """Return manual weekly schedule normalized with integer weekday keys."""
    raw = schedule_manager.get_manual_slots()
    manual: Dict[int, List[str]] = {}
    for day_key, times in raw.items():
        try:
            day_num = int(day_key)
        except (TypeError, ValueError):
            continue
        normalized = sorted(
            {t for t in times if isinstance(t, str) and len(t) == 5 and ":" in t}
        )
        if normalized:
            manual[day_num] = normalized
    return manual


def has_manual_schedule() -> bool:
    """Check if trainer configured manual day/time slots."""
    return bool(get_manual_schedule())


def get_active_workdays() -> List[int]:
    """Return weekdays that should be shown to clients."""
    manual = get_manual_schedule()
    if manual:
        return sorted(manual.keys())
    workdays, _, _ = get_effective_work_settings()
    return workdays


def get_slots_for_weekday(weekday: int) -> List[str]:
    """Return list of base slots for given weekday (manual or default)."""
    manual = get_manual_schedule()
    if manual:
        return manual.get(weekday, [])
    return daily_slots


def get_slots_for_date(target_date: date) -> List[str]:
    """Convenience wrapper for date objects."""
    return get_slots_for_weekday(target_date.weekday())


def _generate_daily_slots() -> List[str]:
    """Generate daily slots based on current work settings."""
    _, work_start, work_end = get_effective_work_settings()
    slots: List[str] = []
    slot_delta = timedelta(minutes=settings.slot_duration_minutes)
    cursor = datetime.combine(date.today(), time(hour=work_start))
    end_time = time(hour=work_end)
    while cursor.time() < end_time:
        slots.append(cursor.strftime("%H:%M"))
        cursor += slot_delta
        if cursor.time() == time(0, 0):
            break
    return slots


def refresh_daily_slots() -> None:
    """Refresh daily slots when work settings change."""
    global daily_slots
    daily_slots = _generate_daily_slots()


daily_slots = _generate_daily_slots()


def collect_available_dates_with_labels() -> Tuple[List[Tuple[date, str]], set[str]]:
    cleanup_expired_requests()
    schedule_manager.cleanup_past_blocks(datetime.now(timezone))
    refresh_daily_slots()  # Refresh in case settings changed
    workdays = get_active_workdays()
    today = datetime.now(timezone).date()
    result: List[Tuple[date, str]] = []
    group_dates: set[str] = set()
    blocked_dates = schedule_manager.get_all_blocked_dates()
    group_slots = schedule_manager.get_group_slots()
    
    for offset in range(settings.booking_horizon_days):
        candidate = today + timedelta(days=offset)
        slots_for_day = get_slots_for_date(candidate)
        if candidate.weekday() in workdays and slots_for_day:
            date_str = candidate.isoformat()
            blocked_slots = schedule_manager.get_blocked_slots_for_day(date_str)
            if len(blocked_slots) < len(slots_for_day):
                result.append((candidate, format_day_short(candidate)))
                weekday = candidate.weekday()
                for slot in group_slots:
                    if slot["weekday"] == weekday:
                        group_dates.add(date_str)
                        break
    return result, group_dates


def format_day_short(day: date) -> str:
    return f"{day.day:02d} {MONTHS_RU_SHORT[day.month - 1]} ({WEEKDAYS_RU_SHORT[day.weekday()]})"


def format_day_full(day: date) -> str:
    return f"{day.day:02d} {MONTHS_RU_GEN[day.month - 1]} ({WEEKDAYS_RU_FULL[day.weekday()]})"


def find_dates_and_page(date_str: str) -> Tuple[List[Tuple[date, str]], int, bool, set[str]]:
    dates, group_dates = collect_available_dates_with_labels()
    page = 0
    found = False
    for idx, (day, _) in enumerate(dates):
        if day.isoformat() == date_str:
            page = idx // DATES_PAGE_SIZE
            found = True
            break
    return dates, page, found, group_dates


def humanize_slot(date_str: str, time_str: str) -> str:
    dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    localized = timezone.localize(dt)
    return f"{format_day_full(localized.date())} · {localized.strftime('%H:%M')}"


def build_cancel_options(requests: List[BookingRequest]) -> List[Tuple[str, str]]:
    options: List[Tuple[str, str]] = []
    for req in requests:
        status_text = STATUS_BADGES.get(req.status, "")
        label = f"{status_text} · {humanize_slot(req.date, req.time)}" if status_text else humanize_slot(req.date, req.time)
        options.append((req.id, label))
    return options


def cleanup_expired_requests() -> None:
    now_local = datetime.now(timezone).replace(tzinfo=None)
    store.cleanup_expired(now_local)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    # Track user activity
    schedule_manager.track_user_activity(user.id)
    await update.message.reply_text(
        "Привет! Я помогу записаться на индивидуальную тренировку.\n"
        "Нажми «📅 Записаться», чтобы выбрать дату и время (1 час).\n"
        "В разделе «🗓 Мои записи» можно увидеть активные заявки и отменить их.",
        reply_markup=build_main_keyboard(user.id),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    # Track user activity
    schedule_manager.track_user_activity(user.id)
    await update.message.reply_text(
        "Алгоритм записи:\n"
        "1. Кнопка «📅 Записаться» — выбираешь день и свободный час.\n"
        "2. Я отправляю запрос тренеру.\n"
        "3. После подтверждения приходит уведомление.\n"
        "4. Кнопка «🗓 Мои записи» — посмотреть активные заявки и отменить их.\n\n"
        "Если нужна другая длительность или есть вопросы, напиши тренеру напрямую.",
        reply_markup=build_main_keyboard(update.effective_user.id),
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot statistics (admin only)."""
    user = update.effective_user
    if not is_main_admin(user.id):
        await update.message.reply_text(
            "Эта команда доступна только главному администратору.",
            reply_markup=build_main_keyboard(user.id),
        )
        return
    await show_stats_command(update, context)


async def show_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display statistics report."""
    user = update.effective_user
    if not is_main_admin(user.id):
        if update.message:
            await update.message.reply_text(
                "Эта функция доступна только главному администратору.",
                reply_markup=build_main_keyboard(user.id),
            )
        return
    
    # Get user statistics
    user_stats = schedule_manager.get_user_stats_summary()
    
    # Get booking statistics
    all_requests = store.list_all_requests()
    total_bookings = len(all_requests)
    confirmed_bookings = len([r for r in all_requests if r.status == "confirmed"])
    pending_bookings = len([r for r in all_requests if r.status == "pending"])
    cancelled_bookings = len([r for r in all_requests if "cancelled" in r.status])
    declined_bookings = len([r for r in all_requests if r.status == "declined"])
    
    # Get unique clients count
    unique_clients = len(set(r.user_id for r in all_requests))
    
    # Get upcoming confirmed bookings
    now = datetime.now(timezone)
    upcoming_confirmed = [
        r for r in all_requests
        if r.status == "confirmed"
        and timezone.localize(datetime.strptime(f"{r.date} {r.time}", "%Y-%m-%d %H:%M")) > now
    ]
    
    # Get trainers count
    total_trainers = len(settings.instructor_chat_ids) + len(schedule_manager.get_trainers())
    
    # Build statistics message
    stats_text = (
        "<b>📊 Статистика бота</b>\n\n"
        f"<b>👥 Пользователи:</b>\n"
        f"Всего пользователей: {user_stats['total_users']}\n"
        f"Активных сегодня: {user_stats['active_today']}\n"
        f"Активных за неделю: {user_stats['active_week']}\n"
        f"Активных за месяц: {user_stats['active_month']}\n\n"
        f"<b>📅 Заявки:</b>\n"
        f"Всего заявок: {total_bookings}\n"
        f"Подтверждено: {confirmed_bookings}\n"
        f"Ожидают подтверждения: {pending_bookings}\n"
        f"Отменено: {cancelled_bookings}\n"
        f"Отклонено: {declined_bookings}\n\n"
        f"<b>📋 Дополнительно:</b>\n"
        f"Уникальных клиентов: {unique_clients}\n"
        f"Предстоящих тренировок: {len(upcoming_confirmed)}\n"
        f"Всего тренеров: {total_trainers}\n"
    )
    
    if update.message:
        await update.message.reply_text(
            stats_text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_keyboard(user.id),
        )
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            stats_text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_keyboard(user.id),
        )


async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    # Track user activity
    schedule_manager.track_user_activity(user.id)
    if is_instructor(user.id):
        await update.message.reply_text(
            "Тренеру недоступна запись. Используйте кнопку «📘 Записи тренера».",
            reply_markup=build_main_keyboard(user.id),
        )
        return
    dates, group_dates = collect_available_dates_with_labels()
    if not dates:
        await update.message.reply_text(
            "На ближайшее время нет рабочих дней для записи. Попробуйте позже.",
            reply_markup=build_main_keyboard(user.id),
        )
        return
    keyboard = build_dates_keyboard(dates, page=0, page_size=DATES_PAGE_SIZE, group_dates=group_dates)
    await update.message.reply_text(
        "Выбери подходящий день:\n👥 — групповой слот (можно записаться нескольким)",
        reply_markup=keyboard,
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    # Track user activity
    schedule_manager.track_user_activity(user.id)
    if is_instructor(user.id):
        await update.message.reply_text(
            "Эта функция доступна только клиентам. Используйте «📘 Записи тренера».",
            reply_markup=build_main_keyboard(user.id),
        )
        return
    cleanup_expired_requests()
    requests = [
        req
        for req in store.list_user_requests(user.id)
        if req.status in CLIENT_CANCELABLE_STATUSES
    ]
    if not requests:
        await update.message.reply_text(
            "У вас нет активных записей, которые можно отменить.",
            reply_markup=build_main_keyboard(user.id),
        )
        return
    keyboard = build_cancel_select_keyboard(build_cancel_options(requests))
    await update.message.reply_text(
        "Выберите запись, которую хотите отменить:",
        reply_markup=keyboard,
    )


async def show_instructor_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_instructor(update.effective_user.id):
        await update.message.reply_text(
            "Только тренер может просматривать этот список.",
            reply_markup=build_main_keyboard(update.effective_user.id),
        )
        return
    cleanup_expired_requests()
    requests = store.list_all_requests()
    now = datetime.now(timezone)
    lines: List[str] = []
    options: List[Tuple[str, str]] = []
    pending_actions: List[Tuple[str, str]] = []
    for req in requests:
        slot_dt = timezone.localize(datetime.strptime(f"{req.date} {req.time}", "%Y-%m-%d %H:%M"))
        slot_label_short = f"{format_day_short(slot_dt.date())} · {slot_dt.strftime('%H:%M')}"
        if slot_dt < now:
            continue
        status = STATUS_BADGES.get(req.status, req.status)
        lines.append(f"{humanize_slot(req.date, req.time)} — {req.full_name} ({status})")
        if req.status in TRAINER_CANCELABLE_STATUSES:
            label = f"{humanize_slot(req.date, req.time)} · {req.full_name}"
            options.append((req.id, label))
        if req.status == "pending":
            label = f"{slot_label_short} · {req.full_name}"
            pending_actions.append((req.id, label))
    if lines:
        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=build_main_keyboard(update.effective_user.id),
        )
    else:
        await update.message.reply_text(
            "Нет активных записей. Заявки появятся после выбора времени клиентом.",
            reply_markup=build_main_keyboard(update.effective_user.id),
        )
    if pending_actions:
        await update.message.reply_text(
            "Выбери заявку, чтобы подтвердить или отклонить её:",
            reply_markup=build_pending_requests_keyboard(pending_actions),
        )
    if options:
        await update.message.reply_text(
            "Выбери запись, чтобы отменить её:",
            reply_markup=build_admin_cancel_keyboard(options),
        )


async def date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    _, date_str = query.data.split("|", maxsplit=1)
    dates, page, found, group_dates = find_dates_and_page(date_str)
    if not found:
        await query.edit_message_text(
            "Эта дата больше недоступна. Выберите заново:",
            reply_markup=build_dates_keyboard(dates, page=0, page_size=DATES_PAGE_SIZE, group_dates=group_dates),
        )
        return
    day_label = format_day_full(datetime.strptime(date_str, "%Y-%m-%d").date())
    busy_map = store.list_day_states(date_str)
    blocked_slots = set(schedule_manager.get_blocked_slots_for_day(date_str))
    
    # Get group times for this date
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    weekday = dt.weekday()
    group_slots = schedule_manager.get_group_slots()
    group_times = {slot["time"] for slot in group_slots if slot["weekday"] == weekday}
    
    base_slots = get_slots_for_date(dt)
    free_slots = []
    for slot in base_slots:
        if slot in blocked_slots:
            continue
        # For group slots, allow multiple bookings
        if slot in group_times:
            free_slots.append(slot)
        # For regular slots, check if already booked
        elif slot not in busy_map or busy_map.get(slot) not in {"pending", "confirmed"}:
            free_slots.append(slot)
    
    keyboard = build_times_keyboard(date_str, free_slots, back_page=page, group_times=group_times)
    await query.edit_message_text(
        text=f"Свободные часы на {day_label}:\n👥 — групповой слот (можно записаться нескольким)",
        reply_markup=keyboard,
    )


async def back_to_dates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|", maxsplit=1)
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    dates, group_dates = collect_available_dates_with_labels()
    max_page = max((len(dates) - 1) // DATES_PAGE_SIZE, 0)
    page = max(0, min(page, max_page))
    if not dates:
        await query.edit_message_text(
            "На ближайшие даты запись недоступна. Попробуйте позже.",
            reply_markup=build_main_keyboard(query.from_user.id),
        )
        return
    await query.edit_message_text(
        "Выбери подходящий день:\n👥 — групповой слот (можно записаться нескольким)",
        reply_markup=build_dates_keyboard(dates, page=page, page_size=DATES_PAGE_SIZE, group_dates=group_dates),
    )


async def time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    # Track user activity
    schedule_manager.track_user_activity(query.from_user.id)
    _, date_str, time_str = query.data.split("|", maxsplit=2)

    slot_dt = timezone.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
    if slot_dt < datetime.now(timezone):
        await query.answer("Нельзя выбрать прошедшее время", show_alert=True)
        return

    # Check if slot is blocked
    if schedule_manager.is_slot_blocked(date_str, time_str):
        await query.answer("Этот слот закрыт для записи тренером", show_alert=True)
        dates, page, _, group_dates = find_dates_and_page(date_str)
        await query.edit_message_text(
            "Этот слот закрыт. Выбери другую дату:",
            reply_markup=build_dates_keyboard(dates, page=page, page_size=DATES_PAGE_SIZE, group_dates=group_dates),
        )
        return

    # Check if it's a group slot
    is_group_slot = schedule_manager.is_group_slot(date_str, time_str)
    
    dates, page, _, group_dates = find_dates_and_page(date_str)
    user = query.from_user
    try:
        request = store.create_request(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            date_str=date_str,
            time_str=time_str,
            is_group_slot=is_group_slot,
        )
    except ValueError:
        await query.answer("Слот уже занят. Выбери другой.", show_alert=True)
        await query.edit_message_text(
            "Этот час уже занят. Выбери другую дату:",
            reply_markup=build_dates_keyboard(dates, page=page, page_size=DATES_PAGE_SIZE, group_dates=group_dates),
        )
        return

    slot_type = "групповой слот" if is_group_slot else "индивидуальный слот"
    await query.edit_message_text(
        f"Запрос на {humanize_slot(date_str, time_str)} ({slot_type}) отправлен тренеру.\n"
        "Я напомню, когда он подтвердит запись."
    )

    bookings_count = store.get_slot_bookings_count(date_str, time_str)
    admin_text = (
        "<b>Новая заявка</b>\n"
        f"Дата: {humanize_slot(date_str, time_str)}\n"
        f"Тип: {slot_type}\n"
        f"Клиент: {user.full_name} "
        f"(@{user.username or 'нет'} | id {user.id})"
    )
    if is_group_slot:
        admin_text += f"\nЗаписей на этот слот: {bookings_count}"
    # Send to first instructor with keyboard, others without
    all_instructor_ids = list(settings.instructor_chat_ids) + schedule_manager.get_all_trainer_ids()
    if all_instructor_ids:
        first_admin_id = all_instructor_ids[0]
        is_first_main_admin = is_main_admin(first_admin_id)
        is_client_already_trainer = is_instructor(user.id)
        await context.bot.send_message(
            chat_id=first_admin_id,
            text=admin_text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_request_review_keyboard(request.id, user.id, is_first_main_admin, is_client_already_trainer),
        )
        # Send to other instructors without keyboard
        for chat_id in all_instructor_ids[1:]:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=admin_text,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning(f"Failed to send message to instructor {chat_id}: {e}")


async def cancel_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, request_id = query.data.split("|", maxsplit=1)
    user = query.from_user
    try:
        request = store.get_request(request_id)
    except KeyError:
        await query.edit_message_text("Запись не найдена.")
        return
    if request.user_id != user.id:
        await query.answer("Эта запись принадлежит другому пользователю.", show_alert=True)
        return
    if request.status not in CLIENT_CANCELABLE_STATUSES:
        await query.edit_message_text("Эту запись уже нельзя отменить.")
        return

    context.user_data["awaiting_cancel_reason"] = {
        "request_id": request_id,
        "message_chat_id": query.message.chat_id,
        "message_id": query.message.message_id,
    }
    await query.edit_message_text(
        f"Укажите причину отмены записи на {humanize_slot(request.date, request.time)} (по желанию) "
        "или нажмите кнопку ниже.",
        reply_markup=build_cancel_reason_keyboard(request_id),
    )


async def admin_cancel_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, request_id = query.data.split("|", maxsplit=1)
    try:
        request = store.get_request(request_id)
    except KeyError:
        await query.edit_message_text("Запись не найдена.")
        return
    if request.status not in TRAINER_CANCELABLE_STATUSES:
        await query.edit_message_text("Эту запись уже нельзя отменить.")
        return
    context.chat_data["awaiting_admin_cancel"] = {
        "request_id": request_id,
        "message_chat_id": query.message.chat_id,
        "message_id": query.message.message_id,
    }
    await query.edit_message_text(
        f"Напишите причину отмены тренировки {humanize_slot(request.date, request.time)} (по желанию)\n"
        "или нажмите кнопку ниже.",
        reply_markup=build_admin_cancel_reason_keyboard(request_id),
    )


async def dates_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, page_str = query.data.split("|", maxsplit=1)
    page = int(page_str) if page_str.isdigit() else 0
    dates, group_dates = collect_available_dates_with_labels()
    max_page = max((len(dates) - 1) // DATES_PAGE_SIZE, 0)
    page = max(0, min(page, max_page))
    if not dates:
        await query.edit_message_text(
            "Нет доступных дат. Попробуйте позже.",
            reply_markup=build_main_keyboard(query.from_user.id),
        )
        return
    await query.edit_message_text(
        "Выбери подходящий день:\n👥 — групповой слот (можно записаться нескольким)",
        reply_markup=build_dates_keyboard(dates, page=page, page_size=DATES_PAGE_SIZE, group_dates=group_dates),
    )


async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Только тренер может управлять заявками", show_alert=True)
        return

    await query.answer()
    _, request_id, action = query.data.split("|", maxsplit=2)
    try:
        request = store.get_request(request_id)
    except KeyError:
        await query.edit_message_text("Заявка не найдена или уже удалена.")
        return

    if request.status != "pending":
        await query.edit_message_text(f"Заявка уже {request.status}.")
        return

    if action == "approve":
        await finalize_request_decision(
            request=request,
            new_status="confirmed",
            context=context,
            edit_target=(query.message.chat_id, query.message.message_id),
        )
        return

    pending_decline = context.chat_data.get("awaiting_reason")
    if pending_decline and pending_decline["request_id"] != request_id:
        await query.answer("Сначала завершите отказ по предыдущей заявке.", show_alert=True)
        return

    context.chat_data["awaiting_reason"] = {
        "request_id": request_id,
        "message_chat_id": query.message.chat_id,
        "message_id": query.message.message_id,
    }
    await query.edit_message_text(
        "Напишите причину отказа ответным сообщением (или нажмите кнопку ниже, чтобы отказаться без причины).",
        reply_markup=build_decline_reason_keyboard(request_id),
    )


async def decline_without_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, request_id = query.data.split("|", maxsplit=1)
    await complete_decline_flow(context, request_id, reason=None)


async def cancel_without_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, request_id = query.data.split("|", maxsplit=1)
    await complete_client_cancel_flow(
        context=context,
        user=query.from_user,
        request_id=request_id,
        reason=None,
    )


async def admin_cancel_without_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, request_id = query.data.split("|", maxsplit=1)
    await complete_admin_cancel_flow(context, request_id, reason=None)


async def menu_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    text = (message.text or "").strip()
    user = update.effective_user

    if is_instructor(user.id):
        # Handle trainer ID input (only for main admin)
        if context.user_data.get("awaiting_trainer_id"):
            if not is_main_admin(user.id):
                await message.reply_text(
                    "Только главный администратор может добавлять тренеров.",
                    reply_markup=build_main_keyboard(user.id),
                )
                context.user_data.pop("awaiting_trainer_id", None)
                return
            if text in MENU_BUTTONS:
                context.user_data.pop("awaiting_trainer_id", None)
            else:
                try:
                    trainer_id = int(text.strip())
                    # Check if already exists in config
                    if trainer_id in settings.instructor_chat_ids:
                        await message.reply_text(
                            "Этот тренер уже есть в основном списке (config.env).",
                            reply_markup=build_main_keyboard(user.id),
                        )
                        context.user_data.pop("awaiting_trainer_id", None)
                        return
                    # Check if already added dynamically
                    existing_trainers = schedule_manager.get_trainers()
                    if any(t["user_id"] == trainer_id for t in existing_trainers):
                        await message.reply_text(
                            "Этот тренер уже добавлен.",
                            reply_markup=build_main_keyboard(user.id),
                        )
                        context.user_data.pop("awaiting_trainer_id", None)
                        return
                    # Try to get user info from bot
                    try:
                        trainer_user = await context.bot.get_chat(trainer_id)
                        trainer_username = trainer_user.username or ""
                        trainer_full_name = trainer_user.full_name or f"User {trainer_id}"
                    except Exception:
                        # If can't get info, use defaults
                        trainer_username = ""
                        trainer_full_name = f"User {trainer_id}"
                    
                    # Store trainer info for confirmation
                    context.user_data["pending_trainer"] = {
                        "user_id": trainer_id,
                        "username": trainer_username,
                        "full_name": trainer_full_name,
                    }
                    context.user_data.pop("awaiting_trainer_id", None)
                    
                    # Show confirmation
                    confirm_text = (
                        "<b>⚠️ Подтверждение добавления тренера</b>\n\n"
                        f"Вы действительно хотите добавить этого пользователя как тренера?\n\n"
                        f"<b>Информация:</b>\n"
                        f"Имя: {trainer_full_name}\n"
                        f"Username: @{trainer_username or 'нет'}\n"
                        f"ID: {trainer_id}\n\n"
                        f"Тренер получит доступ ко всем функциям управления расписанием и заявками."
                    )
                    await message.reply_text(
                        confirm_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=build_confirm_add_trainer_by_id_keyboard(trainer_id),
                    )
                    return
                except ValueError:
                    await message.reply_text(
                        "Неверный формат. Отправьте числовой chat_id тренера.",
                        reply_markup=build_main_keyboard(user.id),
                    )
                    return
        
        pending_decline = context.chat_data.get("awaiting_reason")
        if pending_decline:
            if text in MENU_BUTTONS:
                context.chat_data.pop("awaiting_reason", None)
            else:
                await complete_decline_flow(context, pending_decline["request_id"], reason=text)
                await message.reply_text(
                    "Причина отправлена, клиент уведомлён.",
                    reply_markup=build_main_keyboard(user.id),
                )
                return
        pending_admin_cancel = context.chat_data.get("awaiting_admin_cancel")
        if pending_admin_cancel:
            if text in MENU_BUTTONS:
                context.chat_data.pop("awaiting_admin_cancel", None)
            else:
                await complete_admin_cancel_flow(context, pending_admin_cancel["request_id"], reason=text)
                await message.reply_text(
                    "Отмена тренировки выполнена.",
                    reply_markup=build_main_keyboard(user.id),
                )
                return

    pending_cancel = context.user_data.get("awaiting_cancel_reason")
    if pending_cancel:
        if text in MENU_BUTTONS:
            context.user_data.pop("awaiting_cancel_reason", None)
        else:
            await complete_client_cancel_flow(
                context=context,
                user=user,
                request_id=pending_cancel["request_id"],
                reason=text,
            )
            return

    if text == BUTTON_BOOK:
        await start_booking(update, context)
        return
    if text == BUTTON_MY:
        await cancel_command(update, context)
        return
    if is_instructor(user.id) and text == BUTTON_TRAINER:
        await show_instructor_bookings(update, context)
        return
    if is_instructor(user.id) and text == BUTTON_SCHEDULE:
        await schedule_manage_command(update, context)
        return
    if is_main_admin(user.id) and text == BUTTON_STATS:
        await show_stats_command(update, context)
        return

    await message.reply_text(
        "Используйте кнопки ниже, чтобы продолжить.",
        reply_markup=build_main_keyboard(user.id),
    )


async def complete_decline_flow(
    context: ContextTypes.DEFAULT_TYPE,
    request_id: str,
    reason: str | None,
) -> None:
    pending: Dict | None = context.chat_data.get("awaiting_reason")
    if not pending or pending["request_id"] != request_id:
        return
    try:
        request = store.get_request(request_id)
    except KeyError:
        context.chat_data.pop("awaiting_reason", None)
        return
    if request.status != "pending":
        context.chat_data.pop("awaiting_reason", None)
        return

    await finalize_request_decision(
        request=request,
        new_status="declined",
        context=context,
        edit_target=(pending["message_chat_id"], pending["message_id"]),
        reason=reason,
    )
    context.chat_data.pop("awaiting_reason", None)


async def complete_client_cancel_flow(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    user,
    request_id: str,
    reason: str | None,
) -> None:
    pending = context.user_data.get("awaiting_cancel_reason")
    if not pending or pending["request_id"] != request_id:
        return
    try:
        request = store.get_request(request_id)
    except KeyError:
        await context.bot.send_message(chat_id=user.id, text="Запись не найдена или уже удалена.")
        context.user_data.pop("awaiting_cancel_reason", None)
        return
    if request.user_id != user.id:
        await context.bot.send_message(chat_id=user.id, text="Нельзя отменить чужую запись.")
        context.user_data.pop("awaiting_cancel_reason", None)
        return
    if request.status not in CLIENT_CANCELABLE_STATUSES:
        await context.bot.send_message(
            chat_id=user.id,
            text="Эту запись больше нельзя отменить. При необходимости выберите другую.",
        )
        context.user_data.pop("awaiting_cancel_reason", None)
        return

    await finalize_client_cancel(
        request=request,
        context=context,
        edit_target=(pending["message_chat_id"], pending["message_id"]),
        reason=reason,
    )
    context.user_data.pop("awaiting_cancel_reason", None)


async def complete_admin_cancel_flow(
    context: ContextTypes.DEFAULT_TYPE,
    request_id: str,
    reason: str | None,
) -> None:
    pending = context.chat_data.get("awaiting_admin_cancel")
    if not pending or pending["request_id"] != request_id:
        return
    try:
        request = store.get_request(request_id)
    except KeyError:
        context.chat_data.pop("awaiting_admin_cancel", None)
        return
    if request.status not in TRAINER_CANCELABLE_STATUSES:
        await context.bot.edit_message_text(
            chat_id=pending["message_chat_id"],
            message_id=pending["message_id"],
            text="Эту запись уже нельзя отменить.",
        )
        context.chat_data.pop("awaiting_admin_cancel", None)
        return

    await finalize_trainer_cancel(
        request=request,
        context=context,
        edit_target=(pending["message_chat_id"], pending["message_id"]),
        reason=reason,
    )
    context.chat_data.pop("awaiting_admin_cancel", None)


async def finalize_request_decision(
    *,
    request,
    new_status: str,
    context: ContextTypes.DEFAULT_TYPE,
    edit_target: Tuple[int, int],
    reason: str | None = None,
) -> None:
    updated = store.update_status(request.id, new_status)
    status_text = "подтверждена ✅" if new_status == "confirmed" else "отклонена ❌"
    trainer_text = (
        f"Заявка {humanize_slot(updated.date, updated.time)} для {updated.full_name} {status_text}."
    )
    if reason and new_status == "declined":
        trainer_text += f"\nПричина: {reason}"
    await context.bot.edit_message_text(
        chat_id=edit_target[0],
        message_id=edit_target[1],
        text=trainer_text,
    )

    if new_status == "confirmed":
        user_message = (
            f"Тренер подтвердил вашу запись на {humanize_slot(updated.date, updated.time)}."
        )
    else:
        base = f"К сожалению, тренер отклонил запись на {humanize_slot(updated.date, updated.time)}."
        if reason:
            base += f"\nПричина: {reason}"
        base += "\nПопробуйте выбрать другое время с помощью /book."
        user_message = base
    await context.bot.send_message(chat_id=updated.user_id, text=user_message)


async def finalize_client_cancel(
    *,
    request,
    context: ContextTypes.DEFAULT_TYPE,
    edit_target: Tuple[int, int],
    reason: str | None,
) -> None:
    previous_status = request.status
    updated = store.update_status(request.id, "cancelled_by_client")
    await context.bot.edit_message_text(
        chat_id=edit_target[0],
        message_id=edit_target[1],
        text=f"Запись на {humanize_slot(updated.date, updated.time)} отменена.",
    )

    user_text = f"Вы отменили запись на {humanize_slot(updated.date, updated.time)}."
    if reason:
        user_text += f"\nПричина: {reason}"
    await context.bot.send_message(
        chat_id=updated.user_id,
        text=user_text,
        reply_markup=build_main_keyboard(updated.user_id),
    )

    admin_text = (
        "<b>Отмена клиентом</b>\n"
        f"Дата: {humanize_slot(updated.date, updated.time)}\n"
        f"Клиент: {updated.full_name} (@{updated.username or 'нет'} | id {updated.user_id})\n"
        f"Статус до отмены: {STATUS_BADGES.get(previous_status, previous_status)}"
    )
    if reason:
        admin_text += f"\nПричина клиента: {reason}"
    await send_to_all_instructors(
        context,
        admin_text,
        parse_mode=ParseMode.HTML,
    )


async def finalize_trainer_cancel(
    *,
    request,
    context: ContextTypes.DEFAULT_TYPE,
    edit_target: Tuple[int, int],
    reason: str | None,
) -> None:
    previous_status = request.status
    updated = store.update_status(request.id, "cancelled_by_trainer")
    text = (
        f"Тренировка {humanize_slot(updated.date, updated.time)} с {updated.full_name} отменена тренером."
    )
    if reason:
        text += f"\nПричина: {reason}"
    await context.bot.edit_message_text(
        chat_id=edit_target[0],
        message_id=edit_target[1],
        text=text,
    )

    client_text = (
        "Тренер отменил вашу индивидуальную тренировку.\n"
        f"Время: {humanize_slot(updated.date, updated.time)}."
    )
    if reason:
        client_text += f"\nПричина: {reason}"
    client_text += "\nВы можете выбрать другое время с помощью кнопки «📅 Записаться»."
    await context.bot.send_message(
        chat_id=updated.user_id,
        text=client_text,
        reply_markup=build_main_keyboard(updated.user_id),
    )

    admin_text = (
        "<b>Отмена тренером</b>\n"
        f"Дата: {humanize_slot(updated.date, updated.time)}\n"
        f"Клиент: {updated.full_name} (@{updated.username or 'нет'} | id {updated.user_id})\n"
        f"Статус до отмены: {STATUS_BADGES.get(previous_status, previous_status)}"
    )
    if reason:
        admin_text += f"\nПричина: {reason}"
    await send_to_all_instructors(
        context,
        admin_text,
        parse_mode=ParseMode.HTML,
    )


async def ignore_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def schedule_manage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show schedule management menu for trainer."""
    user = update.effective_user
    if not is_instructor(user.id):
        await update.message.reply_text(
            "Эта функция доступна только тренеру.",
            reply_markup=build_main_keyboard(user.id),
        )
        return
    await update.message.reply_text(
        "⚙️ <b>Управление расписанием</b>\n\n"
        "Выберите, что хотите настроить:",
        parse_mode=ParseMode.HTML,
        reply_markup=build_schedule_manage_keyboard(user.id, is_main_admin(user.id)),
    )


async def schedule_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return to schedule management menu."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        "⚙️ <b>Управление расписанием</b>\n\n"
        "Выберите, что хотите настроить:",
        parse_mode=ParseMode.HTML,
        reply_markup=build_schedule_manage_keyboard(query.from_user.id, is_main_admin(query.from_user.id)),
    )


async def schedule_days_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show days management interface."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    schedule_manager.cleanup_past_blocks(datetime.now(timezone))
    dates, _ = collect_available_dates_with_labels()
    # Also include fully blocked days for unblocking
    today = datetime.now(timezone).date()
    blocked_dates = schedule_manager.get_all_blocked_dates()
    workdays = get_active_workdays()
    for offset in range(settings.booking_horizon_days):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() in workdays:
            date_str = candidate.isoformat()
            if date_str in blocked_dates:
                # Check if it's already in dates
                if not any(d[0] == candidate for d in dates):
                    dates.append((candidate, format_day_short(candidate)))
    dates.sort(key=lambda x: x[0])
    keyboard = build_schedule_days_keyboard(dates, page=0, page_size=DATES_PAGE_SIZE)
    await query.edit_message_text(
        "📅 <b>Управление днями</b>\n\n"
        "Выберите день для настройки слотов:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_days_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Navigate schedule days pages."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, page_str = query.data.split("|", maxsplit=1)
    page = int(page_str)
    schedule_manager.cleanup_past_blocks(datetime.now(timezone))
    dates, _ = collect_available_dates_with_labels()
    today = datetime.now(timezone).date()
    blocked_dates = schedule_manager.get_all_blocked_dates()
    workdays = get_active_workdays()
    for offset in range(settings.booking_horizon_days):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() in workdays:
            date_str = candidate.isoformat()
            if date_str in blocked_dates:
                if not any(d[0] == candidate for d in dates):
                    dates.append((candidate, format_day_short(candidate)))
    dates.sort(key=lambda x: x[0])
    keyboard = build_schedule_days_keyboard(dates, page=page, page_size=DATES_PAGE_SIZE)
    await query.edit_message_text(
        "📅 <b>Управление днями</b>\n\n"
        "Выберите день для настройки слотов:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show time slots for a specific day."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    refresh_daily_slots()  # Refresh in case settings changed
    _, date_str = query.data.split("|", maxsplit=1)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    base_slots = get_slots_for_date(target_date)
    day_label = format_day_full(target_date)
    blocked_times = schedule_manager.get_blocked_slots_for_day(date_str)
    keyboard = build_schedule_day_times_keyboard(date_str, base_slots, blocked_times)
    await query.edit_message_text(
        f"📅 <b>Управление слотами</b>\n\n"
        f"День: {day_label}\n"
        f"🔒 — закрыт, 🔓 — открыт\n\n"
        f"Нажмите на слот, чтобы изменить его статус:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle slot block/unblock."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, date_str, time_str, action = query.data.split("|", maxsplit=3)
    if action == "block":
        schedule_manager.block_slot(date_str, time_str)
        await query.answer("Слот закрыт ✅", show_alert=True)
    else:
        schedule_manager.unblock_slot(date_str, time_str)
        await query.answer("Слот открыт ✅", show_alert=True)
    # Refresh the day view
    refresh_daily_slots()  # Refresh in case settings changed
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    base_slots = get_slots_for_date(target_date)
    day_label = format_day_full(target_date)
    blocked_times = schedule_manager.get_blocked_slots_for_day(date_str)
    keyboard = build_schedule_day_times_keyboard(date_str, base_slots, blocked_times)
    await query.edit_message_text(
        f"📅 <b>Управление слотами</b>\n\n"
        f"День: {day_label}\n"
        f"🔒 — закрыт, 🔓 — открыт\n\n"
        f"Нажмите на слот, чтобы изменить его статус:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_day_block_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block entire day."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, date_str = query.data.split("|", maxsplit=1)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    base_slots = get_slots_for_date(target_date)
    schedule_manager.block_day(date_str, base_slots)
    await query.answer("Весь день закрыт ✅", show_alert=True)
    day_label = format_day_full(target_date)
    blocked_times = schedule_manager.get_blocked_slots_for_day(date_str)
    keyboard = build_schedule_day_times_keyboard(date_str, base_slots, blocked_times)
    await query.edit_message_text(
        f"📅 <b>Управление слотами</b>\n\n"
        f"День: {day_label}\n"
        f"🔒 — закрыт, 🔓 — открыт\n\n"
        f"Нажмите на слот, чтобы изменить его статус:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_day_unblock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unblock entire day."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, date_str = query.data.split("|", maxsplit=1)
    schedule_manager.unblock_day(date_str)
    await query.answer("Весь день открыт ✅", show_alert=True)
    target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    base_slots = get_slots_for_date(target_date)
    day_label = format_day_full(target_date)
    blocked_times = schedule_manager.get_blocked_slots_for_day(date_str)
    keyboard = build_schedule_day_times_keyboard(date_str, base_slots, blocked_times)
    await query.edit_message_text(
        f"📅 <b>Управление слотами</b>\n\n"
        f"День: {day_label}\n"
        f"🔒 — закрыт, 🔓 — открыт\n\n"
        f"Нажмите на слот, чтобы изменить его статус:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_weeks_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show weeks management interface."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    schedule_manager.cleanup_past_blocks(datetime.now(timezone))
    today = datetime.now(timezone).date()
    weeks: List[Tuple[date, str]] = []
    for offset in range(0, settings.booking_horizon_days, 7):
        week_start = today + timedelta(days=offset - today.weekday())
        week_end = week_start + timedelta(days=6)
        label = f"{format_day_short(week_start)} - {format_day_short(week_end)}"
        weeks.append((week_start, label))
    keyboard = build_schedule_weeks_keyboard(weeks, page=0, page_size=5)
    await query.edit_message_text(
        "📆 <b>Управление неделями</b>\n\n"
        "Выберите неделю для массового закрытия/открытия:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_weeks_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Navigate schedule weeks pages."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, page_str = query.data.split("|", maxsplit=1)
    page = int(page_str)
    schedule_manager.cleanup_past_blocks(datetime.now(timezone))
    today = datetime.now(timezone).date()
    weeks: List[Tuple[date, str]] = []
    for offset in range(0, settings.booking_horizon_days, 7):
        week_start = today + timedelta(days=offset - today.weekday())
        week_end = week_start + timedelta(days=6)
        label = f"{format_day_short(week_start)} - {format_day_short(week_end)}"
        weeks.append((week_start, label))
    keyboard = build_schedule_weeks_keyboard(weeks, page=page, page_size=5)
    await query.edit_message_text(
        "📆 <b>Управление неделями</b>\n\n"
        "Выберите неделю для массового закрытия/открытия:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_week_block_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block entire week."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, week_start_str = query.data.split("|", maxsplit=1)
    week_start = datetime.strptime(week_start_str, "%Y-%m-%d").date()
    # Block all days in the week with all time slots
    days_since_monday = week_start.weekday()
    monday = week_start - timedelta(days=days_since_monday)
    active_workdays = set(get_active_workdays())
    for day_offset in range(7):
        day = monday + timedelta(days=day_offset)
        if day.weekday() in active_workdays:
            slots = get_slots_for_weekday(day.weekday())
            if slots:
                schedule_manager.block_day(day.isoformat(), slots)
    await query.answer("Неделя закрыта ✅", show_alert=True)
    # Refresh weeks view
    today = datetime.now(timezone).date()
    weeks: List[Tuple[date, str]] = []
    for offset in range(0, settings.booking_horizon_days, 7):
        ws = today + timedelta(days=offset - today.weekday())
        we = ws + timedelta(days=6)
        label = f"{format_day_short(ws)} - {format_day_short(we)}"
        weeks.append((ws, label))
    keyboard = build_schedule_weeks_keyboard(weeks, page=0, page_size=5)
    await query.edit_message_text(
        "📆 <b>Управление неделями</b>\n\n"
        "Выберите неделю для массового закрытия/открытия:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_week_unblock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unblock entire week."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, week_start_str = query.data.split("|", maxsplit=1)
    week_start = datetime.strptime(week_start_str, "%Y-%m-%d").date()
    schedule_manager.unblock_week(week_start)
    await query.answer("Неделя открыта ✅", show_alert=True)
    # Refresh weeks view
    today = datetime.now(timezone).date()
    weeks: List[Tuple[date, str]] = []
    for offset in range(0, settings.booking_horizon_days, 7):
        ws = today + timedelta(days=offset - today.weekday())
        we = ws + timedelta(days=6)
        label = f"{format_day_short(ws)} - {format_day_short(we)}"
        weeks.append((ws, label))
    keyboard = build_schedule_weeks_keyboard(weeks, page=0, page_size=5)
    await query.edit_message_text(
        "📆 <b>Управление неделями</b>\n\n"
        "Выберите неделю для массового закрытия/открытия:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def schedule_work_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show work settings menu."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    has_custom = schedule_manager.get_work_settings() is not None
    workdays, start_hour, end_hour = get_effective_work_settings()
    manual_schedule = get_manual_schedule()
    manual_active = bool(manual_schedule)
    status_parts = []
    status_parts.append("Используются настройки по умолчанию" if not has_custom else "Используются пользовательские настройки")
    if manual_active:
        status_parts.append("Ручное расписание активно — клиенты видят только выбранные вручную слоты.")
    status_text = "\n".join(status_parts)
    manual_lines = []
    if manual_active:
        for day in sorted(manual_schedule.keys()):
            times = ", ".join(manual_schedule[day])
            manual_lines.append(f"{WEEKDAYS_RU_SHORT[day]}: {times}")
    manual_block = "\n".join(manual_lines) if manual_lines else "нет выбранных слотов"
    await query.edit_message_text(
        f"⚙️ <b>Настройка рабочего времени</b>\n\n"
        f"Текущие настройки:\n"
        f"Дни: {', '.join([WEEKDAYS_RU_SHORT[d] for d in sorted(workdays)])}\n"
        f"Часы: {start_hour:02d}:00 - {end_hour:02d}:00\n\n"
        f"{status_text}"
        + ("\n\nРучные слоты:\n" + manual_block if manual_active else ""),
        parse_mode=ParseMode.HTML,
        reply_markup=build_work_settings_keyboard(has_custom, manual_active),
    )


async def work_settings_days_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show work days selection."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    workdays, _, _ = get_effective_work_settings()
    # Store current selection in user_data
    context.user_data["work_days_temp"] = workdays.copy()
    keyboard = build_work_days_keyboard(workdays)
    await query.edit_message_text(
        "📅 <b>Настройка дней недели</b>\n\n"
        "Выберите рабочие дни (нажмите для переключения):",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def work_day_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle a work day."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, day_str = query.data.split("|", maxsplit=1)
    day_num = int(day_str)
    if "work_days_temp" not in context.user_data:
        workdays, _, _ = get_effective_work_settings()
        context.user_data["work_days_temp"] = workdays.copy()
    selected = context.user_data["work_days_temp"]
    if day_num in selected:
        selected.remove(day_num)
    else:
        selected.append(day_num)
    selected.sort()
    keyboard = build_work_days_keyboard(selected)
    await query.edit_message_text(
        "📅 <b>Настройка дней недели</b>\n\n"
        "Выберите рабочие дни (нажмите для переключения):",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def work_days_apply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apply work days settings."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    if "work_days_temp" not in context.user_data:
        await query.answer("Ошибка: настройки не найдены", show_alert=True)
        return
    selected_days = context.user_data["work_days_temp"]
    if not selected_days:
        await query.answer("Выберите хотя бы один рабочий день", show_alert=True)
        return
    _, start_hour, end_hour = get_effective_work_settings()
    schedule_manager.set_work_settings(selected_days, start_hour, end_hour)
    refresh_daily_slots()
    context.user_data.pop("work_days_temp", None)
    await query.answer("Дни недели обновлены ✅", show_alert=True)
    # Return to settings menu
    has_custom = True
    manual_active = has_manual_schedule()
    workdays = selected_days
    status_text = "Используются пользовательские настройки"
    await query.edit_message_text(
        f"⚙️ <b>Настройка рабочего времени</b>\n\n"
        f"Текущие настройки:\n"
        f"Дни: {', '.join([WEEKDAYS_RU_SHORT[d] for d in sorted(workdays)])}\n"
        f"Часы: {start_hour:02d}:00 - {end_hour:02d}:00\n\n"
        f"{status_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_work_settings_keyboard(has_custom, manual_active),
    )


async def work_settings_hours_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show work hours selection."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, start_hour, end_hour = get_effective_work_settings()
    # Store in user_data
    context.user_data["work_start_temp"] = start_hour
    context.user_data["work_end_temp"] = end_hour
    keyboard = build_work_hours_keyboard(start_hour, end_hour)
    await query.edit_message_text(
        "🕐 <b>Настройка часов работы</b>\n\n"
        "Выберите время начала и окончания работы:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def work_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set work start hour."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, hour_str = query.data.split("|", maxsplit=1)
    start_hour = int(hour_str)
    if "work_end_temp" not in context.user_data:
        _, _, end_hour = get_effective_work_settings()
        context.user_data["work_end_temp"] = end_hour
    end_hour = context.user_data["work_end_temp"]
    if start_hour >= end_hour:
        await query.answer("Время начала должно быть раньше времени окончания", show_alert=True)
        return
    context.user_data["work_start_temp"] = start_hour
    keyboard = build_work_hours_keyboard(start_hour, end_hour)
    await query.edit_message_text(
        "🕐 <b>Настройка часов работы</b>\n\n"
        "Выберите время начала и окончания работы:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def work_end_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set work end hour."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, hour_str = query.data.split("|", maxsplit=1)
    end_hour = int(hour_str)
    if "work_start_temp" not in context.user_data:
        _, start_hour, _ = get_effective_work_settings()
        context.user_data["work_start_temp"] = start_hour
    start_hour = context.user_data["work_start_temp"]
    if end_hour <= start_hour:
        await query.answer("Время окончания должно быть позже времени начала", show_alert=True)
        return
    context.user_data["work_end_temp"] = end_hour
    keyboard = build_work_hours_keyboard(start_hour, end_hour)
    await query.edit_message_text(
        "🕐 <b>Настройка часов работы</b>\n\n"
        "Выберите время начала и окончания работы:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def work_hours_apply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apply work hours settings."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    if "work_start_temp" not in context.user_data or "work_end_temp" not in context.user_data:
        await query.answer("Ошибка: настройки не найдены", show_alert=True)
        return
    start_hour = context.user_data["work_start_temp"]
    end_hour = context.user_data["work_end_temp"]
    if end_hour <= start_hour:
        await query.answer("Время окончания должно быть позже времени начала", show_alert=True)
        return
    workdays, _, _ = get_effective_work_settings()
    schedule_manager.set_work_settings(workdays, start_hour, end_hour)
    refresh_daily_slots()
    context.user_data.pop("work_start_temp", None)
    context.user_data.pop("work_end_temp", None)
    await query.answer("Часы работы обновлены ✅", show_alert=True)
    # Return to settings menu
    has_custom = True
    manual_active = has_manual_schedule()
    status_text = "Используются пользовательские настройки"
    await query.edit_message_text(
        f"⚙️ <b>Настройка рабочего времени</b>\n\n"
        f"Текущие настройки:\n"
        f"Дни: {', '.join([WEEKDAYS_RU_SHORT[d] for d in sorted(workdays)])}\n"
        f"Часы: {start_hour:02d}:00 - {end_hour:02d}:00\n\n"
        f"{status_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_work_settings_keyboard(has_custom, manual_active),
    )


async def work_settings_reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset work settings to defaults."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    schedule_manager.clear_work_settings()
    schedule_manager.clear_manual_slots()
    refresh_daily_slots()
    await query.answer("Настройки сброшены к значениям по умолчанию ✅", show_alert=True)
    workdays, start_hour, end_hour = get_effective_work_settings()
    has_custom = False
    manual_active = has_manual_schedule()
    status_text = "Используются настройки по умолчанию"
    await query.edit_message_text(
        f"⚙️ <b>Настройка рабочего времени</b>\n\n"
        f"Текущие настройки:\n"
        f"Дни: {', '.join([WEEKDAYS_RU_SHORT[d] for d in sorted(workdays)])}\n"
        f"Часы: {start_hour:02d}:00 - {end_hour:02d}:00\n\n"
        f"{status_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_work_settings_keyboard(has_custom, manual_active),
    )


async def manual_slots_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show manual schedule overview."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    manual_schedule = get_manual_schedule()
    text_lines = [
        "🧩 <b>Ручное расписание</b>\n",
        "Добавьте конкретные часы для каждого дня недели.",
        "Клиенты будут видеть только эти слоты (закрытые дни/часы имеют приоритет).",
        "",
        "Выберите день, чтобы отметить часы:",
    ]
    if not manual_schedule:
        text_lines.insert(1, "Пока ничего не выбрано — используются стандартные дни/часы.")
    keyboard = build_manual_schedule_keyboard(manual_schedule)
    await query.edit_message_text(
        "\n".join(text_lines),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def manual_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show manual slot toggles for a weekday."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, weekday_str = query.data.split("|", maxsplit=1)
    weekday = int(weekday_str)
    manual_schedule = get_manual_schedule()
    selected = manual_schedule.get(weekday, [])
    day_label = WEEKDAYS_RU_FULL[weekday]
    keyboard = build_manual_day_times_keyboard(weekday, MANUAL_TIME_OPTIONS, selected)
    await query.edit_message_text(
        f"🧩 <b>{day_label.title()}</b>\n\n"
        "Нажмите на час, чтобы добавить или убрать слот. Можно выбрать несколько часов.\n"
        "Диапазон фиксирован: 00:00–23:00, шаг 1 час.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def manual_slot_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle specific manual slot for weekday."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, weekday_str, time_str = query.data.split("|", maxsplit=2)
    weekday = int(weekday_str)
    if time_str not in MANUAL_TIME_OPTIONS:
        await query.answer("Недопустимое время", show_alert=True)
        return
    manual_schedule = get_manual_schedule()
    current = set(manual_schedule.get(weekday, []))
    if time_str in current:
        current.remove(time_str)
        feedback = "Слот удалён ✅"
    else:
        current.add(time_str)
        feedback = "Слот добавлен ✅"
    new_values = sorted(current)
    schedule_manager.set_manual_slots_for_weekday(weekday, new_values)
    refresh_daily_slots()
    await query.answer(feedback, show_alert=True)
    keyboard = build_manual_day_times_keyboard(weekday, MANUAL_TIME_OPTIONS, new_values)
    day_label = WEEKDAYS_RU_FULL[weekday]
    await query.edit_message_text(
        f"🧩 <b>{day_label.title()}</b>\n\n"
        "Нажмите на час, чтобы добавить или убрать слот. Можно выбрать несколько часов.\n"
        "Диапазон фиксирован: 00:00–23:00, шаг 1 час.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def manual_day_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear manual slots for a single weekday."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, weekday_str = query.data.split("|", maxsplit=1)
    weekday = int(weekday_str)
    schedule_manager.set_manual_slots_for_weekday(weekday, [])
    refresh_daily_slots()
    await query.answer("Слоты дня очищены ✅", show_alert=True)
    manual_schedule = get_manual_schedule()
    keyboard = build_manual_schedule_keyboard(manual_schedule)
    await query.edit_message_text(
        "🧩 <b>Ручное расписание</b>\n\nВыберите день, чтобы отметить часы:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def manual_clear_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove all manual slots."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    schedule_manager.clear_manual_slots()
    refresh_daily_slots()
    await query.answer("Ручное расписание очищено ✅", show_alert=True)
    manual_schedule = get_manual_schedule()
    keyboard = build_manual_schedule_keyboard(manual_schedule)
    await query.edit_message_text(
        "🧩 <b>Ручное расписание</b>\n\n"
        "Пока ничего не выбрано — используются стандартные дни/часы.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def regular_clients_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show regular clients management."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    clients = schedule_manager.get_regular_clients()
    keyboard = build_regular_clients_keyboard(clients)
    await query.edit_message_text(
        "👥 <b>Постоянные клиенты</b>\n\n"
        "Список постоянных клиентов. Вы можете назначить фиксированный слот (день недели + время) для каждого клиента.\n"
        "Клиент будет автоматически занимать этот слот каждую неделю.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def add_regular_client_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show list of clients to add as regular."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    # Get all unique clients from confirmed requests
    all_clients = store.get_all_unique_clients()
    # Filter out already regular clients
    regular_client_ids = {c["user_id"] for c in schedule_manager.get_regular_clients()}
    available_clients = [c for c in all_clients if c["user_id"] not in regular_client_ids]
    keyboard = build_add_regular_client_keyboard(available_clients)
    await query.edit_message_text(
        "➕ <b>Добавить постоянного клиента</b>\n\n"
        "Выберите клиента из списка подтвержденных заявок:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def add_client_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add client to regular clients list."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, user_id_str = query.data.split("|", maxsplit=1)
    user_id = int(user_id_str)
    # Find client info
    all_clients = store.get_all_unique_clients()
    client = next((c for c in all_clients if c["user_id"] == user_id), None)
    if client:
        schedule_manager.add_regular_client(
            user_id=user_id,
            username=client.get("username", ""),
            full_name=client.get("full_name", ""),
        )
        await query.answer("Клиент добавлен в список постоянных ✅", show_alert=True)
    else:
        await query.answer("Клиент не найден", show_alert=True)
    # Refresh the list
    clients = schedule_manager.get_regular_clients()
    keyboard = build_regular_clients_keyboard(clients)
    await query.edit_message_text(
        "👥 <b>Постоянные клиенты</b>\n\n"
        "Список постоянных клиентов. Вы можете удалить клиента или добавить нового из списка подтвержденных клиентов.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def regular_client_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show regular client slot management."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, user_id_str = query.data.split("|", maxsplit=1)
    user_id = int(user_id_str)
    # Find client info
    clients = schedule_manager.get_regular_clients()
    client = next((c for c in clients if c["user_id"] == user_id), None)
    if not client:
        await query.answer("Клиент не найден", show_alert=True)
        return
    weekday = client.get("weekday")
    time_str = client.get("time")
    keyboard = build_regular_client_slot_keyboard(user_id, weekday, time_str)
    client_name = client.get("full_name", "Unknown")
    await query.edit_message_text(
        f"📅 <b>Слот постоянного клиента</b>\n\n"
        f"Клиент: {client_name}\n\n"
        f"Назначьте фиксированный день недели и время для этого клиента. "
        f"Он будет автоматически занимать этот слот каждую неделю.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def regular_client_set_weekday_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show weekday selection for regular client slot."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, user_id_str = query.data.split("|", maxsplit=1)
    user_id = int(user_id_str)
    keyboard = build_regular_client_weekday_keyboard(user_id)
    await query.edit_message_text(
        "📅 <b>Назначить слот постоянному клиенту</b>\n\n"
        "Выберите день недели:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def regular_client_weekday_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show time selection for regular client slot."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, user_id_str, weekday_str = query.data.split("|", maxsplit=2)
    user_id = int(user_id_str)
    weekday = int(weekday_str)
    refresh_daily_slots()
    available_times = get_slots_for_weekday(weekday)
    if not available_times:
        await query.answer("Для этого дня нет свободных слотов. Добавьте их в ручном расписании.", show_alert=True)
        return
    keyboard = build_regular_client_time_keyboard(user_id, weekday, available_times)
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    await query.edit_message_text(
        f"📅 <b>Назначить слот постоянному клиенту</b>\n\n"
        f"День: {weekdays[weekday]}\n"
        f"Выберите время:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def regular_client_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set slot for regular client."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, user_id_str, weekday_str, time_str = query.data.split("|", maxsplit=3)
    user_id = int(user_id_str)
    weekday = int(weekday_str)
    schedule_manager.set_regular_client_slot(user_id, weekday, time_str)
    await query.answer("Слот назначен ✅", show_alert=True)
    # Create bookings for upcoming weeks
    await create_regular_client_bookings(user_id, weekday, time_str, context)
    # Refresh the slot view
    clients = schedule_manager.get_regular_clients()
    client = next((c for c in clients if c["user_id"] == user_id), None)
    if client:
        weekday = client.get("weekday")
        time_str = client.get("time")
        keyboard = build_regular_client_slot_keyboard(user_id, weekday, time_str)
        client_name = client.get("full_name", "Unknown")
        await query.edit_message_text(
            f"📅 <b>Слот постоянного клиента</b>\n\n"
            f"Клиент: {client_name}\n\n"
            f"Назначьте фиксированный день недели и время для этого клиента. "
            f"Он будет автоматически занимать этот слот каждую неделю.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


async def regular_client_clear_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear slot for regular client."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, user_id_str = query.data.split("|", maxsplit=1)
    user_id = int(user_id_str)
    schedule_manager.clear_regular_client_slot(user_id)
    await query.answer("Слот удален ✅", show_alert=True)
    # Refresh the slot view
    clients = schedule_manager.get_regular_clients()
    client = next((c for c in clients if c["user_id"] == user_id), None)
    if client:
        weekday = client.get("weekday")
        time_str = client.get("time")
        keyboard = build_regular_client_slot_keyboard(user_id, weekday, time_str)
        client_name = client.get("full_name", "Unknown")
        await query.edit_message_text(
            f"📅 <b>Слот постоянного клиента</b>\n\n"
            f"Клиент: {client_name}\n\n"
            f"Назначьте фиксированный день недели и время для этого клиента. "
            f"Он будет автоматически занимать этот слот каждую неделю.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


async def create_regular_client_bookings(
    user_id: int,
    weekday: int,
    time_str: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Create confirmed bookings for regular client for upcoming weeks."""
    clients = schedule_manager.get_regular_clients()
    client = next((c for c in clients if c["user_id"] == user_id), None)
    if not client:
        return
    
    today = datetime.now(timezone).date()
    created_count = 0
    
    # Create bookings for the next 4 weeks
    for week_offset in range(4):
        # Find the next occurrence of this weekday
        days_ahead = weekday - today.weekday()
        if days_ahead < 0:
            days_ahead += 7
        elif days_ahead == 0:
            # Same day - check if time has passed
            current_time = datetime.now(timezone).time()
            slot_time = datetime.strptime(time_str, "%H:%M").time()
            if current_time >= slot_time:
                days_ahead += 7
        
        target_date = today + timedelta(days=days_ahead + (week_offset * 7))
        
        # Skip if date is too far in the future
        if (target_date - today).days > settings.booking_horizon_days:
            break
        
        date_str = target_date.isoformat()
        
        # Check if slot is blocked
        if schedule_manager.is_slot_blocked(date_str, time_str):
            continue
        
        # Check if booking already exists
        if store.get_slot_state(date_str, time_str):
            continue
        
        # Create confirmed booking
        try:
            request = store.create_request(
                user_id=user_id,
                username=client.get("username", ""),
                full_name=client.get("full_name", ""),
                date_str=date_str,
                time_str=time_str,
                is_group_slot=False,
            )
            # Auto-confirm for regular clients
            store.update_status(request.id, "confirmed")
            created_count += 1
        except ValueError:
            # Slot already booked, skip
            continue
    
    if created_count > 0:
        logger.info(f"Created {created_count} bookings for regular client {user_id} ({weekday}, {time_str})")


async def remove_regular_client_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove client from regular clients list."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, user_id_str = query.data.split("|", maxsplit=1)
    user_id = int(user_id_str)
    schedule_manager.remove_regular_client(user_id)
    await query.answer("Клиент удален из списка постоянных ✅", show_alert=True)
    # Refresh the list
    clients = schedule_manager.get_regular_clients()
    keyboard = build_regular_clients_keyboard(clients)
    await query.edit_message_text(
        "👥 <b>Постоянные клиенты</b>\n\n"
        "Список постоянных клиентов. Вы можете назначить фиксированный слот (день недели + время) для каждого клиента.\n"
        "Клиент будет автоматически занимать этот слот каждую неделю.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def group_slots_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show group slots management."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    refresh_daily_slots()
    group_slots = schedule_manager.get_group_slots()
    keyboard = build_group_slots_keyboard(group_slots, [])
    await query.edit_message_text(
        "👥 <b>Групповые слоты</b>\n\n"
        "Групповые слоты — это дни недели и время, когда можно записываться нескольким клиентам одновременно.\n"
        "Такие слоты помечаются эмодзи 👥 в календаре.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def add_group_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show weekday selection for new group slot."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    keyboard = build_add_group_slot_weekday_keyboard()
    await query.edit_message_text(
        "➕ <b>Добавить групповой слот</b>\n\n"
        "Выберите день недели:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def group_slot_weekday_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show time selection for group slot."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, weekday_str = query.data.split("|", maxsplit=1)
    weekday = int(weekday_str)
    refresh_daily_slots()
    available_times = get_slots_for_weekday(weekday)
    if not available_times:
        await query.answer("Для этого дня нет рабочих слотов. Добавьте их в ручном расписании.", show_alert=True)
        keyboard = build_add_group_slot_weekday_keyboard()
        await query.edit_message_text(
            "➕ <b>Добавить групповой слот</b>\n\n"
            "Сначала добавьте часы в ручном расписании или выберите другой день:",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        return
    keyboard = build_add_group_slot_time_keyboard(weekday, available_times)
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    await query.edit_message_text(
        f"➕ <b>Добавить групповой слот</b>\n\n"
        f"День: {weekdays[weekday]}\n"
        f"Выберите время:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def group_slot_time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add group slot."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, weekday_str, time_str = query.data.split("|", maxsplit=2)
    weekday = int(weekday_str)
    schedule_manager.add_group_slot(weekday, time_str)
    await query.answer("Групповой слот добавлен ✅", show_alert=True)
    # Refresh the list
    refresh_daily_slots()
    group_slots = schedule_manager.get_group_slots()
    keyboard = build_group_slots_keyboard(group_slots, [])
    await query.edit_message_text(
        "👥 <b>Групповые слоты</b>\n\n"
        "Групповые слоты — это дни недели и время, когда можно записываться нескольким клиентам одновременно.\n"
        "Такие слоты помечаются эмодзи 👥 в календаре.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def remove_group_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove group slot."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, weekday_str, time_str = query.data.split("|", maxsplit=2)
    weekday = int(weekday_str)
    schedule_manager.remove_group_slot(weekday, time_str)
    await query.answer("Групповой слот удален ✅", show_alert=True)
    # Refresh the list
    refresh_daily_slots()
    group_slots = schedule_manager.get_group_slots()
    keyboard = build_group_slots_keyboard(group_slots, [])
    await query.edit_message_text(
        "👥 <b>Групповые слоты</b>\n\n"
        "Групповые слоты — это дни недели и время, когда можно записываться нескольким клиентам одновременно.\n"
        "Такие слоты помечаются эмодзи 👥 в календаре.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def manage_trainers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show trainers management."""
    query = update.callback_query
    if not is_main_admin(query.from_user.id):
        await query.answer("Только главный администратор может управлять тренерами", show_alert=True)
        return
    await query.answer()
    trainers = schedule_manager.get_trainers()
    keyboard = build_trainers_keyboard(trainers)
    await query.edit_message_text(
        "👤 <b>Управление тренерами</b>\n\n"
        "Список динамически добавленных тренеров. Вы можете добавить нового тренера по его chat_id или удалить существующего.\n\n"
        "Чтобы добавить тренера, нажмите кнопку ниже и отправьте его chat_id (число).",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def add_trainer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start adding trainer flow."""
    query = update.callback_query
    if not is_main_admin(query.from_user.id):
        await query.answer("Только главный администратор может добавлять тренеров", show_alert=True)
        return
    await query.answer()
    context.user_data["awaiting_trainer_id"] = True
    await query.edit_message_text(
        "➕ <b>Добавить тренера</b>\n\n"
        "Отправьте chat_id тренера (число).\n"
        "Чтобы узнать chat_id, попросите тренера написать боту @userinfobot или используйте его Telegram ID.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Отмена", callback_data="MANAGE_TRAINERS")]
        ]),
    )


async def remove_trainer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove trainer."""
    query = update.callback_query
    if not is_main_admin(query.from_user.id):
        await query.answer("Только главный администратор может удалять тренеров", show_alert=True)
        return
    await query.answer()
    _, user_id_str = query.data.split("|", maxsplit=1)
    user_id = int(user_id_str)
    
    # Don't allow removing trainers from config
    if user_id in settings.instructor_chat_ids:
        await query.answer("Нельзя удалить тренера из основного списка (config.env)", show_alert=True)
        return
    
    schedule_manager.remove_trainer(user_id)
    await query.answer("Тренер удален ✅", show_alert=True)
    # Refresh the list
    trainers = schedule_manager.get_trainers()
    keyboard = build_trainers_keyboard(trainers)
    await query.edit_message_text(
        "👤 <b>Управление тренерами</b>\n\n"
        "Список динамически добавленных тренеров. Вы можете добавить нового тренера по его chat_id или удалить существующего.\n\n"
        "Чтобы добавить тренера, нажмите кнопку ниже и отправьте его chat_id (число).",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def add_trainer_from_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show confirmation for adding trainer from request card."""
    query = update.callback_query
    if not is_main_admin(query.from_user.id):
        await query.answer("Только главный администратор может добавлять тренеров", show_alert=True)
        return
    await query.answer()
    _, request_id, user_id_str = query.data.split("|", maxsplit=2)
    user_id = int(user_id_str)
    
    try:
        request = store.get_request(request_id)
    except KeyError:
        await query.answer("Заявка не найдена", show_alert=True)
        return
    
    # Check if already exists in config
    if user_id in settings.instructor_chat_ids:
        await query.answer("Этот тренер уже есть в основном списке (config.env)", show_alert=True)
        return
    
    # Check if already added dynamically
    existing_trainers = schedule_manager.get_trainers()
    if any(t["user_id"] == user_id for t in existing_trainers):
        await query.answer("Этот тренер уже добавлен", show_alert=True)
        return
    
    # Show confirmation
    confirm_text = (
        "<b>⚠️ Подтверждение добавления тренера</b>\n\n"
        f"Вы действительно хотите добавить этого пользователя как тренера?\n\n"
        f"<b>Информация:</b>\n"
        f"Имя: {request.full_name}\n"
        f"Username: @{request.username or 'нет'}\n"
        f"ID: {user_id}\n\n"
        f"Тренер получит доступ ко всем функциям управления расписанием и заявками."
    )
    await query.edit_message_text(
        confirm_text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_confirm_add_trainer_keyboard(request_id, user_id),
    )


async def confirm_add_trainer_from_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm and add trainer from request card."""
    query = update.callback_query
    if not is_main_admin(query.from_user.id):
        await query.answer("Только главный администратор может добавлять тренеров", show_alert=True)
        return
    await query.answer()
    _, request_id, user_id_str = query.data.split("|", maxsplit=2)
    user_id = int(user_id_str)
    
    try:
        request = store.get_request(request_id)
    except KeyError:
        await query.answer("Заявка не найдена", show_alert=True)
        return
    
    # Double-check if already exists
    if user_id in settings.instructor_chat_ids:
        await query.answer("Этот тренер уже есть в основном списке (config.env)", show_alert=True)
        return
    
    existing_trainers = schedule_manager.get_trainers()
    if any(t["user_id"] == user_id for t in existing_trainers):
        await query.answer("Этот тренер уже добавлен", show_alert=True)
        return
    
    # Add trainer using info from request
    schedule_manager.add_trainer(
        user_id=user_id,
        username=request.username,
        full_name=request.full_name,
    )
    await query.answer("Тренер добавлен ✅", show_alert=True)
    
    # Update the message to show updated keyboard (client is now a trainer, so button won't show)
    admin_text = (
        "<b>Новая заявка</b>\n"
        f"Дата: {humanize_slot(request.date, request.time)}\n"
        f"Клиент: {request.full_name} "
        f"(@{request.username or 'нет'} | id {request.user_id})\n\n"
        f"✅ <b>Добавлен как тренер</b>"
    )
    await query.edit_message_text(
        admin_text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_request_review_keyboard(request_id, user_id, True, is_client_trainer=True),
    )


async def cancel_add_trainer_from_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel adding trainer from request - return to request card."""
    query = update.callback_query
    if not is_main_admin(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    _, request_id = query.data.split("|", maxsplit=1)
    
    try:
        request = store.get_request(request_id)
    except KeyError:
        await query.answer("Заявка не найдена", show_alert=True)
        return
    
    # Return to request card
    admin_text = (
        "<b>Новая заявка</b>\n"
        f"Дата: {humanize_slot(request.date, request.time)}\n"
        f"Клиент: {request.full_name} "
        f"(@{request.username or 'нет'} | id {request.user_id})"
    )
    is_client_already_trainer = is_instructor(request.user_id)
    await query.edit_message_text(
        admin_text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_request_review_keyboard(request_id, request.user_id, True, is_client_already_trainer),
    )


async def confirm_add_trainer_by_id_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm and add trainer by ID."""
    query = update.callback_query
    if not is_main_admin(query.from_user.id):
        await query.answer("Только главный администратор может добавлять тренеров", show_alert=True)
        return
    await query.answer()
    _, user_id_str = query.data.split("|", maxsplit=1)
    user_id = int(user_id_str)
    
    # Get trainer info from context
    pending_trainer = context.user_data.get("pending_trainer")
    if not pending_trainer or pending_trainer["user_id"] != user_id:
        await query.answer("Ошибка: данные тренера не найдены", show_alert=True)
        return
    
    # Double-check if already exists
    if user_id in settings.instructor_chat_ids:
        await query.answer("Этот тренер уже есть в основном списке (config.env)", show_alert=True)
        context.user_data.pop("pending_trainer", None)
        return
    
    existing_trainers = schedule_manager.get_trainers()
    if any(t["user_id"] == user_id for t in existing_trainers):
        await query.answer("Этот тренер уже добавлен", show_alert=True)
        context.user_data.pop("pending_trainer", None)
        return
    
    # Add trainer
    schedule_manager.add_trainer(
        user_id=pending_trainer["user_id"],
        username=pending_trainer["username"],
        full_name=pending_trainer["full_name"],
    )
    await query.answer("Тренер добавлен ✅", show_alert=True)
    context.user_data.pop("pending_trainer", None)
    
    # Update message
    await query.edit_message_text(
        f"✅ <b>Тренер добавлен</b>\n\n"
        f"ID: {user_id}\n"
        f"Имя: {pending_trainer['full_name']}\n"
        f"Username: @{pending_trainer['username'] or 'нет'}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ К управлению тренерами", callback_data="MANAGE_TRAINERS")]
        ]),
    )


async def sync_regular_client_bookings(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sync bookings for regular clients - create bookings for upcoming weeks."""
    regular_slots = schedule_manager.get_all_regular_client_slots()
    today = datetime.now(timezone).date()
    
    for slot_info in regular_slots:
        user_id = slot_info["user_id"]
        weekday = slot_info["weekday"]
        time_str = slot_info["time"]
        
        # Find the next occurrence of this weekday
        days_ahead = weekday - today.weekday()
        if days_ahead < 0:
            days_ahead += 7
        elif days_ahead == 0:
            # Same day - check if time has passed
            current_time = datetime.now(timezone).time()
            slot_time = datetime.strptime(time_str, "%H:%M").time()
            if current_time >= slot_time:
                days_ahead += 7
        
        # Create bookings for the next 4 weeks
        for week_offset in range(4):
            target_date = today + timedelta(days=days_ahead + (week_offset * 7))
            
            # Skip if date is too far in the future
            if (target_date - today).days > settings.booking_horizon_days:
                break
            
            date_str = target_date.isoformat()
            
            # Check if slot is blocked
            if schedule_manager.is_slot_blocked(date_str, time_str):
                continue
            
            # Check if booking already exists
            if store.get_slot_state(date_str, time_str):
                continue
            
            # Create confirmed booking
            try:
                clients = schedule_manager.get_regular_clients()
                client = next((c for c in clients if c["user_id"] == user_id), None)
                if not client:
                    continue
                
                request = store.create_request(
                    user_id=user_id,
                    username=client.get("username", ""),
                    full_name=client.get("full_name", ""),
                    date_str=date_str,
                    time_str=time_str,
                    is_group_slot=False,
                )
                # Auto-confirm for regular clients
                store.update_status(request.id, "confirmed")
            except ValueError:
                # Slot already booked, skip
                continue


async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send reminders 2 hours before confirmed trainings."""
    now = datetime.now(timezone)
    upcoming = store.get_upcoming_confirmed(now)
    
    for request in upcoming:
        try:
            slot_dt = timezone.localize(
                datetime.strptime(f"{request.date} {request.time}", "%Y-%m-%d %H:%M")
            )
            # Double-check: reminder should be sent exactly 2 hours before
            reminder_time = slot_dt - timedelta(hours=2)
            if now < reminder_time or now >= slot_dt:
                continue
            
            slot_text = humanize_slot(request.date, request.time)
            
            # Send to client
            client_message = (
                f"⏰ Напоминание: через 2 часа у вас индивидуальная тренировка!\n"
                f"Время: {slot_text}"
            )
            try:
                await context.bot.send_message(
                    chat_id=request.user_id,
                    text=client_message,
                    reply_markup=build_main_keyboard(request.user_id),
                )
            except Exception as e:
                logger.warning(f"Failed to send reminder to client {request.user_id}: {e}")
            
            # Send to trainers
            trainer_message = (
                f"⏰ Напоминание: через 2 часа индивидуальная тренировка!\n"
                f"Время: {slot_text}\n"
                f"Клиент: {request.full_name} (@{request.username or 'нет'})"
            )
            await send_to_all_instructors(context, trainer_message)
            
            # Mark as sent
            store.mark_reminder_sent(request.id)
            logger.info(f"Reminder sent for request {request.id} ({slot_text})")
            
        except Exception as e:
            logger.error(f"Error processing reminder for request {request.id}: {e}")


def main() -> None:
    application = Application.builder().token(settings.bot_token).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("book", start_booking))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("stats", stats_command))

    application.add_handler(CallbackQueryHandler(date_callback, pattern=r"^DATE\|"))
    application.add_handler(CallbackQueryHandler(time_callback, pattern=r"^TIME\|"))
    application.add_handler(CallbackQueryHandler(back_to_dates, pattern=r"^BACK_TO_DATES"))
    application.add_handler(CallbackQueryHandler(dates_page_callback, pattern=r"^DATES\|"))
    application.add_handler(CallbackQueryHandler(cancel_select_callback, pattern=r"^CANCEL\|"))
    application.add_handler(CallbackQueryHandler(admin_cancel_select_callback, pattern=r"^ADMIN_CANCEL\|"))
    application.add_handler(CallbackQueryHandler(review_callback, pattern=r"^REVIEW\|"))
    application.add_handler(CallbackQueryHandler(decline_without_reason_callback, pattern=r"^DECLINE_SKIP\|"))
    application.add_handler(CallbackQueryHandler(cancel_without_reason_callback, pattern=r"^CANCEL_SKIP\|"))
    application.add_handler(CallbackQueryHandler(admin_cancel_without_reason_callback, pattern=r"^ADMIN_CANCEL_SKIP\|"))
    application.add_handler(CallbackQueryHandler(ignore_callback, pattern=r"^IGNORE$"))
    # Schedule management handlers
    application.add_handler(CallbackQueryHandler(schedule_menu_callback, pattern=r"^SCHEDULE_MENU$"))
    application.add_handler(CallbackQueryHandler(schedule_menu_callback, pattern=r"^SCHEDULE_BACK$"))
    application.add_handler(CallbackQueryHandler(schedule_days_callback, pattern=r"^SCHEDULE_DAYS$"))
    application.add_handler(CallbackQueryHandler(schedule_days_page_callback, pattern=r"^SCHEDULE_DAYS_PAGE\|"))
    application.add_handler(CallbackQueryHandler(schedule_day_callback, pattern=r"^SCHEDULE_DAY\|"))
    application.add_handler(CallbackQueryHandler(schedule_slot_callback, pattern=r"^SCHEDULE_SLOT\|"))
    application.add_handler(CallbackQueryHandler(schedule_day_block_callback, pattern=r"^SCHEDULE_DAY_BLOCK\|"))
    application.add_handler(CallbackQueryHandler(schedule_day_unblock_callback, pattern=r"^SCHEDULE_DAY_UNBLOCK\|"))
    application.add_handler(CallbackQueryHandler(schedule_weeks_callback, pattern=r"^SCHEDULE_WEEKS$"))
    application.add_handler(CallbackQueryHandler(schedule_weeks_page_callback, pattern=r"^SCHEDULE_WEEKS_PAGE\|"))
    application.add_handler(CallbackQueryHandler(schedule_week_block_callback, pattern=r"^SCHEDULE_WEEK_BLOCK\|"))
    application.add_handler(CallbackQueryHandler(schedule_week_unblock_callback, pattern=r"^SCHEDULE_WEEK_UNBLOCK\|"))
    # Work settings handlers
    application.add_handler(CallbackQueryHandler(schedule_work_settings_callback, pattern=r"^SCHEDULE_WORK_SETTINGS$"))
    application.add_handler(CallbackQueryHandler(work_settings_days_callback, pattern=r"^WORK_SETTINGS_DAYS$"))
    application.add_handler(CallbackQueryHandler(work_day_toggle_callback, pattern=r"^WORK_DAY_TOGGLE\|"))
    application.add_handler(CallbackQueryHandler(work_days_apply_callback, pattern=r"^WORK_DAYS_APPLY$"))
    application.add_handler(CallbackQueryHandler(work_settings_hours_callback, pattern=r"^WORK_SETTINGS_HOURS$"))
    application.add_handler(CallbackQueryHandler(work_start_callback, pattern=r"^WORK_START\|"))
    application.add_handler(CallbackQueryHandler(work_end_callback, pattern=r"^WORK_END\|"))
    application.add_handler(CallbackQueryHandler(work_hours_apply_callback, pattern=r"^WORK_HOURS_APPLY$"))
    application.add_handler(CallbackQueryHandler(work_settings_reset_callback, pattern=r"^WORK_SETTINGS_RESET$"))
    application.add_handler(CallbackQueryHandler(manual_slots_menu_callback, pattern=r"^MANUAL_SLOTS_MENU$"))
    application.add_handler(CallbackQueryHandler(manual_day_callback, pattern=r"^MANUAL_DAY\|"))
    application.add_handler(CallbackQueryHandler(manual_slot_toggle_callback, pattern=r"^MANUAL_SLOT\|"))
    application.add_handler(CallbackQueryHandler(manual_day_clear_callback, pattern=r"^MANUAL_DAY_CLEAR\|"))
    application.add_handler(CallbackQueryHandler(manual_clear_all_callback, pattern=r"^MANUAL_CLEAR$"))
    # Regular clients handlers
    application.add_handler(CallbackQueryHandler(regular_clients_callback, pattern=r"^REGULAR_CLIENTS$"))
    application.add_handler(CallbackQueryHandler(add_regular_client_callback, pattern=r"^ADD_REGULAR_CLIENT$"))
    application.add_handler(CallbackQueryHandler(add_client_confirm_callback, pattern=r"^ADD_CLIENT\|"))
    application.add_handler(CallbackQueryHandler(regular_client_slot_callback, pattern=r"^REGULAR_CLIENT_SLOT\|"))
    application.add_handler(CallbackQueryHandler(regular_client_set_weekday_callback, pattern=r"^REGULAR_CLIENT_SET_WEEKDAY\|"))
    application.add_handler(CallbackQueryHandler(regular_client_weekday_callback, pattern=r"^REGULAR_CLIENT_WEEKDAY\|"))
    application.add_handler(CallbackQueryHandler(regular_client_time_callback, pattern=r"^REGULAR_CLIENT_TIME\|"))
    application.add_handler(CallbackQueryHandler(regular_client_clear_slot_callback, pattern=r"^REGULAR_CLIENT_CLEAR_SLOT\|"))
    application.add_handler(CallbackQueryHandler(remove_regular_client_callback, pattern=r"^REMOVE_CLIENT\|"))
    # Group slots handlers
    application.add_handler(CallbackQueryHandler(group_slots_callback, pattern=r"^GROUP_SLOTS$"))
    application.add_handler(CallbackQueryHandler(add_group_slot_callback, pattern=r"^ADD_GROUP_SLOT$"))
    application.add_handler(CallbackQueryHandler(group_slot_weekday_callback, pattern=r"^GROUP_SLOT_WEEKDAY\|"))
    application.add_handler(CallbackQueryHandler(group_slot_time_callback, pattern=r"^GROUP_SLOT_TIME\|"))
    application.add_handler(CallbackQueryHandler(remove_group_slot_callback, pattern=r"^REMOVE_GROUP_SLOT\|"))
    # Trainers management handlers
    application.add_handler(CallbackQueryHandler(manage_trainers_callback, pattern=r"^MANAGE_TRAINERS$"))
    application.add_handler(CallbackQueryHandler(add_trainer_callback, pattern=r"^ADD_TRAINER$"))
    application.add_handler(CallbackQueryHandler(remove_trainer_callback, pattern=r"^REMOVE_TRAINER\|"))
    application.add_handler(CallbackQueryHandler(add_trainer_from_request_callback, pattern=r"^ADD_TR_REQ\|"))
    application.add_handler(CallbackQueryHandler(confirm_add_trainer_from_request_callback, pattern=r"^CFM_TR_REQ\|"))
    application.add_handler(CallbackQueryHandler(cancel_add_trainer_from_request_callback, pattern=r"^CNL_TR_REQ\|"))
    application.add_handler(CallbackQueryHandler(confirm_add_trainer_by_id_callback, pattern=r"^CFM_TR_ID\|"))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.TEXT
            & (~filters.COMMAND),
            menu_text_handler,
        )
    )

    # Schedule reminder job to run every 5 minutes
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(
            send_reminders,
            interval=300,  # 5 minutes
            first=10,  # Start after 10 seconds
        )
        logger.info("Reminder job scheduled (runs every 5 minutes)")
        
        # Schedule regular client bookings sync to run daily
        job_queue.run_daily(
            sync_regular_client_bookings,
            time=time(hour=0, minute=0),  # Run at midnight
        )
        logger.info("Regular client bookings sync scheduled (runs daily at midnight)")

    application.run_polling(drop_pending_updates=True)


def ensure_event_loop() -> None:
    """Create and set an event loop when Python 3.11+ doesn't do it automatically."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)


if __name__ == "__main__":
    ensure_event_loop()
    main()

