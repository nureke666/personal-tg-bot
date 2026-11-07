"""Журнал действий: у каждого пользователя свой, чужие записи никто не видит."""

from datetime import datetime

from aiosqlite import Connection, Row

from bot.db import execute, fetchall
from bot.services import timeutil as tu

# Сколько последних записей хранить на человека. На экране больше 50 не бывает.
KEEP = 200


async def add(db: Connection, user_id: int, text: str, now: datetime | None = None) -> None:
    await execute(
        db,
        "INSERT INTO action_log (user_id, at, text) VALUES (?, ?, ?)",
        (user_id, tu.to_db(now or tu.utcnow()), text),
    )
    await execute(
        db,
        "DELETE FROM action_log WHERE user_id = ? AND id <= "
        "(SELECT id FROM action_log WHERE user_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?)",
        (user_id, user_id, KEEP),
    )


async def recent(db: Connection, user_id: int, limit: int) -> list[Row]:
    """Последние записи, свежие первыми."""
    return await fetchall(db, "SELECT * FROM action_log WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
