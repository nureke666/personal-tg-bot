from datetime import date, datetime, time, timedelta

from aiosqlite import Connection, Row

from bot.db import execute, fetchall, fetchone, scalar
from bot.services import timeutil as tu

OPEN = "open"
DONE = "done"


def fire_times(due_date: date, due_time: time | None, offsets: list[int], tz: str) -> list[tuple[int, datetime]]:
    base = tu.local_to_utc(due_date, due_time or tu.ALL_DAY_REMIND, tz)
    return [(offset, base - timedelta(minutes=offset)) for offset in sorted(set(offsets))]


async def rebuild_reminders(
    db: Connection,
    task_id: int,
    due_date: str | None,
    due_time: str | None,
    offsets: list[int],
    tz: str,
    now: datetime,
) -> None:
    """Пересоздаёт напоминания задачи. Те, что уже в прошлом, сразу помечаются отправленными."""
    await execute(db, "DELETE FROM task_reminders WHERE task_id = ?", (task_id,))
    if not due_date:
        return
    at = tu.parse_hm(due_time) if due_time else None
    for offset, fire_at in fire_times(date.fromisoformat(due_date), at, offsets, tz):
        sent_at = tu.to_db(now) if fire_at <= now else None
        await execute(
            db,
            "INSERT INTO task_reminders (task_id, offset_min, fire_at, sent_at) VALUES (?, ?, ?, ?)",
            (task_id, offset, tu.to_db(fire_at), sent_at),
        )


async def create(db: Connection, user_id: int, draft: dict, tz: str, now: datetime) -> int:
    due_date = draft.get("due_date")
    task_id = await execute(
        db,
        "INSERT INTO tasks (user_id, title, details, due_date, due_time, repeat, anchor_day, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            draft["title"],
            draft.get("details"),
            due_date,
            draft.get("due_time"),
            draft.get("repeat", tu.REPEAT_NONE),
            date.fromisoformat(due_date).day if due_date else None,
            tu.to_db(now),
        ),
    )
    await rebuild_reminders(db, task_id, due_date, draft.get("due_time"), draft.get("offsets", []), tz, now)
    return task_id


async def update(db: Connection, task_id: int, draft: dict, tz: str, now: datetime) -> None:
    due_date = draft.get("due_date")
    await execute(
        db,
        "UPDATE tasks SET title = ?, details = ?, due_date = ?, due_time = ?, repeat = ?, anchor_day = ? WHERE id = ?",
        (
            draft["title"],
            draft.get("details"),
            due_date,
            draft.get("due_time"),
            draft.get("repeat", tu.REPEAT_NONE),
            date.fromisoformat(due_date).day if due_date else None,
            task_id,
        ),
    )
    await rebuild_reminders(db, task_id, due_date, draft.get("due_time"), draft.get("offsets", []), tz, now)


