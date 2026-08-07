"""Пороговые алерты курсов — чистая логика, покрытая unit-тестами.

Пользователь может подписаться на алерт: «уведоми меня, когда USD станет
дешевле 90 ₽». Часовой джоб (apscheduler) проверяет активные алерты против
свежих курсов и уведомляет. Логика отделена от Telegram и БД.
"""
from __future__ import annotations

from cbr_api import Rate

DIRECTIONS = ("below", "above")


def parse_alert_args(args: list[str]) -> tuple[str, float, str] | None:
    """Разбирает '/alert USD 90 below' → ('USD', 90.0, 'below').

    Направление необязательно (по умолчанию below). None — если аргументы
    невалидны.
    """
    if len(args) < 2:
        return None
    code = args[0].upper()
    try:
        threshold = float(args[1].replace(",", "."))
    except ValueError:
        return None
    if threshold <= 0:
        return None
    direction = args[2].lower() if len(args) > 2 else "below"
    if direction not in DIRECTIONS:
        return None
    return code, threshold, direction


def evaluate_alerts(rates: dict[str, Rate], alerts: list[dict]) -> list[dict]:
    """Возвращает алерты, сработавшие при данных курсах.

    alerts: [{"id": int, "user_id": int, "char_code": "USD",
              "threshold": 90.0, "direction": "below"}]
    Сравниваем курс за 1 единицу валюты (per_one): 'below' — курс ниже порога,
    'above' — выше. Валюты, которых нет в rates, пропускаем.
    """
    triggered: list[dict] = []
    for alert in alerts:
        rate = rates.get(alert["char_code"].upper())
        if rate is None:
            continue
        current = rate.per_one
        if alert["direction"] == "below" and current < alert["threshold"]:
            triggered.append({**alert, "current": current})
        elif alert["direction"] == "above" and current > alert["threshold"]:
            triggered.append({**alert, "current": current})
    return triggered
