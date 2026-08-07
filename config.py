"""Конфигурация Currency Rate Bot через переменные окружения."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOT_TOKEN = os.getenv("CURRENCY_BOT_TOKEN", "")
DB_PATH = os.getenv("CURRENCY_DB_PATH", os.path.join(BASE_DIR, "rates.db"))

# 1 = демо-режим (встроенные курсы, сеть не нужна) | 0 = официальный ЦБ РФ
DEMO_MODE = os.getenv("CURRENCY_DEMO_MODE", "1") == "1"

# Официальный ежедневный XML ЦБ РФ (бесплатно, без ключа)
CBR_URL = os.getenv("CBR_URL", "http://www.cbr.ru/scripts/XML_daily.asp")
CBR_TIMEOUT = float(os.getenv("CBR_TIMEOUT", "10"))

# Час ежедневной рассылки (apscheduler, локальное время системы)
DAILY_DIGEST_HOUR = int(os.getenv("DAILY_DIGEST_HOUR", "10"))

# Валюты, показываемые в /rates и в ежедневной рассылке
MAIN_CURRENCIES = os.getenv(
    "MAIN_CURRENCIES",
    "USD,EUR,CNY,GBP,JPY,KZT,TRY,BYN",
).split(",")
