"""Пересчёт всего расписания пользователя, например после смены часового пояса.

Локальные даты и время остаются прежними, меняются только моменты в UTC.
"""

from datetime import datetime

from aiosqlite import Connection

from bot.repo import countdowns, habits, settings, tasks


async def reschedule_all(db: Connection, user_id: int, now: datetime) -> None:
    user = await settings.get(db, user_id)
    tz = user["tz"]
    await settings.update(
        db,
        user_id,
        **{f"{key}_next_at": settings.next_at(key, user[f"{key}_time"], tz, now) for key in settings.SCHEDULES},
    )
    for countdown in await countdowns.list_all(db, user_id):
        await countdowns.set_next_run(db, countdown["id"], countdowns.next_run(countdown["send_time"], tz, now))
    for habit in await habits.list_active(db, user_id):
        await habits.set_next_run(db, habit["id"], habits.next_run(habit["remind_time"], habit["days_mask"], tz, now))
    for task in await tasks.open_dated(db, user_id):
        offsets = await tasks.offsets(db, task["id"])
        await tasks.rebuild_reminders(db, task["id"], task["due_date"], task["due_time"], offsets, tz, now)
