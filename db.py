"""SQLite-БД (aiosqlite): история курсов, подписчики рассылки, watchlist,
пороговые алерты.

Продвинутый уровень:
  - watchlist — персональный набор валют пользователя (/watch, /watchlist);
  - alerts — пороговые алерты: пользователь задаёт «USD < 90» и получает
    уведомление, когда курс пересекает порог.
"""
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
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    user_id   INTEGER NOT NULL,
                    char_code TEXT NOT NULL,
                    PRIMARY KEY (user_id, char_code)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    char_code  TEXT NOT NULL,
                    threshold  REAL NOT NULL,
                    direction  TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            await db.commit()

    # --- история курсов ---

    async def save_rates(self, rates: dict[str, Rate], date_iso: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                """
                INSERT OR REPLACE INTO rates_history (char_code, date, value, nominal)
                VALUES (?, ?, ?, ?)
                """,
                [(code, date_iso, rate.value, rate.nominal) for code, rate in rates.items()],
            )
            await db.commit()
        return len(rates)

    async def history(self, char_code: str, days: int = 7) -> list[tuple[str, float]]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """
                SELECT date, value FROM rates_history
                WHERE char_code = ? ORDER BY date DESC LIMIT ?
                """,
                (char_code.upper(), days),
            ) as cur:
                rows = await cur.fetchall()
        return [(r[0], r[1]) for r in rows]

    async def latest_snapshot(self) -> tuple[str, dict[str, float]] | None:
        """Самый свежий сохранённый день: (дата, {код: курс}) — для дельт."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """
                SELECT date, char_code, value FROM rates_history
                WHERE date = (SELECT MAX(date) FROM rates_history)
                """
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            return None
        return rows[0][0], {r[1]: r[2] for r in rows}

    # --- ежедневная рассылка ---

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
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT user_id, username FROM users WHERE daily_digest = 1"
            ) as cur:
                rows = await cur.fetchall()
        return [(r[0], r[1] or "") for r in rows]

    # --- watchlist (персональный набор валют) ---

    async def add_watch(self, user_id: int, char_code: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO watchlist (user_id, char_code) VALUES (?, ?)",
                (user_id, char_code.upper()),
            )
            await db.commit()

    async def remove_watch(self, user_id: int, char_code: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND char_code = ?",
                (user_id, char_code.upper()),
            )
            await db.commit()
            return cur.rowcount > 0

    async def watchlist(self, user_id: int) -> list[str]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT char_code FROM watchlist WHERE user_id = ? ORDER BY char_code",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [r[0] for r in rows]

    # --- пороговые алерты ---

    async def add_alert(self, user_id: int, char_code: str, threshold: float, direction: str) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "INSERT INTO alerts (user_id, char_code, threshold, direction) "
                "VALUES (?, ?, ?, ?)",
                (user_id, char_code.upper(), threshold, direction),
            )
            await db.commit()
            return cur.lastrowid

    async def list_alerts(self, user_id: int) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM alerts WHERE user_id = ? ORDER BY id DESC", (user_id,)
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def remove_alert(self, user_id: int, alert_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM alerts WHERE user_id = ? AND id = ?", (user_id, alert_id)
            )
            await db.commit()
            return cur.rowcount > 0

    async def all_active_alerts(self) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, user_id, char_code, threshold, direction FROM alerts"
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
