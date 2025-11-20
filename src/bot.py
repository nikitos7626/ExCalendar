from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Dict, Iterable, List, Tuple

import pytz
from telegram import ReplyKeyboardMarkup, Update
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
    build_cancel_reason_keyboard,
    build_cancel_select_keyboard,
    build_dates_keyboard,
    build_decline_reason_keyboard,
    build_request_review_keyboard,
    build_schedule_day_times_keyboard,
    build_schedule_days_keyboard,
    build_schedule_manage_keyboard,
    build_schedule_weeks_keyboard,
    build_times_keyboard,
    build_work_days_keyboard,
    build_work_hours_keyboard,
    build_work_settings_keyboard,
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
MENU_BUTTONS = {BUTTON_BOOK, BUTTON_MY, BUTTON_TRAINER, BUTTON_SCHEDULE}
DATES_PAGE_SIZE = 6


def is_instructor(user_id: int) -> bool:
    return user_id == settings.instructor_chat_id


def build_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    if is_instructor(user_id):
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


def collect_available_dates_with_labels() -> List[Tuple[date, str]]:
    cleanup_expired_requests()
    schedule_manager.cleanup_past_blocks(datetime.now(timezone))
    refresh_daily_slots()  # Refresh in case settings changed
    workdays, _, _ = get_effective_work_settings()
    today = datetime.now(timezone).date()
    result: List[Tuple[date, str]] = []
    blocked_dates = schedule_manager.get_all_blocked_dates()
    for offset in range(settings.booking_horizon_days):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() in workdays:
            # Skip fully blocked days (when all slots are blocked)
            date_str = candidate.isoformat()
            blocked_slots = schedule_manager.get_blocked_slots_for_day(date_str)
            # If day is not fully blocked, include it
            if len(blocked_slots) < len(daily_slots):
                result.append((candidate, format_day_short(candidate)))
    return result


def format_day_short(day: date) -> str:
    return f"{day.day:02d} {MONTHS_RU_SHORT[day.month - 1]} ({WEEKDAYS_RU_SHORT[day.weekday()]})"


def format_day_full(day: date) -> str:
    return f"{day.day:02d} {MONTHS_RU_GEN[day.month - 1]} ({WEEKDAYS_RU_FULL[day.weekday()]})"


def find_dates_and_page(date_str: str) -> Tuple[List[Tuple[date, str]], int, bool]:
    dates = collect_available_dates_with_labels()
    page = 0
    found = False
    for idx, (day, _) in enumerate(dates):
        if day.isoformat() == date_str:
            page = idx // DATES_PAGE_SIZE
            found = True
            break
    return dates, page, found


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
    await update.message.reply_text(
        "Привет! Я помогу записаться на индивидуальную тренировку.\n"
        "Нажми «📅 Записаться», чтобы выбрать дату и время (1 час).\n"
        "В разделе «🗓 Мои записи» можно увидеть активные заявки и отменить их.",
        reply_markup=build_main_keyboard(user.id),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Алгоритм записи:\n"
        "1. Кнопка «📅 Записаться» — выбираешь день и свободный час.\n"
        "2. Я отправляю запрос тренеру.\n"
        "3. После подтверждения приходит уведомление.\n"
        "4. Кнопка «🗓 Мои записи» — посмотреть активные заявки и отменить их.\n\n"
        "Если нужна другая длительность или есть вопросы, напиши тренеру напрямую.",
        reply_markup=build_main_keyboard(update.effective_user.id),
    )


