from collections.abc import Mapping, Sequence
from datetime import date

from aiogram.types import InlineKeyboardMarkup

from bot.callbacks import AccCb, CatCb, FinCb, NavCb, OpCb, PayCb, QuickCb
from bot.keyboards.common import btn, chunks, kb, nav_row
from bot.repo.finance import EXPENSE, INCOME
from bot.services import reports
from bot.services.reports import PERIODS, limit_progress
from bot.services.texts import bar, clip, esc, fmt_date, fmt_money, plural
from bot.services.timeutil import pack_day

KIND_TITLES = {EXPENSE: "Расход", INCOME: "Доход"}
KIND_SIGNS = {EXPENSE: "➖", INCOME: "➕"}


def finance_menu(
    balance: int,
    accounts: Sequence[tuple[str, int]],
    income: int,
    expense: int,
    month_title: str,
    month_limit: int | None,
    currency: str,
) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["💰 <b>Финансы</b>", f"Баланс: <b>{fmt_money(balance, currency)}</b>"]
    if len(accounts) > 1:
        lines += [f"{esc(name)}: {fmt_money(amount, currency)}" for name, amount in accounts]
    lines += [
        "",
        f"<b>{month_title}</b>",
        f"Доходы: {fmt_money(income, currency)}",
        f"Расходы: {fmt_money(expense, currency)}",
        f"Итог: {fmt_money(income - expense, currency, sign=True)}",
    ]
    if month_limit:
        lines += ["", "🎯 Лимит на месяц: " + limit_progress(expense, month_limit, currency)]
    markup = kb(
        [btn("➖ Расход", FinCb(act="add", v=EXPENSE)), btn("➕ Доход", FinCb(act="add", v=INCOME))],
        [btn("📊 Отчёт", FinCb(act="report", v="month")), btn("🧾 История", FinCb(act="history"))],
        [btn("👛 Счета", AccCb(act="list")), btn("🔁 Перевод", AccCb(act="transfer"))],
        [btn("📅 Регулярные платежи", PayCb(act="list"))],
        [btn("🎯 Лимиты", FinCb(act="limits")), btn("🗂 Категории", FinCb(act="cats", v=EXPENSE))],
        [btn("📤 Выгрузка в Excel/CSV", FinCb(act="export"))],
    )
    return "\n".join(lines), markup


def amount_prompt(kind: str) -> str:
    what = "расхода" if kind == EXPENSE else "дохода"
    return f"{KIND_SIGNS[kind]} Сумма {what}?\nНапиши число, например <code>1500</code> или <code>2 350,50</code>."


def pick_category_kb(
    categories: Sequence[Mapping], kind: str | None = None, quick_mid: int | None = None
) -> InlineKeyboardMarkup:
    rows = chunks([btn(c["name"], CatCb(act="pick", id=c["id"])) for c in categories], 2)
    if quick_mid:
        # Быстрый ввод: сумму могли понять не так — даём сменить вид или сделать из текста задачу.
        other = "➕ Это доход" if kind == EXPENSE else "➖ Это расход"
        rows.append(
            [btn(other, QuickCb(act="kind", mid=quick_mid)), btn("📋 Это задача", QuickCb(act="task", mid=quick_mid))]
        )
        rows.append(nav_row(back=False))
    else:
        rows.append(nav_row())
    return kb(*rows)


def op_line(op: Mapping, currency: str, today: date) -> str:
    comment = f" — {op['comment']}" if op["comment"] else ""
    day = fmt_date(date.fromisoformat(op["op_date"]), today)
    return f"{day} {KIND_SIGNS[op['kind']]} {fmt_money(op['amount'], currency)} · {op['category']}{comment}"


def op_card(op: Mapping, currency: str, today: date, notes: Sequence[str] = ()) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        f"{KIND_SIGNS[op['kind']]} <b>{KIND_TITLES[op['kind']]}: {fmt_money(op['amount'], currency)}</b>",
        f"🗂 {esc(op['category'])}",
        f"📅 {fmt_date(date.fromisoformat(op['op_date']), today)}",
    ]
    if op["account"]:
        lines.append(f"👛 {esc(op['account'])}")
    if op["comment"]:
        lines.append(f"💬 {esc(op['comment'])}")
    if notes:
        lines += ["", *notes]
    markup = kb(
        [btn("📅 Другая дата", OpCb(act="date", id=op["id"])), btn("🗂 Категория", OpCb(act="cat", id=op["id"]))],
        [btn("👛 Счёт", OpCb(act="acc", id=op["id"])), btn("🗑 Удалить", OpCb(act="del", id=op["id"]))],
        [btn(f"{KIND_SIGNS[op['kind']]} Ещё", FinCb(act="add", v=op["kind"])), btn("◀ Финансы", FinCb(act="menu"))],
    )
    return "\n".join(lines), markup


