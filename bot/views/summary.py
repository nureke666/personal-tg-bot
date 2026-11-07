from datetime import date

from aiogram.types import InlineKeyboardMarkup

from bot.callbacks import SumCb
from bot.keyboards.common import btn, kb
from bot.services import reports
from bot.services.summary import Summary
from bot.services.texts import esc, fmt_money
from bot.services.timeutil import pack_day

PERIODS = {"week": "Неделя", "month": "Месяц", "year": "Год"}


def _change(current: int, previous: int, period: str) -> str:
    if not previous:
        return ""
    percent = round((current - previous) * 100 / previous)
    arrow = "↑" if percent > 0 else "↓" if percent < 0 else "±"
    return f" ({arrow}{abs(percent)}% {reports.PREVIOUS[period]})"


def summary_text(summary: Summary, currency: str) -> str:
    lines = [f"📊 <b>Итоги: {summary.title}</b>"]
    if summary.empty:
        lines += ["", "За этот период ничего не записано."]
        return "\n".join(lines)
    if summary.tasks_done:
        lines += ["", f"📋 <b>Задачи</b> — выполнено {summary.tasks_done}"]
        for title, count in summary.top_tasks:
            lines.append(f"• {esc(title)}" + (f" ×{count}" if count > 1 else ""))
    if summary.habits:
        lines += ["", "✅ <b>Привычки</b>"]
        for habit in summary.habits:
            if habit.quit:
                result = f"срывов: {habit.relapses}" if habit.relapses else "без срывов 💪"
            elif habit.planned:
                result = f"{habit.done} из {habit.planned} ({round(habit.done * 100 / habit.planned)}%)"
            else:
                result = "не было запланировано"
            lines.append(f"{esc(habit.title)} — {result}")
    if summary.income or summary.expense:
        lines += [
            "",
            "💰 <b>Деньги</b>",
            f"Доходы: {fmt_money(summary.income, currency)}",
            f"Расходы: {fmt_money(summary.expense, currency)}"
            + _change(summary.expense, summary.prev_expense, summary.period),
            f"Итог: <b>{fmt_money(summary.income - summary.expense, currency, sign=True)}</b>",
        ]
        lines += [f"{esc(name)} — {fmt_money(total, currency)}" for name, total in summary.top_expenses]
    return "\n".join(lines)


def summary_kb(period: str, anchor: date, today: date) -> InlineKeyboardMarkup:
    tabs = [btn(("• " if key == period else "") + label, SumCb(p=key)) for key, label in PERIODS.items()]
    arrows = [btn("◀ Раньше", SumCb(p=period, d=pack_day(reports.shift(period, anchor, -1))))]
    if reports.has_next(period, anchor, today):
        arrows.append(btn("Позже ▶", SumCb(p=period, d=pack_day(reports.shift(period, anchor, 1)))))
    return kb(tabs, arrows)
