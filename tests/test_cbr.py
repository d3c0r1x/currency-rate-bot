"""Unit-тесты Currency Rate Bot: парсинг XML ЦБ, демо-режим, история,
watchlist и пороговые алерты."""
import asyncio

from alerts import evaluate_alerts, parse_alert_args
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


def _rates() -> dict[str, Rate]:
    return {
        "USD": Rate(char_code="USD", nominal=1, name="Доллар США", value=91.23),
        "JPY": Rate(char_code="JPY", nominal=100, name="Японская иена", value=60.83),
    }


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


# ---------------------------------------------------------------- продвинутый уровень

def test_watchlist_db(tmp_path) -> None:
    async def run() -> None:
        db = Database(str(tmp_path / "rates.db"))
        await db.init()
        await db.add_watch(1, "usd")   # регистр нормализуется
        await db.add_watch(1, "EUR")
        await db.add_watch(1, "USD")   # дубль игнорируется
        assert await db.watchlist(1) == ["EUR", "USD"]
        assert await db.remove_watch(1, "eur")   # регистр не важен
        assert await db.watchlist(1) == ["USD"]
        assert not await db.remove_watch(1, "EUR")  # повторное удаление — False

    asyncio.run(run())


def test_alert_db_and_trigger(tmp_path) -> None:
    async def run() -> None:
        db = Database(str(tmp_path / "rates.db"))
        await db.init()
        alert_id = await db.add_alert(1, "USD", 90.0, "below")
        await db.add_alert(1, "USD", 95.0, "above")
        await db.add_alert(2, "JPY", 0.6, "below")
        rows = await db.list_alerts(1)
        assert len(rows) == 2 and rows[0]["id"] == alert_id + 1  # новые сверху

        # курс USD = 91.23: 'below 90' и 'above 95' не сработали
        active = await db.all_active_alerts()
        triggered = evaluate_alerts(_rates(), active)
        assert triggered == []

        # курс ниже порога: меняем курс и пересчитываем
        rates_low = {"USD": Rate(char_code="USD", nominal=1, name="Доллар США", value=89.5)}
        triggered = evaluate_alerts(rates_low, active)
        assert len(triggered) == 1
        assert triggered[0]["id"] == alert_id
        assert round(triggered[0]["current"], 1) == 89.5

        # одноразовость: удаляем сработавший алерт
        assert await db.remove_alert(triggered[0]["user_id"], triggered[0]["id"])
        assert await db.remove_alert(2, 3)  # второй алерт тоже

    asyncio.run(run())


def test_evaluate_alerts_directions() -> None:
    rates = _rates()  # USD 91.23, JPY 0.6083
    alerts = [
        {"id": 1, "user_id": 1, "char_code": "USD", "threshold": 90.0, "direction": "above"},
        {"id": 2, "user_id": 1, "char_code": "USD", "threshold": 92.0, "direction": "below"},
        {"id": 3, "user_id": 2, "char_code": "JPY", "threshold": 0.61, "direction": "below"},
        {"id": 4, "user_id": 3, "char_code": "XXX", "threshold": 1.0, "direction": "below"},
    ]
    triggered = evaluate_alerts(rates, alerts)
    ids = sorted(t["id"] for t in triggered)
    # USD 91.23 > 90 (above) ✓; USD 91.23 < 92 (below) ✓;
    # JPY 0.6083 < 0.61 (below) ✓; XXX нет в курсах — пропущен
    assert ids == [1, 2, 3]


def test_parse_alert_args() -> None:
    assert parse_alert_args(["USD", "90"]) == ("USD", 90.0, "below")
    assert parse_alert_args(["usd", "90,5", "above"]) == ("USD", 90.5, "above")
    assert parse_alert_args(["USD", "abc"]) is None       # не число
    assert parse_alert_args(["USD", "-5"]) is None        # порог <= 0
    assert parse_alert_args(["USD", "90", "sideways"]) is None  # неверное направление
    assert parse_alert_args(["USD"]) is None              # мало аргументов