def op_account_kb(op_id: int, accounts: Sequence[Mapping]) -> InlineKeyboardMarkup:
    rows = chunks([btn(a["name"], OpCb(act="setacc", id=op_id, v=str(a["id"]))) for a in accounts], 2)
    rows.append([btn("◀ Назад", OpCb(act="open", id=op_id))])
    return kb(*rows)


# --- счета ---
def accounts_view(
    active: Sequence[Mapping], archived: Sequence[Mapping], balances: Mapping[int, int], default_id: int, currency: str
) -> tuple[str, InlineKeyboardMarkup]:
    total = sum(balances.get(a["id"], 0) for a in (*active, *archived))
    lines = ["👛 <b>Счета</b>", f"Всего: <b>{fmt_money(total, currency)}</b>", ""]
    lines += [
        f"{'⭐ ' if a['id'] == default_id else ''}{esc(a['name'])}: {fmt_money(balances.get(a['id'], 0), currency)}"
        for a in active
    ]
    lines += ["", "⭐ — основной: на него записываются новые операции. Счёт операции меняется в её карточке."]
    rows = chunks([btn(a["name"], AccCb(act="open", id=a["id"])) for a in active], 2)
    if archived:
        rows += chunks([btn(f"🗄 {a['name']}", AccCb(act="open", id=a["id"])) for a in archived], 2)
    rows.append([btn("➕ Новый счёт", AccCb(act="new")), btn("🔁 Перевод", AccCb(act="transfer"))])
    rows.append([btn("🧾 Переводы", AccCb(act="transfers")), btn("◀ Финансы", FinCb(act="menu"))])
    return "\n".join(lines), kb(*rows)


def account_card(account: Mapping, balance: int, is_default: bool, currency: str) -> tuple[str, InlineKeyboardMarkup]:
    hidden = bool(account["archived"])
    lines = [
        f"👛 <b>{esc(account['name'])}</b>" + (" ⭐ основной" if is_default else ""),
        f"Баланс: <b>{fmt_money(balance, currency)}</b>",
        f"Начальный баланс: {fmt_money(account['start_balance'], currency)}",
    ]
    if hidden:
        lines.append("Скрыт: не показывается в списках, операции и баланс остаются.")
    aid = account["id"]
    toggle = btn("👁 Вернуть", AccCb(act="restore", id=aid)) if hidden else btn("🗄 Скрыть", AccCb(act="archive", id=aid))
    return "\n".join(lines), kb(
        [btn("✏️ Переименовать", AccCb(act="rename", id=aid)), btn("💼 Начальный баланс", AccCb(act="start", id=aid))],
        [btn("⭐ Сделать основным", AccCb(act="default", id=aid))] if not is_default and not hidden else [],
        [toggle] if not is_default else [],
        [btn("◀ Счета", AccCb(act="list"))],
    )


def transfers_view(transfers: Sequence[Mapping], currency: str, today: date) -> tuple[str, InlineKeyboardMarkup]:
    text = "🧾 <b>Последние переводы</b>\nНажми, чтобы удалить ошибочный." if transfers else "🧾 Переводов пока нет."
    rows = [
        [
            btn(
                clip(
                    f"{fmt_date(date.fromisoformat(t['op_date']), today)} · {fmt_money(t['amount'], currency)} · "
                    f"{t['from_name']} → {t['to_name']}",
                    60,
                ),
                AccCb(act="tdel", id=t["id"]),
            )
        ]
        for t in transfers
    ]
    rows.append([btn("◀ Счета", AccCb(act="list"))])
    return text, kb(*rows)


def transfer_pick_kb(accounts: Sequence[Mapping], act: str, from_id: int = 0) -> InlineKeyboardMarkup:
    """Выбор счёта для перевода: act = tfrom (откуда) или tto (куда, from_id уже выбран)."""
    choices = [a for a in accounts if a["id"] != from_id]
    rows = chunks([btn(a["name"], AccCb(act=act, id=a["id"], v=str(from_id or ""))) for a in choices], 2)
    rows.append([btn("✖ Отмена", AccCb(act="list"))])
    return kb(*rows)


