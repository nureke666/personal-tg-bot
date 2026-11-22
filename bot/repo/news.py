from datetime import datetime

from aiosqlite import Connection

from bot.db import fetchall
from bot.services import timeutil as tu


async def sent_keys(db: Connection) -> set[str]:
    return {row["url"] for row in await fetchall(db, "SELECT url FROM news_sent")}


async def remember(db: Connection, keys: list[str], now: datetime) -> None:
    await db.executemany(
        "INSERT OR IGNORE INTO news_sent (url, sent_at) VALUES (?, ?)", [(key, tu.to_db(now)) for key in keys]
    )
