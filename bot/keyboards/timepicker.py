from datetime import time

from aiogram.types import InlineKeyboardMarkup

from bot.callbacks import TimeCb
from bot.keyboards.common import btn, chunks, kb, nav_row

KIND_TASK = "t"
KIND_HABIT = "h"
KIND_PLAIN = "p"

PRESETS = [(8, 0), (9, 0), (12, 0), (15, 0), (18, 0), (20, 0), (21, 0), (22, 0)]
# Доп. кнопка «без времени» у разных мастеров называется по-разному.
EXTRA = {KIND_TASK: "🗓 Весь день, без времени", KIND_HABIT: "🔕 Без напоминания"}


def presets_kb(kind: str) -> InlineKeyboardMarkup:
    presets = [btn(f"{h:02d}:{m:02d}", TimeCb(action="pick", kind=kind, h=h, m=m)) for h, m in PRESETS]
    rows = chunks(presets, 4)
    rows.append([btn("🕐 Другое время", TimeCb(action="exact", kind=kind))])
    if kind in EXTRA:
        rows.append([btn(EXTRA[kind], TimeCb(action="none", kind=kind))])
    rows.append(nav_row())
    return kb(*rows)


def hours_kb(kind: str) -> InlineKeyboardMarkup:
    hours = [btn(f"{h:02d}", TimeCb(action="hour", kind=kind, h=h)) for h in range(24)]
    rows = chunks(hours, 6)
    rows.append([btn("◀ Назад", TimeCb(action="presets", kind=kind))])
    return kb(*rows)


def minutes_kb(kind: str, hour: int) -> InlineKeyboardMarkup:
    minutes = [btn(f"{hour:02d}:{m:02d}", TimeCb(action="pick", kind=kind, h=hour, m=m)) for m in range(0, 60, 5)]
    rows = chunks(minutes, 4)
    rows.append([btn("◀ Назад", TimeCb(action="exact", kind=kind))])
    return kb(*rows)


def picked_time(cb: TimeCb) -> time:
    return time(cb.h, cb.m)