async def get(db: Connection, user_id: int, task_id: int) -> Row | None:
    return await fetchone(db, "SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))


async def offsets(db: Connection, task_id: int) -> list[int]:
    rows = await fetchall(
        db,
        "SELECT DISTINCT offset_min FROM task_reminders WHERE task_id = ? AND offset_min IS NOT NULL"
        " ORDER BY offset_min",
        (task_id,),
    )
    return [row[0] for row in rows]


def to_draft(task: Row, task_offsets: list[int]) -> dict:
    return {
        "title": task["title"],
        "details": task["details"],
        "due_date": task["due_date"],
        "due_time": task["due_time"],
        "repeat": task["repeat"],
        "offsets": task_offsets,
    }


async def move_to_next(db: Connection, task: Row, tz: str, now: datetime) -> date:
    """Переносит повторяющуюся задачу на следующий раз: хотя бы на шаг вперёд и не раньше сегодня."""
    next_date = tu.advance_repeat(
        date.fromisoformat(task["due_date"]), task["repeat"], tu.local_today(tz, now), task["anchor_day"]
    )
    task_offsets = await offsets(db, task["id"])
    await execute(db, "UPDATE tasks SET due_date = ? WHERE id = ?", (next_date.isoformat(), task["id"]))
    await rebuild_reminders(db, task["id"], next_date.isoformat(), task["due_time"], task_offsets, tz, now)
    return next_date


async def complete(db: Connection, task: Row, tz: str, now: datetime) -> date | None:
    """Отмечает задачу выполненной. Повторяющаяся переезжает на следующую дату, её и возвращаем."""
    await execute(
        db,
        "INSERT INTO task_log (user_id, task_id, title, done_at) VALUES (?, ?, ?, ?)",
        (task["user_id"], task["id"], task["title"], tu.to_db(now)),
    )
    if task["repeat"] != tu.REPEAT_NONE and task["due_date"]:
        return await move_to_next(db, task, tz, now)
    await execute(db, "UPDATE tasks SET status = ?, done_at = ? WHERE id = ?", (DONE, tu.to_db(now), task["id"]))
    return None


async def missed_recurring(db: Connection, user_id: int, today: date) -> list[Row]:
    """Повторяющиеся задачи, чей день прошёл без отметки: их пора перенести на следующий раз."""
    return await fetchall(
        db,
        "SELECT * FROM tasks WHERE user_id = ? AND status = 'open' AND repeat != 'none' AND due_date < ?",
        (user_id, today.isoformat()),
    )


async def reopen(db: Connection, task: Row, tz: str, now: datetime) -> None:
    await execute(db, "UPDATE tasks SET status = ?, done_at = NULL WHERE id = ?", (OPEN, task["id"]))
    await execute(
        db,
        "DELETE FROM task_log WHERE id = (SELECT MAX(id) FROM task_log WHERE task_id = ? AND user_id = ?)",
        (task["id"], task["user_id"]),
    )
    task_offsets = await offsets(db, task["id"])
    await rebuild_reminders(db, task["id"], task["due_date"], task["due_time"], task_offsets, tz, now)


async def delete(db: Connection, task_id: int) -> None:
    await execute(db, "DELETE FROM tasks WHERE id = ?", (task_id,))


_ORDER = " ORDER BY due_date, due_time IS NOT NULL, due_time, id"


async def list_view(db: Connection, user_id: int, view: str, today: date, now_hm: str) -> list[Row]:
    base = "SELECT * FROM tasks WHERE user_id = ? AND status = 'open'"
    day = today.isoformat()
    if view == "today":
        return await fetchall(db, base + " AND due_date = ?" + _ORDER, (user_id, day))
    if view == "tomorrow":
        return await fetchall(db, base + " AND due_date = ?" + _ORDER, (user_id, (today + timedelta(1)).isoformat()))
    if view == "week":
        week_end = (today + timedelta(days=6)).isoformat()
        return await fetchall(db, base + " AND due_date BETWEEN ? AND ?" + _ORDER, (user_id, day, week_end))
    if view == "overdue":
        return await fetchall(
            db,
            base + " AND (due_date < ? OR (due_date = ? AND due_time < ?))" + _ORDER,
            (user_id, day, day, now_hm),
        )
    if view == "nodate":
        return await fetchall(db, base + " AND due_date IS NULL ORDER BY id", (user_id,))
    if view == "done":
        return await fetchall(
            db, "SELECT * FROM tasks WHERE user_id = ? AND status = 'done' ORDER BY done_at DESC LIMIT 20", (user_id,)
        )
    raise ValueError(view)


async def counts(db: Connection, user_id: int, today: date, now_hm: str) -> dict[str, int]:
    day = today.isoformat()
    row = await fetchone(
        db,
        """
        SELECT
            SUM(due_date = ?),
            SUM(due_date = ?),
            SUM(due_date < ? OR (due_date = ? AND due_time < ?)),
            SUM(due_date IS NULL)
        FROM tasks WHERE user_id = ? AND status = 'open'
        """,
        (day, (today + timedelta(days=1)).isoformat(), day, day, now_hm, user_id),
    )
    return {key: value or 0 for key, value in zip(("today", "tomorrow", "overdue", "nodate"), row, strict=True)}


async def due_reminders(db: Connection, now: datetime) -> list[Row]:
    return await fetchall(
        db,
        """
        SELECT r.id AS reminder_id, r.fire_at, t.*, s.tz
        FROM task_reminders r
        JOIN tasks t ON t.id = r.task_id
        JOIN settings s ON s.user_id = t.user_id
        WHERE r.sent_at IS NULL AND r.fire_at <= ? AND t.status = 'open'
        ORDER BY r.fire_at
        """,
        (tu.to_db(now),),
    )


async def mark_reminder_sent(db: Connection, reminder_id: int, now: datetime) -> None:
    await execute(db, "UPDATE task_reminders SET sent_at = ? WHERE id = ?", (tu.to_db(now), reminder_id))


async def add_snooze(db: Connection, task_id: int, fire_at: datetime) -> None:
    await execute(db, "INSERT INTO task_reminders (task_id, fire_at) VALUES (?, ?)", (task_id, tu.to_db(fire_at)))


async def open_dated(db: Connection, user_id: int) -> list[Row]:
    return await fetchall(
        db, "SELECT * FROM tasks WHERE user_id = ? AND status = 'open' AND due_date IS NOT NULL", (user_id,)
    )


async def pending_reminders(db: Connection, task_id: int) -> int:
    return await scalar(db, "SELECT COUNT(*) FROM task_reminders WHERE task_id = ? AND sent_at IS NULL", (task_id,))


async def done_between(db: Connection, user_id: int, start: datetime, end: datetime) -> list[Row]:
    """Выполнения из журнала в полуинтервале [start, end) по UTC."""
    return await fetchall(
        db,
        "SELECT * FROM task_log WHERE user_id = ? AND done_at >= ? AND done_at < ? ORDER BY done_at",
        (user_id, tu.to_db(start), tu.to_db(end)),
    )
