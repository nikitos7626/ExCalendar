# Архитектура проекта (MVC + Repository)

## Структура папок

```
src/
├── models/           # Модели данных (сущности)
│   ├── __init__.py
│   └── booking.py    # BookingRequest
│
├── repositories/     # Доступ к БД (слой данных)
│   ├── __init__.py
│   ├── base.py       # Подключение к MySQL, DatabaseConfig
│   ├── migrations.py # run_migrations(), ensure_initial_trainers()
│   ├── trainer_repository.py  # Тренеры (invite_code, telegram_id)
│   ├── client_repository.py   # Клиенты (referred_by_trainer_id)
│   └── booking_repository.py # Брони (CRUD, слоты, напоминания)
│
├── services/         # Бизнес-логика (оркестрация репозиториев)
│   ├── __init__.py
│   └── booking_service.py    # Создание заявок, проверка слота, привязка к тренеру
│
├── views/            # Представление (форматирование, константы UI)
│   ├── __init__.py
│   └── formatters.py # Даты, слоты, статусы, кнопки меню
│
├── controllers/      # Зарезервировано под вынос хендлеров
│   └── __init__.py
│
├── config.py        # Настройки (Settings, load_settings)
├── keyboards.py     # Inline- и Reply-клавиатуры
├── schedule_manager.py  # Расписание (блокировки, слоты) — пока JSON
└── bot.py           # Точка входа, регистрация хендлеров, run_polling
```

## Поток данных

1. **Controller (bot.py)** — получает Update, вызывает Service или Repository.
2. **Service** — проверяет правила (например, «клиент привязан к тренеру»), вызывает Repositories.
3. **Repository** — выполняет SQL, возвращает модели или сырые данные.
4. **View (formatters, keyboards)** — формирует текст сообщений и кнопки.

## Зависимости

- `models` — не зависят ни от чего.
- `repositories` — зависят от `config` и `models`.
- `services` — зависят от `repositories` и `models`.
- `views` — только типы из `models` при необходимости.
- `bot.py` — импортирует config, repositories, services, models, views, keyboards, schedule_manager.

## Запуск

```bash
python -m src.bot
```

Перед первым запуском: `config.env` с `TELEGRAM_BOT_TOKEN`, `INSTRUCTOR_CHAT_ID`, `DATABASE_URL`.
