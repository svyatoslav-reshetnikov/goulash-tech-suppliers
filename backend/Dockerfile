FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# constraints.txt фиксирует точные версии всех зависимостей (pip freeze рабочего образа): сборка воспроизводима
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir --default-timeout=120 --retries 8 -r requirements.txt -c constraints.txt

COPY . .

EXPOSE 8000

# Миграции применяются при каждом старте, затем запускается API.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
