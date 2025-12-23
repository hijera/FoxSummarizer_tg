#!/bin/sh
# Скрипт-обертка для запуска daily_summary.py через cron
# Загружает переменные окружения из .env файла

cd /app

# Загружаем переменные окружения из .env (если используется python-dotenv, он сам загрузит)
# Но для надежности можно также использовать env, если переменные заданы в docker-compose
/usr/local/bin/python /app/daily_summary.py >> /app/logs/cron.log 2>&1
