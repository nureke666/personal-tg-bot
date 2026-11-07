"""Форматирование: русские окончания, деньги, даты."""

import html
import re
from datetime import date
from decimal import Decimal, InvalidOperation

WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]  # fmt: skip
MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]  # fmt: skip
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

MAX_AMOUNT = 10**12  # в минорных единицах


def esc(value: str | None) -> str:
    return html.escape(value or "", quote=False)


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """forms: (1 день, 2 дня, 5 дней)."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def days(n: int) -> str:
    return f"{n} {plural(n, ('день', 'дня', 'дней'))}"


def fmt_money(minor: int, currency: str, sign: bool = False) -> str:
    whole, frac = divmod(abs(minor), 100)
    text = f"{whole:,}".replace(",", " ")
    if frac:
        text += f",{frac:02d}"
    prefix = "-" if minor < 0 else ("+" if sign and minor > 0 else "")
    return f"{prefix}{text} {currency}"


def parse_amount(text: str, allow_negative: bool = False, allow_zero: bool = False) -> int | None:
    """«1 500», «1500,50», «1.5» → минорные единицы. None, если это не сумма."""
    cleaned = re.sub(r"[\s  ]", "", text or "").replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d{1,2})?", cleaned):
        return None
    try:
        value = int(Decimal(cleaned) * 100)
    except InvalidOperation:
        return None
    if value < 0 and not allow_negative:
        return None
    if value == 0 and not allow_zero:
        return None
    if abs(value) >= MAX_AMOUNT:
        return None
    return value


def fmt_date(day: date, today: date) -> str:
    delta = (day - today).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    if delta == -1:
        return "вчера"
    text = fmt_day(day)
    if day.year != today.year:
        text += f" {day.year}"
    return text


def fmt_day(day: date) -> str:
    """«сб, 17 окт»"""
    return f"{WEEKDAYS_SHORT[day.weekday()].lower()}, {day.day} {MONTHS_SHORT[day.month - 1]}"


def fmt_date_long(day: date) -> str:
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]} {day.year}"


def bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, round(fraction * width)))
    return "▓" * filled + "░" * (width - filled)


def clip(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def quote(name: str) -> str:
    """«Название» для журнала действий: длинное обрезаем, чтобы 50 записей влезли в одно сообщение."""
    return f"«{clip(name)}»"
