"""Курсы ЦБ РФ: официальный XML (cbr.ru) + демо-режим.

Источник: http://www.cbr.ru/scripts/XML_daily.asp — официальный ежедневный
XML Центробанка РФ, бесплатный, без ключа. Разбираем стандартным
xml.etree.ElementTree (stdlib) — без внешних зависимостей для парсинга.
"""
from __future__ import annotations

import random
import xml.etree.ElementTree as ET

import httpx
from pydantic import BaseModel

import config

# (номинал, название, курс в рублях) — замороженные курсы для демо-режима
DEMO_RATES: dict[str, tuple[int, str, float]] = {
    "USD": (1, "Доллар США", 91.23),
    "EUR": (1, "Евро", 99.45),
    "CNY": (1, "Китайский юань", 12.67),
    "GBP": (1, "Фунт стерлингов", 116.05),
    "JPY": (100, "Японская иена", 60.83),
    "KZT": (100, "Казахстанский тенге", 18.90),
    "TRY": (1, "Турецкая лира", 2.86),
    "BYN": (1, "Белорусский рубль", 28.14),
}


class Rate(BaseModel):
    """Курс одной валюты: value рублей за nominal единиц."""

    char_code: str
    nominal: int
    name: str
    value: float

    @property
    def per_one(self) -> float:
        """Сколько рублей стоит 1 единица валюты."""
        return self.value / self.nominal


def parse_cbr_xml(xml_input: str | bytes) -> dict[str, Rate]:
    """Разбирает XML_daily.asp в {CharCode: Rate}.

    Принимает str (декодированный текст) или bytes — в этом случае expat сам
    учтёт объявленную в XML кодировку (ответ ЦБ приходит в windows-1251,
    поэтому клиент передаёт байты). Для str с декларацией кодировки
    декларация срезается: ET не принимает строку с не-UTF-8 декларацией.

    Числа в XML приходят с запятой в качестве десятичного разделителя
    («91,2345») — заменяем на точку перед float().
    """
    if isinstance(xml_input, str):
        xml_input = xml_input.lstrip()
        end = xml_input.find("?>")
        if xml_input.startswith("<?xml") and end != -1:
            xml_input = xml_input[end + 2:]
    root = ET.fromstring(xml_input)
    rates: dict[str, Rate] = {}
    for valute in root.findall("Valute"):
        code = (valute.findtext("CharCode") or "").strip()
        if not code:
            continue
        try:
            value = float((valute.findtext("Value") or "0").replace(",", "."))
            nominal = int(valute.findtext("Nominal") or "1")
        except ValueError:
            continue
        rates[code] = Rate(
            char_code=code,
            nominal=nominal,
            name=valute.findtext("Name") or code,
            value=value,
        )
    return rates


class CbrClient:
    """Загружает курсы. transport подменяется в тестах."""

    def __init__(
        self,
        *,
        demo_mode: bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.demo_mode = config.DEMO_MODE if demo_mode is None else demo_mode
        self._transport = transport

    async def fetch_rates(self) -> dict[str, Rate]:
        if self.demo_mode:
            # лёгкая «живая» вариация ±0.5%, чтобы бот не выглядел застывшим
            jitter = random.uniform(0.995, 1.005)
            return {
                code: Rate(
                    char_code=code,
                    nominal=nominal,
                    name=name,
                    value=round(value * jitter, 2),
                )
                for code, (nominal, name, value) in DEMO_RATES.items()
            }
        async with httpx.AsyncClient(
            transport=self._transport, timeout=config.CBR_TIMEOUT
        ) as client:
            resp = await client.get(config.CBR_URL)
            resp.raise_for_status()
        # Байты, а не текст: XML объявляет windows-1251, и expat декодирует сам
        rates = parse_cbr_xml(resp.content)
        if not rates:
            raise RuntimeError("Пустой ответ ЦБ РФ")
        return rates

    async def rate(self, code: str) -> Rate | None:
        rates = await self.fetch_rates()
        return rates.get(code.upper())

    async def convert(self, code: str, amount: float) -> float | None:
        """amount единиц валюты → рубли (или None, если валюта не найдена)."""
        rate = await self.rate(code)
        if rate is None:
            return None
        return amount * rate.value / rate.nominal
