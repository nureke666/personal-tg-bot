"""Итоги за период по всем разделам: выполненные задачи, привычки, деньги."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, time, timedelta

from aiosqlite import Connection

from bot.repo import finance, habits, tasks
from bot.repo.habits import QUIT
from bot.services import reports
from bot.services import timeutil as tu


@dataclass(frozen=True)
class HabitLine:
    title: str
    quit: bool
    done: int = 0  # обычная: выполнено из запланированных
    planned: int = 0
    relapses: int = 0  # отказ: срывы за период


@dataclass
class Summary:
    title: str
    period: str
    tasks_done: int = 0
    top_tasks: list[tuple[str, int]] = field(default_factory=list)
    habits: list[HabitLine] = field(default_factory=list)
    income: int = 0
    expense: int = 0
    prev_expense: int = 0
    top_expenses: list[tuple[str, int]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.tasks_done or self.habits or self.income or self.expense)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def habit_line(habit, marks: set[date], start: date, end: date, today: date, tz: str) -> HabitLine | None:
    """Строка привычки за [start, end] или None, если её в этом периоде не было."""
    if habit["kind"] == QUIT:
        first = max(start, date.fromisoformat(habit["since"]))
    else:
        first = max(start, tu.local_today(tz, tu.from_db(habit["created_at"])))
    last = min(end, today)
    if first > last:
        return None
    in_period = {day for day in marks if first <= day <= last}
    # Когда привычку убрали в архив, мы не знаем, поэтому архивную показываем, только если в периоде есть отметки.
    if habit["archived"] and not in_period:
        return None
    if habit["kind"] == QUIT:
        return HabitLine(habit["title"], quit=True, relapses=len(in_period))
    planned = [day for day in _days(first, last) if tu.day_in_mask(day, habit["days_mask"])]
    # Сегодня, пока не отмечено, ещё не провал.
    if planned and planned[-1] == today and today not in marks:
        planned.pop()
    done = sum(day in marks for day in planned)
    return HabitLine(habit["title"], quit=False, done=done, planned=len(planned))


async def build(db: Connection, user_id: int, tz: str, period: str, anchor: date, today: date) -> Summary:
    start, end, title = reports.period_range(period, today, anchor)
    result = Summary(title=title, period=period)

    done = await tasks.done_between(
        db, user_id, tu.local_to_utc(start, time(0), tz), tu.local_to_utc(end + timedelta(days=1), time(0), tz)
    )
    result.tasks_done = len(done)
    result.top_tasks = Counter(row["title"] for row in done).most_common(5)

    for habit in await habits.list_all(db, user_id):
        line = habit_line(habit, await habits.marks(db, habit["id"]), start, end, today, tz)
        if line:
            result.habits.append(line)

    result.income, result.expense = await finance.totals(db, user_id, start, end)
    previous_start, previous_end, _ = reports.period_range(period, today, reports.shift(period, anchor, -1))
    _, result.prev_expense = await finance.totals(db, user_id, previous_start, previous_end)
    by_category = await finance.by_category(db, user_id, finance.EXPENSE, start, end)
    result.top_expenses = [(row["name"], row["total"]) for row in by_category[:3]]
    return result
