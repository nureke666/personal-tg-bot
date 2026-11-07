"""Серии и статистика привычек.

Правила: пропуск дня, на который привычка не назначена, серию не рвёт. Отметка в такой день
засчитывается как бонус. Сегодняшний день, пока не отмечен, серию тоже не рвёт.

У отказа счётчик — полные дни после последнего срыва (или после дня, с которого держишься):
в день срыва он 0, назавтра 1.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from bot.services.timeutil import day_in_mask

DONE = "✅"
MISSED = "❌"
PENDING = "⬜"
OFF = "·"


@dataclass(frozen=True)
class HabitStats:
    current: int
    best: int
    percent_30: int
    week: list[tuple[date, str]]


@dataclass(frozen=True)
class QuitStats:
    current: int
    best: int
    relapses_30: int
    last: date | None  # последний срыв


def _start(marks: set[date], created: date) -> date:
    return min([created, *marks])


def current_streak(marks: set[date], mask: int, created: date, today: date) -> int:
    start = _start(marks, created)
    streak, day = 0, today
    while day >= start:
        if day in marks:
            streak += 1
        elif day_in_mask(day, mask) and day != today:
            break
        day -= timedelta(days=1)
    return streak


def best_streak(marks: set[date], mask: int, created: date, today: date) -> int:
    best = run = 0
    day = _start(marks, created)
    while day <= today:
        if day in marks:
            run += 1
            best = max(best, run)
        elif day_in_mask(day, mask) and day != today:
            run = 0
        day += timedelta(days=1)
    return best


def percent_30(marks: set[date], mask: int, created: date, today: date) -> int:
    start = max(_start(marks, created), today - timedelta(days=29))
    planned = done = 0
    day = start
    while day <= today:
        if day_in_mask(day, mask) and (day != today or day in marks):
            planned += 1
            done += day in marks
        day += timedelta(days=1)
    return round(100 * done / planned) if planned else 0


def week_grid(marks: set[date], mask: int, created: date, today: date) -> list[tuple[date, str]]:
    start = _start(marks, created)
    grid = []
    for shift in range(6, -1, -1):
        day = today - timedelta(days=shift)
        if day in marks:
            status = DONE
        elif day < start or not day_in_mask(day, mask):
            status = OFF
        elif day == today:
            status = PENDING
        else:
            status = MISSED
        grid.append((day, status))
    return grid


def stats(marks: set[date], mask: int, created: date, today: date) -> HabitStats:
    return HabitStats(
        current=current_streak(marks, mask, created, today),
        best=best_streak(marks, mask, created, today),
        percent_30=percent_30(marks, mask, created, today),
        week=week_grid(marks, mask, created, today),
    )


def quit_stats(relapses: set[date], since: date, today: date) -> QuitStats:
    points = sorted(day for day in relapses if since <= day <= today)
    start = points[-1] if points else since
    current = (today - start).days
    best, previous = current, since
    for day in points:
        # Перед срывом счётчик успел дойти до (срыв − начало − 1).
        best = max(best, (day - previous).days - 1)
        previous = day
    month_ago = today - timedelta(days=29)
    return QuitStats(
        current=current,
        best=best,
        relapses_30=sum(day >= month_ago for day in points),
        last=points[-1] if points else None,
    )
