"""SQLite-БД (aiosqlite): история курсов + подписчики ежедневной рассылки."""
from __future__ import annotations

from cbr_api import Rate

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS rates_history (
                    char_code TEXT NOT NULL,
                    date      TEXT NOT NULL,
                    value     REAL NOT NULL,
                    nominal   INTEGER NOT NULL,
                    PRIMARY KEY (char_code, date)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id      INTEGER PRIMARY KEY,
                    username     TEXT,
                    daily_digest INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await db.commit()

    async def save_rates(self, rates: dict[str, Rate], date_iso: str) -> int:
        """Сохраняет курсы на дату, возвращает число записанных строк."""
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                """
                INSERT OR REPLACE INTO rates_history (char_code, date, value, nominal)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (code, date_iso, rate.value, rate.nominal)
                    for code, rate in rates.items()
                ],
            )
            await db.commit()
        return len(rates)

    async def history(self, char_code: str, days: int = 7) -> list[tuple[str, float]]:
        """Последние N дней курса валюты: [(date, value), ...] (новые сверху)."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """
                SELECT date, value FROM rates_history
                WHERE char_code = ?
                ORDER BY date DESC
                LIMIT ?
                """,
                (char_code.upper(), days),
            ) as cur:
                rows = await cur.fetchall()
        return [(r[0], r[1]) for r in rows]

    async def set_digest(self, user_id: int, username: str | None, on: bool) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, daily_digest)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    daily_digest = excluded.daily_digest
                """,
                (user_id, username or "", 1 if on else 0),
            )
            await db.commit()

    async def digest_users(self) -> list[tuple[int, str]]:
        """Пользователи, подписанные на ежедневную рассылку."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT user_id, username FROM users WHERE daily_digest = 1"
            ) as cur:
                rows = await cur.fetchall()
        return [(r[0], r[1] or "") for r in rows]
