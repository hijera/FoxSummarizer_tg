#!/bin/sh
# Скрипт запуска бота и cron

# Создаем директорию для логов, если её нет
mkdir -p /app/logs

# Запуск cron в фоне
cron

# Запуск основного бота
exec python bot.py
