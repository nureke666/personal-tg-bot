from datetime import date, datetime

from aiosqlite import Connection, Row

from bot.db import execute, fetchall, fetchone
from bot.services import timeutil as tu

ACTIVE = "active"
PAUSED = "paused"
DONE = "done"

EDITABLE = {"title", "text", "target_date", "send_time"}
# Варианты повтора: (каждый год, за сколько дней до события начинать писать; None — каждый день).
REPEATS = {"once": (False, None), "yearly": (True, None), "yearly30": (True, 30), "yearly7": (True, 7)}


def next_run(send_time: str, tz: str, now: datetime) -> str:
    return tu.to_db(tu.next_daily_run(tu.parse_hm(send_time), tz, now))


async def create(
    db: Connection,
    user_id: int,
    title: str,
    text: str | None,
    target_date: str,
    send_time: str,
    tz: str,
    now: datetime,
    yearly: bool = False,
    lead_days: int | None = None,
) -> int:
    return await execute(
        db,
        "INSERT INTO countdowns (user_id, title, text, target_date, send_time, next_run_at, yearly, lead_days)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, title, text, target_date, send_time, next_run(send_time, tz, now), int(yearly), lead_days),
    )


async def get(db: Connection, user_id: int, countdown_id: int) -> Row | None:
    return await fetchone(db, "SELECT * FROM countdowns WHERE id = ? AND user_id = ?", (countdown_id, user_id))


async def list_all(db: Connection, user_id: int) -> list[Row]:
    return await fetchall(
        db, "SELECT * FROM countdowns WHERE user_id = ? ORDER BY status = 'done', target_date", (user_id,)
    )


async def update_field(db: Connection, countdown: Row, field: str, value: str | None, tz: str, now: datetime) -> None:
    if field not in EDITABLE:
        raise ValueError(field)
    await execute(db, f"UPDATE countdowns SET {field} = ? WHERE id = ?", (value, countdown["id"]))
    send_time = value if field == "send_time" else countdown["send_time"]
    status = countdown["status"]
    # Завершённый отсчёт оживает, если дату перенесли в будущее.
    if field == "target_date" and status == DONE and date.fromisoformat(value) >= tu.local_today(tz, now):
        status = ACTIVE
    await execute(
        db,
        "UPDATE countdowns SET status = ?, next_run_at = ? WHERE id = ?",
        (status, next_run(send_time, tz, now), countdown["id"]),
    )


async def set_repeat(
    db: Connection, countdown: Row, yearly: bool, lead_days: int | None, tz: str, now: datetime
) -> None:
    await execute(
        db, "UPDATE countdowns SET yearly = ?, lead_days = ? WHERE id = ?", (int(yearly), lead_days, countdown["id"])
    )
    today = tu.local_today(tz, now)
    if yearly and (countdown["status"] == DONE or date.fromisoformat(countdown["target_date"]) < today):
        # Завершённый отсчёт стал ежегодным — переносим на ближайшую годовщину и снова считаем.
        await roll_year(db, countdown, today, tz, now)


async def roll_year(db: Connection, countdown: Row, not_before: date, tz: str, now: datetime) -> None:
    """Ежегодный отсчёт после события: следующая годовщина, снова активен."""
    target = tu.next_yearly(date.fromisoformat(countdown["target_date"]), not_before)
    status = PAUSED if countdown["status"] == PAUSED else ACTIVE
    await execute(
        db,
        "UPDATE countdowns SET target_date = ?, status = ?, next_run_at = ? WHERE id = ?",
        (target.isoformat(), status, next_run(countdown["send_time"], tz, now), countdown["id"]),
    )


async def set_status(db: Connection, countdown: Row, status: str, tz: str, now: datetime) -> None:
    await execute(
        db,
        "UPDATE countdowns SET status = ?, next_run_at = ? WHERE id = ?",
        (status, next_run(countdown["send_time"], tz, now), countdown["id"]),
    )


async def set_next_run(db: Connection, countdown_id: int, next_run_at: str) -> None:
    await execute(db, "UPDATE countdowns SET next_run_at = ? WHERE id = ?", (next_run_at, countdown_id))


async def delete(db: Connection, countdown_id: int) -> None:
    await execute(db, "DELETE FROM countdowns WHERE id = ?", (countdown_id,))


async def due(db: Connection, now: datetime) -> list[Row]:
    return await fetchall(
        db,
        """
        SELECT c.*, s.tz FROM countdowns c JOIN settings s ON s.user_id = c.user_id
        WHERE c.status = 'active' AND c.next_run_at <= ?
        """,
        (tu.to_db(now),),
    )
