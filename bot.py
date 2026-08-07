"""Currency Rate Bot (aiogram v3 + официальный ЦБ РФ + apscheduler).

Стек: aiogram v3 (Telegram Bot API) + httpx (XML ЦБ РФ) + xml.etree (stdlib,
парсинг XML) + aiosqlite (история курсов) + APScheduler (ежедневная рассылка).

Команды:
  /rates          — курсы основных валют на сегодня
  /rate USD       — курс одной валюты
  /convert 100 USD— перевод валюты в рубли
  /history USD    — курс за последние 7 дней (из SQLite)
  /daily on|off   — подписка на ежедневную рассылку (в 10:00)

Запуск:  python bot.py   (задайте CURRENCY_BOT_TOKEN, или run_bot6.cmd).
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import os
from datetime import date

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from cbr_api import CbrClient, Rate
from db import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(config.BASE_DIR, "bot.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

router = Router()
db = Database(config.DB_PATH)
cbr = CbrClient()

# Бот создаётся в main() — нужен планировщику для ежедневной рассылки
_bot: Bot | None = None
scheduler = AsyncIOScheduler()


def _money(value: float) -> str:
    """9 123,45 — формат чисел как принято в России."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _fmt(rate: Rate) -> str:
    return (
        f"<b>{rate.char_code}</b> — {_money(rate.value)} ₽ "
        f"за {rate.nominal} {_html.escape(rate.name)}"
    )


async def _save_today(rates: dict[str, Rate]) -> None:
    await db.save_rates(rates, date.today().isoformat())


async def _daily_job() -> None:
    """Ежедневная рассылка подписчикам (apscheduler cron)."""
    if _bot is None:
        return
    try:
        rates = await cbr.fetch_rates()
    except Exception:
        logger.exception("Ежедневная рассылка: не удалось получить курсы")
        return
    await _save_today(rates)
    lines = [_fmt(r) for code, r in rates.items() if code in config.MAIN_CURRENCIES]
    text = f"📈 <b>Курсы ЦБ РФ на {date.today().isoformat()}</b>\n\n" + "\n".join(lines)
    for user_id, _username in await db.digest_users():
        try:
            await _bot.send_message(user_id, text)
        except Exception:
            logger.warning("Не удалось доставить рассылку user_id=%s", user_id)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "💱 <b>Currency Rate Bot</b>\n\n"
        "/rates — курсы основных валют\n"
        "/rate USD — курс одной валюты\n"
        "/convert 100 USD — перевод в рубли\n"
        "/history USD — курс за 7 дней\n"
        "/daily on — ежедневная рассылка (в 10:00)\n\n"
        f"Источник: <b>{'демо-данные' if cbr.demo_mode else 'официальный ЦБ РФ (cbr.ru)'}</b>"
    )


@router.message(Command("rates"))
async def cmd_rates(message: Message) -> None:
    status = await message.answer("Загружаю курсы…")
    try:
        rates = await cbr.fetch_rates()
    except Exception:
        logger.exception("Не удалось получить курсы")
        await status.edit_text("⚠️ Не удалось получить курсы. Попробуйте позже.")
        return
    await _save_today(rates)
    lines = [_fmt(r) for code, r in rates.items() if code in config.MAIN_CURRENCIES]
    await status.edit_text(
        f"💱 <b>Курсы ЦБ РФ на {date.today().isoformat()}</b>\n\n" + "\n".join(lines)
    )


@router.message(Command("rate"))
async def cmd_rate(message: Message) -> None:
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /rate USD")
        return
    code = args[1].upper()
    try:
        rate = await cbr.rate(code)
    except Exception:
        logger.exception("Не удалось получить курс %s", code)
        await message.answer("⚠️ Не удалось получить курс. Попробуйте позже.")
        return
    if rate is None:
        await message.answer(
            f"Валюта <b>{_html.escape(code)}</b> не найдена. "
            "Пример: /rate USD, /rate EUR, /rate CNY"
        )
        return
    await _save_today({code: rate})
    await message.answer(_fmt(rate))


@router.message(Command("convert"))
async def cmd_convert(message: Message) -> None:
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /convert 100 USD")
        return
    try:
        amount = float(args[1].replace(",", "."))
    except ValueError:
        await message.answer("Сумма должна быть числом: /convert 100 USD")
        return
    code = args[2].upper()
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    try:
        rub = await cbr.convert(code, amount)
    except Exception:
        logger.exception("Не удалось сконвертировать %s", code)
        await message.answer("⚠️ Не удалось получить курс. Попробуйте позже.")
        return
    if rub is None:
        await message.answer(
            f"Валюта <b>{_html.escape(code)}</b> не найдена. Пример: /convert 100 USD"
        )
        return
    await message.answer(
        f"💱 {_money(amount)} {_html.escape(code)} = "
        f"<b>{_money(rub)} ₽</b>"
    )


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /history USD")
        return
    code = args[1].upper()
    rows = await db.history(code, days=7)
    if not rows:
        await message.answer(
            f"История для <b>{_html.escape(code)}</b> пуста. "
            f"Запросите сначала /rate {_html.escape(code)} — курс сохранится в базу."
        )
        return
    lines = [f"{d}: <b>{_money(v)} ₽</b>" for d, v in rows]
    await message.answer(
        f"📅 <b>История курса {_html.escape(code)} (7 дней)</b>\n\n" + "\n".join(lines)
    )


@router.message(Command("daily"))
async def cmd_daily(message: Message) -> None:
    args = message.text.split()
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await message.answer("Использование: /daily on  или  /daily off")
        return
    on = args[1].lower() == "on"
    await db.set_digest(message.from_user.id, message.from_user.username, on)
    await message.answer(
        f"📬 Ежедневная рассылка курсов <b>{'включена' if on else 'выключена'}</b> "
        f"(каждый день в {config.DAILY_DIGEST_HOUR}:00)."
    )


async def main() -> None:
    global _bot
    if not config.BOT_TOKEN:
        raise SystemExit(
            "Не задан CURRENCY_BOT_TOKEN. Скопируйте .env.example и задайте токен."
        )
    _bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await db.init()
    scheduler.add_job(_daily_job, "cron", hour=config.DAILY_DIGEST_HOUR, minute=0)
    scheduler.start()
    logger.info(
        "Валютный бот запущен. Источник: %s",
        "демо-данные" if cbr.demo_mode else "ЦБ РФ (cbr.ru)",
    )
    try:
        await dp.start_polling(_bot)
    finally:
        await _bot.session.close()
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
