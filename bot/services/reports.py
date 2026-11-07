import calendar
from datetime import date, timedelta

from bot.services.texts import MONTHS, MONTHS_GENITIVE, bar, fmt_money

PERIODS = {
    "today": "Сегодня",
    "week": "Неделя",
    "month": "Месяц",
    "prev": "Прошлый месяц",
    "year": "Год",
    "all": "Всё время",
}

WARN_PERCENT = 80


def month_range(day: date) -> tuple[date, date]:
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


# Периоды, которые можно листать стрелками ◀ ▶.
NAVIGABLE = ("today", "week", "month", "year")
PREVIOUS = {"today": "ко вчера", "week": "к прошлой неделе", "month": "к прошлому месяцу", "year": "к прошлому году"}


def period_range(period: str, today: date, anchor: date | None = None) -> tuple[date | None, date | None, str]:
    """(начало, конец включительно, заголовок). anchor — любой день нужного периода, по умолчанию сегодня."""
    day = anchor or today
    if period == "today":
        if day == today:
            return day, day, f"Сегодня, {day.day} {MONTHS_GENITIVE[day.month - 1]}"
        return day, day, f"{day.day} {MONTHS_GENITIVE[day.month - 1]} {day.year}"
    if period == "week":
        monday = day - timedelta(days=day.weekday())
        sunday = monday + timedelta(days=6)
        year = "" if monday.year == sunday.year == today.year else f".{sunday.year}"
        return monday, sunday, f"Неделя {monday.strftime('%d.%m')}–{sunday.strftime('%d.%m')}{year}"
    if period == "month":
        start, end = month_range(day)
        return start, end, f"{MONTHS[day.month - 1]} {day.year}"
    if period == "prev":
        last = today.replace(day=1) - timedelta(days=1)
        start, end = month_range(last)
        return start, end, f"{MONTHS[last.month - 1]} {last.year}"
    if period == "year":
        return date(day.year, 1, 1), date(day.year, 12, 31), f"{day.year} год"
    if period == "all":
        return None, None, "Всё время"
    raise ValueError(period)


def shift(period: str, anchor: date, step: int) -> date:
    """День в соседнем периоде: step = -1 — предыдущий, 1 — следующий."""
    if period == "today":
        return anchor + timedelta(days=step)
    if period == "week":
        return anchor + timedelta(weeks=step)
    if period == "month":
        index = anchor.year * 12 + anchor.month - 1 + step
        return date(index // 12, index % 12 + 1, 1)
    if period == "year":
        return date(anchor.year + step, 1, 1)
    raise ValueError(period)


def has_next(period: str, anchor: date, today: date) -> bool:
    """Следующий период уже начался — туда можно листать."""
    start, _, _ = period_range(period, today, shift(period, anchor, 1))
    return start <= today


def limit_alert(before: int, after: int, limit: int | None, label: str, currency: str) -> str | None:
    """Предупреждение, только если именно эта трата пересекла 80% или 100% лимита."""
    if not limit or limit <= 0:
        return None
    spent = f"{fmt_money(after, currency)} из {fmt_money(limit, currency)}"
    if before < limit <= after:
        return f"🚨 {label}: лимит превышен — {spent}"
    if before * 100 < limit * WARN_PERCENT <= after * 100:
        return f"⚠️ {label}: потрачено {after * 100 // limit}% лимита — {spent}"
    return None


def limit_progress(spent: int, limit: int, currency: str) -> str:
    percent = spent * 100 // limit if limit else 0
    mark = " 🚨" if spent > limit else (" ⚠️" if percent >= WARN_PERCENT else "")
    return (
        f"{fmt_money(spent, currency)} из {fmt_money(limit, currency)} ({percent}%){mark}\n"
        f"{bar(min(spent / limit, 1) if limit else 0)}"
    )