def op_category_kb(op_id: int, categories: Sequence[Mapping]) -> InlineKeyboardMarkup:
    rows = chunks([btn(c["name"], OpCb(act="setcat", id=op_id, v=str(c["id"]))) for c in categories], 2)
    rows.append([btn("◀ Назад", OpCb(act="open", id=op_id))])
    return kb(*rows)


def op_date_kb(op_id: int) -> InlineKeyboardMarkup:
    return kb(
        [
            btn("Сегодня", OpCb(act="setdate", id=op_id, v="0")),
            btn("Вчера", OpCb(act="setdate", id=op_id, v="1")),
            btn("Позавчера", OpCb(act="setdate", id=op_id, v="2")),
        ],
        [btn("📅 Календарь", OpCb(act="setdate", id=op_id, v="cal"))],
        [btn("◀ Назад", OpCb(act="open", id=op_id))],
    )


def report(
    title: str,
    income: int,
    expense: int,
    expense_by_cat: Sequence[Mapping],
    income_by_cat: Sequence[Mapping],
    currency: str,
    limits: Sequence[str] = (),
) -> str:
    lines = [
        f"📊 <b>{title}</b>",
        f"Доходы: {fmt_money(income, currency)}",
        f"Расходы: {fmt_money(expense, currency)}",
        f"Итог: <b>{fmt_money(income - expense, currency, sign=True)}</b>",
    ]
    if expense_by_cat:
        lines += ["", "<b>Расходы по категориям</b>"]
        for row in expense_by_cat:
            share = row["total"] / expense if expense else 0
            lines.append(f"{esc(row['name'])} — {fmt_money(row['total'], currency)} ({round(share * 100)}%)")
            lines.append(bar(share))
    if income_by_cat:
        lines += ["", "<b>Доходы по категориям</b>"]
        lines += [f"{esc(row['name'])} — {fmt_money(row['total'], currency)}" for row in income_by_cat]
    if not expense_by_cat and not income_by_cat:
        lines += ["", "За этот период операций нет."]
    if limits:
        lines += ["", "<b>🎯 Лимиты этого месяца</b>", *limits]
    return "\n".join(lines)


REPORT_PERIODS = ("today", "week", "month", "year", "all")


def report_kb(current: str, anchor: date, today: date) -> InlineKeyboardMarkup:
    """Вкладки периодов и стрелки ◀ ▶ к соседнему периоду (в будущее — нельзя)."""
    tabs = [btn(("• " if key == current else "") + PERIODS[key], FinCb(act="report", v=key)) for key in REPORT_PERIODS]
    arrows = []
    if current in reports.NAVIGABLE:
        arrows.append(btn("◀", FinCb(act="report", v=current, id=pack_day(reports.shift(current, anchor, -1)))))
        if reports.has_next(current, anchor, today):
            arrows.append(btn("▶", FinCb(act="report", v=current, id=pack_day(reports.shift(current, anchor, 1)))))
    return kb(tabs[:3], tabs[3:], arrows, [btn("◀ Финансы", FinCb(act="menu"))])


def history(
    ops: Sequence[Mapping], page: int, pages: int, currency: str, today: date
) -> tuple[str, InlineKeyboardMarkup]:
    text = "🧾 <b>История операций</b>" + (f"\nСтраница {page + 1} из {pages}" if pages > 1 else "")
    if not ops:
        text += "\nОпераций пока нет."
    rows = [[btn(clip(op_line(op, currency, today), 60), OpCb(act="open", id=op["id"]))] for op in ops]
    paging = []
    if page > 0:
        paging.append(btn("◀", FinCb(act="history", id=page - 1)))
    if page + 1 < pages:
        paging.append(btn("▶", FinCb(act="history", id=page + 1)))
    rows.append(paging)
    rows.append([btn("◀ Финансы", FinCb(act="menu"))])
    return text, kb(*rows)


