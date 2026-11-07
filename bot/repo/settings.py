from datetime import datetime

from aiosqlite import Connection, Row

from bot.db import execute, fetchall, fetchone
from bot.repo import finance
from bot.services import timeutil as tu

FIELDS = {
    "tz", "currency", "month_limit", "default_account_id",
    "digest_on", "digest_time", "digest_next_at",
    "checkin_on", "checkin_time", "checkin_next_at",
    "backup_on", "backup_time", "backup_next_at",
    "weekly_on", "weekly_time", "weekly_next_at",
    "morning_on", "morning_time", "morning_next_at", "morning_msg_id", "morning_msg_day",
}  # fmt: skip
# Рассылки по расписанию: у каждой есть поля {key}_on, {key}_time и {key}_next_at.
SCHEDULES = ("morning", "digest", "checkin", "weekly", "backup")
SUNDAY = 1 << 6
# Итоги недели приходят по воскресеньям, остальное — каждый день.
SCHEDULE_DAYS = {"weekly": SUNDAY}


def next_at(key: str, at: str, tz: str, now: datetime) -> str:
    return tu.to_db(tu.next_daily_run(tu.parse_hm(at), tz, now, SCHEDULE_DAYS.get(key, tu.ALL_DAYS)))


async def get(db: Connection, user_id: int) -> Row | None:
    return await fetchone(db, "SELECT * FROM settings WHERE user_id = ?", (user_id,))


async def ensure(db: Connection, user_id: int, tz: str, now: datetime | None = None) -> Row:
    """Настройки пользователя; при первом обращении создаются вместе с категориями по умолчанию."""
    row = await get(db, user_id)
    if row:
        return row
    now = now or tu.utcnow()
    defaults = {"morning": "06:00", "digest": "08:00", "checkin": "21:00", "weekly": "20:00", "backup": "03:00"}
    values = {
        "user_id": user_id,
        "tz": tz,
        "morning_on": 1,
        **{f"{key}_next_at": next_at(key, at, tz, now) for key, at in defaults.items()},
    }
    await execute(
        db,
        f"INSERT OR IGNORE INTO settings ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
        tuple(values.values()),
    )
    await finance.seed_categories(db, user_id)
    await finance.seed_accounts(db, user_id)
    return await get(db, user_id)


async def update(db: Connection, user_id: int, **fields) -> None:
    unknown = set(fields) - FIELDS
    if unknown:
        raise ValueError(f"Неизвестные поля настроек: {unknown}")
    assignments = ", ".join(f"{name} = ?" for name in fields)
    await execute(db, f"UPDATE settings SET {assignments} WHERE user_id = ?", (*fields.values(), user_id))


async def all_users(db: Connection) -> list[Row]:
    return await fetchall(db, "SELECT * FROM settings")


async def due_digests(db: Connection, now: datetime) -> list[Row]:
    return await fetchall(db, "SELECT * FROM settings WHERE digest_on = 1 AND digest_next_at <= ?", (tu.to_db(now),))


async def due_checkins(db: Connection, now: datetime) -> list[Row]:
    return await fetchall(db, "SELECT * FROM settings WHERE checkin_on = 1 AND checkin_next_at <= ?", (tu.to_db(now),))


async def due_weekly(db: Connection, now: datetime) -> list[Row]:
    return await fetchall(
        db,
        "SELECT * FROM settings WHERE weekly_on = 1 AND (weekly_next_at IS NULL OR weekly_next_at <= ?)",
        (tu.to_db(now),),
    )


async def due_mornings(db: Connection, now: datetime) -> list[Row]:
    return await fetchall(
        db,
        "SELECT * FROM settings WHERE morning_on = 1 AND (morning_next_at IS NULL OR morning_next_at <= ?)",
        (tu.to_db(now),),
    )


async def due_backup(db: Connection, user_id: int, now: datetime) -> Row | None:
    """Настройки владельца, если ему пора прислать бэкап."""
    return await fetchone(
        db,
        "SELECT * FROM settings WHERE user_id = ? AND backup_on = 1"
        " AND (backup_next_at IS NULL OR backup_next_at <= ?)",
        (user_id, tu.to_db(now)),
    )
