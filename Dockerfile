# ExCalendar Telegram bot
FROM python:3.12-slim

WORKDIR /app

# Зависимости (без psycopg2 — используется MySQL)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Бот читает config из переменных окружения
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.bot"]