def limits_view(
    month_limit: int | None,
    month_spent: int,
    categories: Sequence[Mapping],
    spent_by_cat: Mapping[int, int],
    currency: str,
) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["🎯 <b>Лимиты на месяц</b>", "Предупрежу, когда потрачено 80% и 100%.", ""]
    if month_limit:
        lines.append("Общий: " + limit_progress(month_spent, month_limit, currency))
    else:
        lines.append("Общий: не задан")
    for cat in categories:
        if cat["month_limit"]:
            lines.append(
                f"\n{esc(cat['name'])}: " + limit_progress(spent_by_cat.get(cat["id"], 0), cat["month_limit"], currency)
            )
    general = fmt_money(month_limit, currency) if month_limit else "—"
    rows = [[btn(f"Общий лимит: {general}", FinCb(act="limit", id=0))]]
    rows += chunks(
        [
            btn(
                f"{c['name']}: {fmt_money(c['month_limit'], currency) if c['month_limit'] else '—'}",
                FinCb(act="limit", id=c["id"]),
            )
            for c in categories
        ],
        2,
    )
    rows.append([btn("◀ Финансы", FinCb(act="menu"))])
    return "\n".join(lines), kb(*rows)


def limit_input_kb(has_limit: bool) -> InlineKeyboardMarkup:
    return kb(
        [btn("🗑 Убрать лимит", NavCb(action="skip"))] if has_limit else [], [btn("✖ Отмена", FinCb(act="limits"))]
    )


def categories_view(
    kind: str, active: Sequence[Mapping], archived: Sequence[Mapping]
) -> tuple[str, InlineKeyboardMarkup]:
    other = INCOME if kind == EXPENSE else EXPENSE
    title = "расходов" if kind == EXPENSE else "доходов"
    text = f"🗂 <b>Категории {title}</b>\nНажми на категорию, чтобы переименовать, скрыть или удалить."
    rows = chunks([btn(c["name"], CatCb(act="open", id=c["id"])) for c in active], 2)
    if archived:
        rows += chunks([btn(f"🗄 {c['name']}", CatCb(act="open", id=c["id"])) for c in archived], 2)
    rows.append([btn("➕ Добавить", CatCb(act="add", kind=kind))])
    rows.append(
        [
            btn("Категории доходов" if other == INCOME else "Категории расходов", FinCb(act="cats", v=other)),
            btn("◀ Финансы", FinCb(act="menu")),
        ]
    )
    return text, kb(*rows)


def category_card(category: Mapping) -> tuple[str, InlineKeyboardMarkup]:
    hidden = bool(category["archived"])
    text = f"🗂 <b>{esc(category['name'])}</b>" + (
        "\nСкрыта: не показывается при добавлении операций." if hidden else ""
    )
    toggle = (
        btn("👁 Вернуть", CatCb(act="restore", id=category["id"]))
        if hidden
        else btn("🗄 Скрыть", CatCb(act="archive", id=category["id"]))
    )
    return text, kb(
        [btn("✏️ Переименовать", CatCb(act="rename", id=category["id"])), toggle],
        [btn("🗑 Удалить", CatCb(act="delete", id=category["id"]))],
        [btn("◀ Категории", FinCb(act="cats", v=category["kind"]))],
    )


def category_move_view(
    category: Mapping, ops: int, pays: int, targets: Sequence[Mapping]
) -> tuple[str, InlineKeyboardMarkup]:
    """Удаление категории, в которой есть записи: сначала выбрать, куда их перенести."""
    used = []
    if ops:
        used.append(f"{ops} {plural(ops, ('операция', 'операции', 'операций'))}")
    if pays:
        used.append(f"{pays} {plural(pays, ('регулярный платёж', 'регулярных платежа', 'регулярных платежей'))}")
    text = (
        f"🗑 <b>Удалить {esc(category['name'])}</b>\n"
        f"В ней {' и '.join(used)}. Куда их перенести? После переноса категория удалится."
    )
    rows = chunks([btn(c["name"], CatCb(act="into", id=c["id"])) for c in targets], 2)
    rows.append([btn("✖ Отмена", CatCb(act="open", id=category["id"]))])
    return text, kb(*rows)


def export_kb() -> InlineKeyboardMarkup:
    periods = ["month", "prev", "year", "all"]
    return kb(
        *[
            [btn(f"{PERIODS[p]} · Excel", FinCb(act="xlsx", v=p)), btn(f"{PERIODS[p]} · CSV", FinCb(act="csv", v=p))]
            for p in periods
        ],
        [btn("◀ Финансы", FinCb(act="menu"))],
    )
