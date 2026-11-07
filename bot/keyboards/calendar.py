import calendar
from datetime import date

from aiogram.types import InlineKeyboardMarkup

from bot.callbacks import CalCb, NavCb
from bot.keyboards.common import btn, kb
from bot.services.texts import MONTHS, WEEKDAYS_SHORT

FUTURE = "f"
PAST = "p"
ANY = "a"


def _allowed(day: date, today: date, mode: str) -> bool:
    if mode == FUTURE:
        return day >= today
    if mode == PAST:
        return day <= today
    return True


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + delta
    return index // 12, index % 12 + 1


def calendar_kb(year: int, month: int, today: date, mode: str = ANY) -> InlineKeyboardMarkup:
    noop = CalCb(action="noop", mode=mode, y=year, m=month)
    prev_y, prev_m = _shift(year, month, -1)
    next_y, next_m = _shift(year, month, 1)
    current = (year, month) == (today.year, today.month)

    prev_btn = (
        btn(" ", noop) if mode == FUTURE and current
        else btn("◀", CalCb(action="nav", mode=mode, y=prev_y, m=prev_m))
    )  # fmt: skip
    next_btn = (
        btn(" ", noop) if mode == PAST and current
        else btn("▶", CalCb(action="nav", mode=mode, y=next_y, m=next_m))
    )  # fmt: skip
    rows = [
        [prev_btn, btn(f"{MONTHS[month - 1]} {year}", noop), next_btn],
        [btn(name, noop) for name in WEEKDAYS_SHORT],
    ]
    for week in calendar.monthcalendar(year, month):
        row = []
        for number in week:
            if not number:
                row.append(btn(" ", noop))
                continue
            day = date(year, month, number)
            if not _allowed(day, today, mode):
                row.append(btn("·", noop))
                continue
            label = f"[{number}]" if day == today else str(number)
            row.append(btn(label, CalCb(action="pick", mode=mode, y=year, m=month, d=number)))
        rows.append(row)
    rows.append(
        [
            btn("Сегодня", CalCb(action="pick", mode=mode, y=today.year, m=today.month, d=today.day)),
            btn("◀ Назад", NavCb(action="back")),
            btn("✖ Отмена", NavCb(action="cancel")),
        ]
    )
    return kb(*rows)


def picked_date(cb: CalCb) -> date:
    return date(cb.y, cb.m, cb.d)
