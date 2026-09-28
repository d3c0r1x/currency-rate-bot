"""Currency Rate Bot (aiogram v3 + официальный ЦБ РФ + apscheduler).

Стек: aiogram v3 (Telegram Bot API) + httpx (XML ЦБ РФ) + xml.etree (stdlib,
парсинг XML) + aiosqlite (история курсов) + APScheduler (ежедневная рассылка
и проверка алертов).

Команды:
  /rates          — курсы моих валют (watchlist) или основных
  /rate USD       — курс одной валюты
  /convert 100 USD — перевод валюты в рубли
  /convert 100 USD EUR — перевод между валютами
  /history USD    — курс за последние 7 дней (из SQLite)
  /daily on|off   — подписка на ежедневную рассылку
  /watch USD EUR  — добавить валюты в мой список
  /unwatch USD    — убрать валюту из списка
  /watchlist      — мой список валют
  /alert USD 90 below — алерт: уведомить, когда курс пересечёт порог
  /alerts         — мои алерты
  /unalert ID     — удалить алерт

Продвинутый уровень:
  - персональный watchlist; пороговые алерты (чистая логика в alerts.py),
    проверяются часовым джобом apscheduler;
  - middlewares: троттлинг и логирование.

Запуск:  python bot.py   (задайте CURRENCY_BOT_TOKEN, или start.bat).
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
from alerts import evaluate_alerts, parse_alert_args
from cbr_api import CbrClient, Rate
from db import Database
from middlewares import LoggingMiddleware, ThrottlingMiddleware

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

# Бот создаётся в main() — нужен планировщику для рассылки и алертов
_bot: Bot | None = None
scheduler = AsyncIOScheduler()


def _money(value: float) -> str:
    """9 123,45 — формат чисел как принято в России."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def _fmt(rate: Rate, prev_value: float | None = None) -> str:
    delta = ""
    if prev_value and prev_value != rate.value:
        diff = rate.value - prev_value
        arrow = "📈" if diff > 0 else "📉"
        delta = f" {arrow} {diff:+.2f}"
    return (
        f"<b>{rate.char_code}</b> — {_money(rate.value)} ₽ "
        f"за {rate.nominal} {_html.escape(rate.name)}{delta}"
    )


async def _prev_rates() -> dict[str, Rate]:
    """Курсы последнего сохранённого дня из истории (для дельт в /rates).

    Сегодняшний день исключаем: сравнивать курс сам с собой нечего.
    Если база ещё холодная (истории нет) — возвращаем пустой словарь.
    """
    snap = await db.latest_snapshot()
    if not snap:
        return {}
    snap_date, values = snap
    if snap_date == date.today().isoformat():
        return {}
    return {
        code: Rate(char_code=code, nominal=1, name=code, value=value)
        for code, value in values.items()
    }


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


async def _alerts_job() -> None:
    """Проверка пороговых алертов (по умолчанию раз в час).

    Одноразовые: сработавший алерт удаляется после уведомления, чтобы не
    спамить. Логика срабатывания — чистая функция alerts.evaluate_alerts.
    """
    if _bot is None:
        return
    alerts = await db.all_active_alerts()
    if not alerts:
        return
    try:
        rates = await cbr.fetch_rates()
    except Exception:
        logger.exception("Алерты: не удалось получить курсы")
        return
    for alert in evaluate_alerts(rates, alerts):
        direction = "дешевле" if alert["direction"] == "below" else "дороже"
        try:
            await _bot.send_message(
                alert["user_id"],
                f"🔔 <b>Алерт сработал!</b>\n"
                f"<b>{alert['char_code']}</b> стал {direction} "
                f"{_money(alert['threshold'])} ₽ — сейчас "
                f"<b>{_money(alert['current'])} ₽</b>.",
            )
        except Exception:
            logger.warning("Алерт: не удалось уведомить user_id=%s", alert["user_id"])
        await db.remove_alert(alert["user_id"], alert["id"])


