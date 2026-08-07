# Currency Rate Bot (Проект 6)

Telegram-бот с курсами валют: берёт официальный ежедневный XML **ЦБ РФ**
(бесплатно, без ключа), парсит его стандартным `xml.etree.ElementTree`,
умеет конвертировать валюты, хранит историю курсов в SQLite и **каждый день
в 10:00 рассылает подписчикам сводку** через APScheduler.

## Стек

| Библиотека   | Зачем                                              |
|--------------|----------------------------------------------------|
| aiogram v3.x | Telegram Bot API                                   |
| httpx        | асинхронные запросы к официальному XML ЦБ РФ       |
| xml.etree    | парсинг XML (stdlib, без внешних зависимостей)     |
| aiosqlite    | асинхронная SQLite (история курсов, подписчики)    |
| APScheduler  | ежедневная рассылка (cron)                         |
| pydantic     | модель курса с валидацией                          |

## Команды

- `/rates` — курсы основных валют на сегодня (сохраняются в SQLite)
- `/rate USD` — курс одной валюты
- `/convert 100 USD` — перевод валюты в рубли
- `/history USD` — курс за последние 7 дней из базы
- `/daily on|off` — подписка на ежедневную рассылку (в 10:00)

## Запуск

```bash
python -m venv .venv
pip install -r requirements.txt
export CURRENCY_BOT_TOKEN=123456:ABC...   # Windows PowerShell: $env:CURRENCY_BOT_TOKEN="..."
export CURRENCY_DEMO_MODE=0               # 1 — демо-режим без сети
python bot.py
```

Либо двойной клик по `run_bot6.cmd` (читает токен `TG_TOKEN` из корневого `.env`).

## Честное примечание об API ЦБ РФ

- Официальный источник: http://www.cbr.ru/scripts/XML_daily.asp — ежедневный
  XML с курсами, публикуется ЦБ РФ, бесплатный, без ключа.
- Формат: `<Valute><CharCode>USD</CharCode><Nominal>1</Nominal>
  <Name>Доллар США</Name><Value>91,2345</Value></Valute>`.
- Нюансы: десятичная запятая (`91,2345`), `Nominal` для «дорогих» валют
  (иена — 100 единиц), кодировка windows-1251 (httpx декодирует по заголовку).
- **В демо-режиме (`CURRENCY_DEMO_MODE=1`, по умолчанию) бот работает без сети**
  с замороженными курсами (±0.5% «живая» вариация).

## Тесты

```bash
pytest tests/ -q
```
