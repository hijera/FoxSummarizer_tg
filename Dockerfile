FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN mkdir -p /app/data /app/logs

# Установка cron
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Конвертируем окончания строк из CRLF в LF (если нужно) и делаем скрипты исполняемыми
RUN sed -i 's/\r$//' /app/start.sh && \
    sed -i 's/\r$//' /app/run_daily_summary.sh && \
    chmod +x /app/start.sh && \
    chmod +x /app/run_daily_summary.sh && \
    head -1 /app/start.sh | grep -q "#!/bin/sh" || (echo "Error: start.sh missing shebang" && exit 1)

# Установка cron задачи из файла crontab
RUN sed -i 's/\r$//' /app/crontab && \
    crontab /app/crontab && \
    touch /app/logs/cron.log

VOLUME ["/app/data", "/app/logs"]

# Запуск cron в фоне и основного бота
CMD ["/bin/sh", "/app/start.sh"]