# ---------------------------------------------------------------- команды

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "💱 <b>Currency Rate Bot</b>\n\n"
        "/rates — курсы моих валют\n"
        "/rate USD — курс одной валюты\n"
        "/convert 100 USD — перевод в рубли\n"
        "/convert 100 USD EUR — перевод между валютами\n"
        "/history USD — курс за 7 дней\n"
        "/watch USD EUR — добавить валюты в список\n"
        "/watchlist — мой список валют\n"
        "/alert USD 90 below — алерт на порог курса\n"
        "/alerts — мои алерты\n"
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
    codes = await db.watchlist(message.from_user.id) or config.MAIN_CURRENCIES
    prev = await _prev_rates()
    lines = [_fmt(r, prev[code].value if code in prev else None) for code, r in rates.items() if code in codes]
    if not lines:
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
    if len(args) not in (3, 4):
        await message.answer(
            "Использование: /convert 100 USD  (в рубли)\n"
            "/convert 100 USD EUR  (между двумя валютами)"
        )
        return
    try:
        amount = float(args[1].replace(",", "."))
    except ValueError:
        await message.answer("Сумма должна быть числом: /convert 100 USD")
        return
    code_from = args[2].upper()
    code_to = args[3].upper() if len(args) == 4 else "RUB"
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    try:
        if code_to == "RUB":
            result = await cbr.convert(code_from, amount)
            suffix = "₽"
        else:
            result = await cbr.convert_between(code_from, code_to, amount)
            suffix = _html.escape(code_to)
    except Exception:
        logger.exception("Не удалось сконвертировать %s → %s", code_from, code_to)
        await message.answer("⚠️ Не удалось получить курс. Попробуйте позже.")
        return
    if result is None:
        await message.answer(
            f"Валюта <b>{_html.escape(code_from)}</b> или "
            f"<b>{_html.escape(code_to)}</b> не найдена. "
            "Пример: /convert 100 USD EUR"
        )
        return
    await message.answer(
        f"💱 {_money(amount)} {_html.escape(code_from)} = "
        f"<b>{_money(result)} {suffix}</b>"
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


# ---------------------------------------------------- watchlist и алерты

@router.message(Command("watch"))
async def cmd_watch(message: Message) -> None:
    codes = [c.upper() for c in message.text.split()[1:] if c.isalnum() and len(c) <= 8]
    if not codes:
        await message.answer("Использование: /watch USD EUR CNY")
        return
    for code in codes:
        await db.add_watch(message.from_user.id, code)
    await message.answer(
        f"👀 Валюты добавлены в ваш список: <b>{', '.join(codes)}</b>\n"
        "/rates — показать их курсы"
    )


@router.message(Command("unwatch"))
async def cmd_unwatch(message: Message) -> None:
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /unwatch USD")
        return
    code = args[1].upper()
    removed = await db.remove_watch(message.from_user.id, code)
    await message.answer(
        f"Валюта <b>{_html.escape(code)}</b> убрана из списка."
        if removed
        else f"<b>{_html.escape(code)}</b> не было в вашем списке."
    )


@router.message(Command("watchlist"))
async def cmd_watchlist(message: Message) -> None:
    codes = await db.watchlist(message.from_user.id)
    if not codes:
        await message.answer(
            "Ваш список валют пуст. Добавьте: /watch USD EUR\n"
            "Пока показываются основные валюты."
        )
        return
    await message.answer("👀 <b>Ваш список валют:</b>\n" + "\n".join(f"• {c}" for c in codes))


@router.message(Command("alert"))
async def cmd_alert(message: Message) -> None:
    parsed = parse_alert_args(message.text.split()[1:])
    if parsed is None:
        await message.answer(
            "Использование: /alert USD 90 below\n"
            "направление: <b>below</b> (дешевле) или <b>above</b> (дороже)"
        )
        return
    code, threshold, direction = parsed
    alert_id = await db.add_alert(message.from_user.id, code, threshold, direction)
    word = "дешевле" if direction == "below" else "дороже"
    await message.answer(
        f"🔔 Алерт создан: уведомлю, когда <b>{code}</b> станет {word} "
        f"<b>{_money(threshold)} ₽</b> (id: {alert_id}).\n"
        "Проверка — раз в час, /alerts — посмотреть все."
    )


@router.message(Command("alerts"))
async def cmd_alerts(message: Message) -> None:
    rows = await db.list_alerts(message.from_user.id)
    if not rows:
        await message.answer("У вас нет алертов. Создать: /alert USD 90 below")
        return
    lines = [
        f"• <code>{r['id']}</code> — {r['char_code']} "
        f"{'<' if r['direction'] == 'below' else '>'} {_money(r['threshold'])} ₽ "
        f"({r['created_at']})"
        for r in rows
    ]
    await message.answer("🔔 <b>Ваши алерты</b>\n\n" + "\n".join(lines) +
                         "\n\nУдалить: /unalert ID")


@router.message(Command("unalert"))
async def cmd_unalert(message: Message) -> None:
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /unalert 12 (id из /alerts)")
        return
    removed = await db.remove_alert(message.from_user.id, int(args[1]))
    await message.answer(
        f"Алерт <code>{args[1]}</code> удалён." if removed
        else f"Алерт <code>{args[1]}</code> не найден."
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
    dp.message.middleware(ThrottlingMiddleware(min_interval=config.THROTTLE_MIN_INTERVAL))
    dp.update.middleware(LoggingMiddleware())
    await db.init()
    scheduler.add_job(_daily_job, "cron", hour=config.DAILY_DIGEST_HOUR, minute=0)
    scheduler.add_job(_alerts_job, "interval", minutes=config.ALERT_CHECK_MINUTES)
    scheduler.start()
    logger.info(
        "Валютный бот запущен. Источник: %s. Алерты: каждые %s мин.",
        "демо-данные" if cbr.demo_mode else "ЦБ РФ (cbr.ru)",
        config.ALERT_CHECK_MINUTES,
    )
    try:
        await dp.start_polling(_bot)
    finally:
        await _bot.session.close()
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
