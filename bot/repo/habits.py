from datetime import date, datetime

from aiosqlite import Connection, Row

from bot.db import execute, fetchall, fetchone
from bot.services import timeutil as tu

CHECK = "check"  # отмечать, когда сделал
QUIT = "quit"  # отказ: счётчик дней идёт сам, отметки — срывы
# Когда в течение дня делается обычная привычка; по частям делится список на день. У отказа не важна.
PARTS = ("morning", "day", "evening")


def next_run(remind_time: str | None, mask: int, tz: str, now: datetime) -> str | None:
    if not remind_time:
        return None
    return tu.to_db(tu.next_daily_run(tu.parse_hm(remind_time), tz, now, mask))


async def create(
    db: Connection,
    user_id: int,
    title: str,
    mask: int,
    remind_time: str | None,
    tz: str,
    now: datetime,
    kind: str = CHECK,
    since: date | None = None,
    part: str = PARTS[0],
) -> int:
    return await execute(
        db,
        "INSERT INTO habits (user_id, title, days_mask, remind_time, next_run_at, created_at, kind, since, part)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            title,
            mask,
            remind_time,
            next_run(remind_time, mask, tz, now),
            tu.to_db(now),
            kind,
            since.isoformat() if since else None,
            part,
        ),
    )


async def get(db: Connection, user_id: int, habit_id: int) -> Row | None:
    return await fetchone(db, "SELECT * FROM habits WHERE id = ? AND user_id = ?", (habit_id, user_id))


async def list_active(db: Connection, user_id: int) -> list[Row]:
    return await fetchall(db, "SELECT * FROM habits WHERE user_id = ? AND archived = 0 ORDER BY id", (user_id,))


async def planned(db: Connection, user_id: int, day: date) -> list[Row]:
    """Обычные привычки, назначенные на этот день (отказы не назначаются — они считаются сами)."""
    return [h for h in await list_active(db, user_id) if h["kind"] == CHECK and tu.day_in_mask(day, h["days_mask"])]


async def list_all(db: Connection, user_id: int) -> list[Row]:
    return await fetchall(db, "SELECT * FROM habits WHERE user_id = ? ORDER BY archived, id", (user_id,))


async def rename(db: Connection, habit_id: int, title: str) -> None:
    await execute(db, "UPDATE habits SET title = ? WHERE id = ?", (title, habit_id))


async def set_part(db: Connection, habit_id: int, part: str) -> None:
    await execute(db, "UPDATE habits SET part = ? WHERE id = ?", (part, habit_id))


async def set_since(db: Connection, habit_id: int, since: date) -> None:
    await execute(db, "UPDATE habits SET since = ? WHERE id = ?", (since.isoformat(), habit_id))


async def set_schedule(
    db: Connection, habit_id: int, mask: int, remind_time: str | None, tz: str, now: datetime
) -> None:
    await execute(
        db,
        "UPDATE habits SET days_mask = ?, remind_time = ?, next_run_at = ? WHERE id = ?",
        (mask, remind_time, next_run(remind_time, mask, tz, now), habit_id),
    )


async def set_archived(db: Connection, habit: Row, archived: bool, tz: str, now: datetime) -> None:
    await execute(
        db,
        "UPDATE habits SET archived = ?, next_run_at = ? WHERE id = ?",
        (int(archived), next_run(habit["remind_time"], habit["days_mask"], tz, now), habit["id"]),
    )


async def set_next_run(db: Connection, habit_id: int, next_run_at: str | None) -> None:
    await execute(db, "UPDATE habits SET next_run_at = ? WHERE id = ?", (next_run_at, habit_id))


async def delete(db: Connection, habit_id: int) -> None:
    await execute(db, "DELETE FROM habits WHERE id = ?", (habit_id,))


async def toggle_mark(db: Connection, habit_id: int, day: date) -> bool:
    """Переключает отметку за день; возвращает, отмечен ли день теперь."""
    removed = await fetchone(
        db, "DELETE FROM habit_marks WHERE habit_id = ? AND date = ? RETURNING 1", (habit_id, day.isoformat())
    )
    if removed:
        return False
    await execute(db, "INSERT INTO habit_marks (habit_id, date) VALUES (?, ?)", (habit_id, day.isoformat()))
    return True


async def set_mark(db: Connection, habit_id: int, day: date) -> None:
    await execute(db, "INSERT OR IGNORE INTO habit_marks (habit_id, date) VALUES (?, ?)", (habit_id, day.isoformat()))


async def remove_mark(db: Connection, habit_id: int, day: date) -> None:
    await execute(db, "DELETE FROM habit_marks WHERE habit_id = ? AND date = ?", (habit_id, day.isoformat()))


async def marks(db: Connection, habit_id: int) -> set[date]:
    rows = await fetchall(db, "SELECT date FROM habit_marks WHERE habit_id = ?", (habit_id,))
    return {date.fromisoformat(row[0]) for row in rows}


async def marked_ids(db: Connection, user_id: int, day: date) -> set[int]:
    rows = await fetchall(
        db,
        "SELECT m.habit_id FROM habit_marks m JOIN habits h ON h.id = m.habit_id WHERE h.user_id = ? AND m.date = ?",
        (user_id, day.isoformat()),
    )
    return {row[0] for row in rows}


async def due_reminders(db: Connection, now: datetime) -> list[Row]:
    return await fetchall(
        db,
        """
        SELECT h.*, s.tz FROM habits h JOIN settings s ON s.user_id = h.user_id
        WHERE h.archived = 0 AND h.remind_time IS NOT NULL AND h.next_run_at <= ?
        """,
        (tu.to_db(now),),
    )
