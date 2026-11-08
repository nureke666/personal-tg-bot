from collections.abc import Mapping, Sequence
from datetime import date

from aiogram.types import InlineKeyboardMarkup

from bot.callbacks import FinCb, PayCb
from bot.keyboards.common import btn, chunks, kb
from bot.repo.payments import MONTHLY, WEEKLY, YEARLY
from bot.services.texts import esc, fmt_date, fmt_money, plural
from bot.services.timeutil import pack_day

PERIOD_TITLES = {MONTHLY: "каждый месяц", WEEKLY: "каждую неделю", YEARLY: "каждый год"}
# Сколько раз в месяц в среднем — для строки «в месяц примерно».
PER_MONTH = {MONTHLY: 1, WEEKLY: 52 / 12, YEARLY: 1 / 12}
BACK_TO_LIST = kb([btn("◀ Платежи", PayCb(act="list"))])


def _when(due: date, today: date) -> str:
    late = (today - due).days
    if late > 0:
        return f"просрочен на {late} {plural(late, ('день', 'дня', 'дней'))}"
    return fmt_date(due, today)


def list_view(payments: Sequence[Mapping], currency: str, today: date) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["📅 <b>Регулярные платежи</b>", "В день платежа напомню с кнопкой «Оплачено» — она запишет расход.", ""]
    if payments:
        for p in payments:
            when = _when(date.fromisoformat(p["due_date"]), today)
            lines.append(f"{when} · {esc(p['title'])} — {fmt_money(p['amount'], currency)}")
        monthly = round(sum(p["amount"] * PER_MONTH[p["period"]] for p in payments))
        lines += ["", f"В месяц примерно: <b>{fmt_money(monthly, currency)}</b>"]
    else:
        lines.append("Пока нет ни одного: аренда, связь, подписки…")
    rows = chunks([btn(p["title"], PayCb(act="open", id=p["id"])) for p in payments], 2)
    rows.append([btn("➕ Новый платёж", PayCb(act="new"))])
    rows.append([btn("◀ Финансы", FinCb(act="menu"))])
    return "\n".join(lines), kb(*rows)


def card(payment: Mapping, currency: str, today: date) -> tuple[str, InlineKeyboardMarkup]:
    due = date.fromisoformat(payment["due_date"])
    lines = [
        f"📅 <b>{esc(payment['title'])}</b> — {fmt_money(payment['amount'], currency)}",
        f"🔁 {PERIOD_TITLES[payment['period']]}",
        f"🗓 Ближайший: {_when(due, today)}",
        f"🗂 {esc(payment['category'])}",
    ]
    if payment["account"]:
        lines.append(f"👛 {esc(payment['account'])}")
    pid, day = payment["id"], pack_day(due)
    return "\n".join(lines), kb(
        [btn("✅ Оплачено", PayCb(act="paid", id=pid, d=day)), btn("⏭ Пропустить", PayCb(act="skip", id=pid, d=day))],
        [btn("✏️ Сумма", PayCb(act="amount", id=pid)), btn("🗑 Удалить", PayCb(act="del", id=pid))],
        [btn("◀ Платежи", PayCb(act="list"))],
    )


def reminder(payment: Mapping, today: date) -> tuple[str, InlineKeyboardMarkup]:
    due = date.fromisoformat(payment["due_date"])
    when = "сегодня день оплаты" if due >= today else _when(due, today)
    text = (
        f"📅 <b>{esc(payment['title'])}</b> — {fmt_money(payment['amount'], payment['currency'])}\n{when.capitalize()}."
    )
    pid, day = payment["id"], pack_day(due)
    return text, kb(
        [btn("✅ Оплачено", PayCb(act="paid", id=pid, d=day)), btn("⏰ Завтра", PayCb(act="snooze", id=pid, d=day))],
        [btn("⏭ Пропустить", PayCb(act="skip", id=pid, d=day))],
    )


def categories_kb(categories: Sequence[Mapping]) -> InlineKeyboardMarkup:
    rows = chunks([btn(c["name"], PayCb(act="cat", id=c["id"])) for c in categories], 2)
    rows.append([btn("✖ Отмена", PayCb(act="list"))])
    return kb(*rows)


def period_kb() -> InlineKeyboardMarkup:
    return kb(
        [btn(PERIOD_TITLES[MONTHLY].capitalize(), PayCb(act="period", v=MONTHLY))],
        [
            btn(PERIOD_TITLES[WEEKLY].capitalize(), PayCb(act="period", v=WEEKLY)),
            btn(PERIOD_TITLES[YEARLY].capitalize(), PayCb(act="period", v=YEARLY)),
        ],
        [btn("✖ Отмена", PayCb(act="list"))],
    )
