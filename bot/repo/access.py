"""Кому можно пользоваться ботом. Владелец из OWNER_ID тоже лежит здесь (is_owner = 1)."""

from datetime import datetime, timedelta

from aiosqlite import Connection, Row

from bot.db import execute, fetchall, fetchone
from bot.services import timeutil as tu

# Повторный запрос доступа от того же человека не чаще раза в сутки, чтобы не спамить владельца.
REQUEST_COOLDOWN = timedelta(days=1)


async def sync_owner(db: Connection, owner_id: int, now: datetime) -> None:
    """Владелец из .env. Если OWNER_ID поменяли, прежний владелец теряет доступ."""
    await execute(db, "DELETE FROM allowed_users WHERE is_owner = 1 AND user_id != ?", (owner_id,))
    await execute(
        db,
        "INSERT INTO allowed_users (user_id, is_owner, added_at) VALUES (?, 1, ?)"
        " ON CONFLICT (user_id) DO UPDATE SET is_owner = 1",
        (owner_id, tu.to_db(now)),
    )


async def is_owner(db: Connection, user_id: int) -> bool:
    return await fetchone(db, "SELECT 1 FROM allowed_users WHERE user_id = ? AND is_owner = 1", (user_id,)) is not None


async def is_allowed(db: Connection, user_id: int) -> bool:
    return await fetchone(db, "SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)) is not None


async def allowed_ids(db: Connection) -> set[int]:
    return {row[0] for row in await fetchall(db, "SELECT user_id FROM allowed_users")}


async def list_allowed(db: Connection) -> list[Row]:
    return await fetchall(db, "SELECT * FROM allowed_users WHERE is_owner = 0 ORDER BY added_at")


async def allow(db: Connection, user_id: int, name: str | None, username: str | None, now: datetime) -> bool:
    """Открывает доступ. False, если он уже был."""
    added = await fetchone(
        db,
        "INSERT INTO allowed_users (user_id, name, username, added_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (user_id) DO NOTHING RETURNING 1",
        (user_id, name, username, tu.to_db(now)),
    )
    await drop_request(db, user_id)
    return added is not None


async def revoke(db: Connection, user_id: int) -> bool:
    """Закрывает доступ (данные человека остаются). False, если доступа и не было."""
    removed = await fetchone(db, "DELETE FROM allowed_users WHERE user_id = ? AND is_owner = 0 RETURNING 1", (user_id,))
    return removed is not None


async def touch(db: Connection, user_id: int, name: str, username: str | None) -> None:
    """Обновляет имя, под которым человек показывается в /users."""
    await execute(
        db,
        "UPDATE allowed_users SET name = ?, username = ? WHERE user_id = ? AND (name IS NOT ? OR username IS NOT ?)",
        (name, username, user_id, name, username),
    )


async def register_request(db: Connection, user_id: int, name: str, username: str | None, now: datetime) -> bool:
    """Запоминает запрос доступа. True — пора уведомить владельца."""
    previous = await get_request(db, user_id)
    if previous and now - tu.from_db(previous["requested_at"]) < REQUEST_COOLDOWN:
        return False
    await execute(
        db,
        "INSERT INTO access_requests (user_id, name, username, requested_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (user_id) DO UPDATE SET name = excluded.name, username = excluded.username,"
        " requested_at = excluded.requested_at",
        (user_id, name, username, tu.to_db(now)),
    )
    return True


async def get_request(db: Connection, user_id: int) -> Row | None:
    return await fetchone(db, "SELECT * FROM access_requests WHERE user_id = ?", (user_id,))


async def list_requests(db: Connection) -> list[Row]:
    return await fetchall(db, "SELECT * FROM access_requests ORDER BY requested_at")


async def drop_request(db: Connection, user_id: int) -> None:
    await execute(db, "DELETE FROM access_requests WHERE user_id = ?", (user_id,))