async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if is_instructor(user.id):
        await update.message.reply_text(
            "Тренеру недоступна запись. Используйте кнопку «📘 Записи тренера».",
            reply_markup=build_main_keyboard(user.id),
        )
        return
    dates = collect_available_dates_with_labels()
    if not dates:
        await update.message.reply_text(
            "На ближайшее время нет рабочих дней для записи. Попробуйте позже.",
            reply_markup=build_main_keyboard(user.id),
        )
        return
    keyboard = build_dates_keyboard(dates, page=0, page_size=DATES_PAGE_SIZE)
    await update.message.reply_text(
        "Выбери подходящий день:",
        reply_markup=keyboard,
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
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
    for req in requests:
        slot_dt = timezone.localize(datetime.strptime(f"{req.date} {req.time}", "%Y-%m-%d %H:%M"))
        if slot_dt < now:
            continue
        status = STATUS_BADGES.get(req.status, req.status)
        lines.append(f"{humanize_slot(req.date, req.time)} — {req.full_name} ({status})")
        if req.status in TRAINER_CANCELABLE_STATUSES:
            label = f"{humanize_slot(req.date, req.time)} · {req.full_name}"
            options.append((req.id, label))
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
    if options:
        await update.message.reply_text(
            "Выбери запись, чтобы отменить её:",
            reply_markup=build_admin_cancel_keyboard(options),
        )


async def date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    _, date_str = query.data.split("|", maxsplit=1)
    dates, page, found = find_dates_and_page(date_str)
    if not found:
        await query.edit_message_text(
            "Эта дата больше недоступна. Выберите заново:",
            reply_markup=build_dates_keyboard(dates, page=0, page_size=DATES_PAGE_SIZE),
        )
        return
    day_label = format_day_full(datetime.strptime(date_str, "%Y-%m-%d").date())
    busy_map = store.list_day_states(date_str)
    blocked_slots = set(schedule_manager.get_blocked_slots_for_day(date_str))
    free_slots = [
        slot
        for slot in daily_slots
        if slot not in busy_map
        and busy_map.get(slot) not in {"pending", "confirmed"}
        and slot not in blocked_slots
    ]
    keyboard = build_times_keyboard(date_str, free_slots, back_page=page)
    await query.edit_message_text(
        text=f"Свободные часы на {day_label}:",
        reply_markup=keyboard,
    )


async def back_to_dates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|", maxsplit=1)
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    dates = collect_available_dates_with_labels()
    max_page = max((len(dates) - 1) // DATES_PAGE_SIZE, 0)
    page = max(0, min(page, max_page))
    if not dates:
        await query.edit_message_text(
            "На ближайшие даты запись недоступна. Попробуйте позже.",
            reply_markup=build_main_keyboard(query.from_user.id),
        )
        return
    await query.edit_message_text(
        "Выбери подходящий день:",
        reply_markup=build_dates_keyboard(dates, page=page, page_size=DATES_PAGE_SIZE),
    )


async def time_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, date_str, time_str = query.data.split("|", maxsplit=2)

    slot_dt = timezone.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
    if slot_dt < datetime.now(timezone):
        await query.answer("Нельзя выбрать прошедшее время", show_alert=True)
        return

    # Check if slot is blocked
    if schedule_manager.is_slot_blocked(date_str, time_str):
        await query.answer("Этот слот закрыт для записи тренером", show_alert=True)
        dates, page, _ = find_dates_and_page(date_str)
        await query.edit_message_text(
            "Этот слот закрыт. Выбери другую дату:",
            reply_markup=build_dates_keyboard(dates, page=page, page_size=DATES_PAGE_SIZE),
        )
        return

    dates, page, _ = find_dates_and_page(date_str)
    user = query.from_user
    try:
        request = store.create_request(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            date_str=date_str,
            time_str=time_str,
        )
    except ValueError:
        await query.answer("Слот уже занят. Выбери другой.", show_alert=True)
        await query.edit_message_text(
            "Этот час уже занят. Выбери другую дату:",
            reply_markup=build_dates_keyboard(dates, page=page, page_size=DATES_PAGE_SIZE),
        )
        return

    await query.edit_message_text(
        f"Запрос на {humanize_slot(date_str, time_str)} отправлен тренеру.\n"
        "Я напомню, когда он подтвердит запись."
    )

    admin_text = (
        "<b>Новая заявка</b>\n"
        f"Дата: {humanize_slot(date_str, time_str)}\n"
        f"Клиент: {user.full_name} "
        f"(@{user.username or 'нет'} | id {user.id})"
    )
    message = await context.bot.send_message(
        chat_id=settings.instructor_chat_id,
        text=admin_text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_request_review_keyboard(request.id),
    )


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
    dates = collect_available_dates_with_labels()
    max_page = max((len(dates) - 1) // DATES_PAGE_SIZE, 0)
    page = max(0, min(page, max_page))
    if not dates:
        await query.edit_message_text(
            "Нет доступных дат. Попробуйте позже.",
            reply_markup=build_main_keyboard(query.from_user.id),
        )
        return
    await query.edit_message_text(
        "Выбери подходящий день:",
        reply_markup=build_dates_keyboard(dates, page=page, page_size=DATES_PAGE_SIZE),
    )


async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.from_user.id != settings.instructor_chat_id:
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
    if query.from_user.id != settings.instructor_chat_id:
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
    await context.bot.send_message(
        chat_id=settings.instructor_chat_id,
        text=admin_text,
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
    await context.bot.send_message(
        chat_id=settings.instructor_chat_id,
        text=admin_text,
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
        reply_markup=build_schedule_manage_keyboard(),
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
        reply_markup=build_schedule_manage_keyboard(),
    )


async def schedule_days_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show days management interface."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    schedule_manager.cleanup_past_blocks(datetime.now(timezone))
    dates = collect_available_dates_with_labels()
    # Also include fully blocked days for unblocking
    today = datetime.now(timezone).date()
    blocked_dates = schedule_manager.get_all_blocked_dates()
    for offset in range(settings.booking_horizon_days):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() in settings.workdays:
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
    dates = collect_available_dates_with_labels()
    today = datetime.now(timezone).date()
    blocked_dates = schedule_manager.get_all_blocked_dates()
    for offset in range(settings.booking_horizon_days):
        candidate = today + timedelta(days=offset)
        if candidate.weekday() in settings.workdays:
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
    day_label = format_day_full(datetime.strptime(date_str, "%Y-%m-%d").date())
    blocked_times = schedule_manager.get_blocked_slots_for_day(date_str)
    keyboard = build_schedule_day_times_keyboard(date_str, daily_slots, blocked_times)
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
    day_label = format_day_full(datetime.strptime(date_str, "%Y-%m-%d").date())
    blocked_times = schedule_manager.get_blocked_slots_for_day(date_str)
    keyboard = build_schedule_day_times_keyboard(date_str, daily_slots, blocked_times)
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
    schedule_manager.block_day(date_str, daily_slots)
    await query.answer("Весь день закрыт ✅", show_alert=True)
    day_label = format_day_full(datetime.strptime(date_str, "%Y-%m-%d").date())
    blocked_times = schedule_manager.get_blocked_slots_for_day(date_str)
    keyboard = build_schedule_day_times_keyboard(date_str, daily_slots, blocked_times)
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
    day_label = format_day_full(datetime.strptime(date_str, "%Y-%m-%d").date())
    blocked_times = schedule_manager.get_blocked_slots_for_day(date_str)
    keyboard = build_schedule_day_times_keyboard(date_str, daily_slots, blocked_times)
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
    for day_offset in range(7):
        day = monday + timedelta(days=day_offset)
        if day.weekday() in settings.workdays:
            schedule_manager.block_day(day.isoformat(), daily_slots)
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
    status_text = "Используются настройки по умолчанию" if not has_custom else "Используются пользовательские настройки"
    await query.edit_message_text(
        f"⚙️ <b>Настройка рабочего времени</b>\n\n"
        f"Текущие настройки:\n"
        f"Дни: {', '.join([WEEKDAYS_RU_SHORT[d] for d in sorted(workdays)])}\n"
        f"Часы: {start_hour:02d}:00 - {end_hour:02d}:00\n\n"
        f"{status_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_work_settings_keyboard(has_custom),
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
    workdays = selected_days
    status_text = "Используются пользовательские настройки"
    await query.edit_message_text(
        f"⚙️ <b>Настройка рабочего времени</b>\n\n"
        f"Текущие настройки:\n"
        f"Дни: {', '.join([WEEKDAYS_RU_SHORT[d] for d in sorted(workdays)])}\n"
        f"Часы: {start_hour:02d}:00 - {end_hour:02d}:00\n\n"
        f"{status_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_work_settings_keyboard(has_custom),
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
    status_text = "Используются пользовательские настройки"
    await query.edit_message_text(
        f"⚙️ <b>Настройка рабочего времени</b>\n\n"
        f"Текущие настройки:\n"
        f"Дни: {', '.join([WEEKDAYS_RU_SHORT[d] for d in sorted(workdays)])}\n"
        f"Часы: {start_hour:02d}:00 - {end_hour:02d}:00\n\n"
        f"{status_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_work_settings_keyboard(has_custom),
    )


async def work_settings_reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset work settings to defaults."""
    query = update.callback_query
    if not is_instructor(query.from_user.id):
        await query.answer("Недостаточно прав", show_alert=True)
        return
    await query.answer()
    schedule_manager.clear_work_settings()
    refresh_daily_slots()
    await query.answer("Настройки сброшены к значениям по умолчанию ✅", show_alert=True)
    workdays, start_hour, end_hour = get_effective_work_settings()
    has_custom = False
    status_text = "Используются настройки по умолчанию"
    await query.edit_message_text(
        f"⚙️ <b>Настройка рабочего времени</b>\n\n"
        f"Текущие настройки:\n"
        f"Дни: {', '.join([WEEKDAYS_RU_SHORT[d] for d in sorted(workdays)])}\n"
        f"Часы: {start_hour:02d}:00 - {end_hour:02d}:00\n\n"
        f"{status_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_work_settings_keyboard(has_custom),
    )


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
            
            # Send to trainer
            trainer_message = (
                f"⏰ Напоминание: через 2 часа индивидуальная тренировка!\n"
                f"Время: {slot_text}\n"
                f"Клиент: {request.full_name} (@{request.username or 'нет'})"
            )
            try:
                await context.bot.send_message(
                    chat_id=settings.instructor_chat_id,
                    text=trainer_message,
                )
            except Exception as e:
                logger.warning(f"Failed to send reminder to trainer: {e}")
            
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

    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

