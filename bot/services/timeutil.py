"""Работа со временем. В БД всё хранится в UTC, пользователю показывается его локальное время."""

import calendar
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo, available_timezones

DB_FORMAT = "%Y-%m-%d %H:%M:%S"
ALL_DAYS = 0b1111111
WEEKDAYS = 0b0011111
# Задача «на весь день» напоминает в это время.
ALL_DAY_REMIND = time(9, 0)

REPEAT_NONE = "none"
REPEAT_DAILY = "daily"
REPEAT_WEEKDAYS = "weekdays"
REPEAT_WEEKLY = "weekly"
REPEAT_MONTHLY = "monthly"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_db(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime(DB_FORMAT)


def from_db(value: str) -> datetime:
    return datetime.strptime(value, DB_FORMAT).replace(tzinfo=UTC)


def parse_hm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def fmt_hm(value: time) -> str:
    return value.strftime("%H:%M")


def local_now(tz: str, now: datetime | None = None) -> datetime:
    return (now or utcnow()).astimezone(ZoneInfo(tz))


def local_today(tz: str, now: datetime | None = None) -> date:
    return local_now(tz, now).date()


def local_to_utc(day: date, at: time, tz: str) -> datetime:
    return datetime.combine(day, at, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def pack_day(day: date) -> int:
    """Дата числом YYYYMMDD — компактно для callback_data."""
    return day.year * 10000 + day.month * 100 + day.day


def unpack_day(value: int) -> date:
    return date(value // 10000, value // 100 % 100, value % 100)


def day_in_mask(day: date, mask: int) -> bool:
    return bool(mask >> day.weekday() & 1)


def next_daily_run(at: time, tz: str, now: datetime, mask: int = ALL_DAYS) -> datetime:
    """Ближайший момент строго после now, когда наступит время at в один из дней маски."""
    if not mask & ALL_DAYS:
        raise ValueError("Маска дней пустая")
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    for shift in range(8):
        day = local.date() + timedelta(days=shift)
        if not day_in_mask(day, mask):
            continue
        run = datetime.combine(day, at, tzinfo=zone)
        if run > local:
            return run.astimezone(UTC)
    raise AssertionError("unreachable")


def add_month(day: date, anchor_day: int) -> date:
    """Следующий месяц, тот же день; 31-е в коротком месяце становится последним днём."""
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def add_years(day: date, years: int) -> date:
    """Тот же день через N лет; 29 февраля в невисокосный год становится 28-м."""
    year = day.year + years
    return date(year, day.month, min(day.day, calendar.monthrange(year, day.month)[1]))


def next_yearly(day: date, not_before: date) -> date:
    """Ближайшая годовщина day, не раньше not_before и хотя бы через год."""
    years = 1
    while add_years(day, years) < not_before:
        years += 1
    return add_years(day, years)


def next_repeat_date(day: date, repeat: str, anchor_day: int | None = None) -> date:
    if repeat == REPEAT_DAILY:
        return day + timedelta(days=1)
    if repeat == REPEAT_WEEKDAYS:
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day
    if repeat == REPEAT_WEEKLY:
        return day + timedelta(days=7)
    if repeat == REPEAT_MONTHLY:
        return add_month(day, anchor_day or day.day)
    raise ValueError(f"Задача не повторяется: {repeat}")


def advance_repeat(day: date, repeat: str, today: date, anchor_day: int | None = None) -> date:
    """Дата следующего повтора после выполнения: сдвиг хотя бы на один шаг и не раньше сегодня."""
    day = next_repeat_date(day, repeat, anchor_day)
    while day < today:
        day = next_repeat_date(day, repeat, anchor_day)
    return day


@cache
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())


def is_valid_tz(name: str) -> bool:
    return name in _zones()


def tz_offset_label(tz: str) -> str:
    offset = datetime.now(ZoneInfo(tz)).utcoffset() or timedelta()
    minutes = int(offset.total_seconds()) // 60
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")
