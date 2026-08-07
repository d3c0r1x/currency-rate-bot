"""Unit-тесты Currency Rate Bot: парсинг XML ЦБ, демо-режим, история."""
import asyncio

from cbr_api import CbrClient, Rate, parse_cbr_xml
from db import Database

SAMPLE_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="05.08.2026" name="Foreign Currency Market">
<Valute ID="R01235">
  <NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal>
  <Name>Доллар США</Name><Value>91,2345</Value>
</Valute>
<Valute ID="R01239">
  <NumCode>978</NumCode><CharCode>EUR</CharCode><Nominal>1</Nominal>
  <Name>Евро</Name><Value>99,4567</Value>
</Valute>
<Valute ID="R01820">
  <NumCode>392</NumCode><CharCode>JPY</CharCode><Nominal>100</Nominal>
  <Name>Японская иена</Name><Value>60,8300</Value>
</Valute>
</ValCurs>"""


def test_parse_cbr_xml() -> None:
    # байты в windows-1251 — как приходит реальный ответ ЦБ РФ
    rates = parse_cbr_xml(SAMPLE_XML.encode("cp1251"))
    assert set(rates) == {"USD", "EUR", "JPY"}
    assert rates["USD"].value == 91.2345       # запятая -> точка
    assert rates["USD"].per_one == 91.2345
    assert rates["EUR"].name == "Евро"
    assert rates["JPY"].per_one == 0.6083      # 60,83 за 100 иен
    # строка с декларацией кодировки тоже парсится (декларация срезается)
    assert set(parse_cbr_xml(SAMPLE_XML)) == {"USD", "EUR", "JPY"}


def test_demo_mode_rates() -> None:
    async def run() -> None:
        client = CbrClient(demo_mode=True)
        rates = await client.fetch_rates()
        assert len(rates) >= 8
        assert "USD" in rates and "EUR" in rates and "CNY" in rates
        assert rates["USD"].per_one > 0
        rub = await client.convert("usd", 100)   # регистр не важен
        assert rub is not None and rub > 0

    asyncio.run(run())


def test_convert_math() -> None:
    # 100 USD = 100 * (91.2345 / 1) рублей
    async def run() -> None:
        import httpx

        class FakeTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                return httpx.Response(
                    200, content=SAMPLE_XML.encode("cp1251"), request=request
                )

        client = CbrClient(demo_mode=False)
        client._transport = FakeTransport()   # не ходим в сеть
        rub = await client.convert("USD", 100)
        assert rub is not None
        assert round(rub, 2) == 9123.45
        assert await client.rate("XXX") is None  # несуществующая валюта

    asyncio.run(run())


def test_history_db(tmp_path) -> None:
    async def run() -> None:
        # файловая БД: у aiosqlite каждый connect(":memory:") даёт новую базу
        db = Database(str(tmp_path / "rates.db"))
        await db.init()
        await db.save_rates(
            {"USD": Rate(char_code="USD", nominal=1, name="Доллар США", value=91.23)},
            "2026-08-05",
        )
        await db.save_rates(
            {"USD": Rate(char_code="USD", nominal=1, name="Доллар США", value=92.00)},
            "2026-08-06",
        )
        history = await db.history("usd", days=7)
        assert len(history) == 2
        assert history[0] == ("2026-08-06", 92.00)  # новые сверху
        await db.set_digest(1, "alice", True)
        await db.set_digest(2, "bob", False)
        assert [uid for uid, _ in await db.digest_users()] == [1]

    asyncio.run(run())
