import math
import sqlite3
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from aiosqlite import Connection, Row

from bot.callbacks import AccCb, CalCb, CatCb, FinCb, NavCb, OpCb
from bot.handlers.ui import show
from bot.keyboards.calendar import PAST, calendar_kb, picked_date
from bot.keyboards.common import btn, confirm_kb, kb, nav_row
from bot.repo import finance as repo
from bot.repo import journal
from bot.repo import settings as settings_repo
from bot.services import export, reports
from bot.services import timeutil as tu
from bot.services.texts import MONTHS, clip, esc, fmt_date_long, fmt_money, parse_amount, plural, quote
from bot.views import finance as views

router = Router(name="finance")

PAGE_SIZE = 10
BACK_TO_FINANCE = kb([btn("◀ Финансы", FinCb(act="menu"))])


class FinForm(StatesGroup):
    amount = State()
    category = State()
    comment = State()
    op_date = State()
    limit = State()
    cat_name = State()
    cat_move = State()


class AccForm(StatesGroup):
    name = State()
    start = State()
    transfer_amount = State()


def _today(settings: Row) -> date:
    return tu.local_today(settings["tz"])


def _op_name(kind: str, amount: int, category: str, currency: str) -> str:
    """«➖ 1 500 ₸ · 🚕 Транспорт» — операция в журнале действий."""
    return f"{views.KIND_SIGNS[kind]} {fmt_money(amount, currency)} · {clip(category)}"


# --- экраны ---
async def open_menu(event: Message | CallbackQuery, db: Connection, settings: Row) -> None:
    uid, today = settings["user_id"], _today(settings)
    start, end = reports.month_range(today)
    balances = await repo.account_balances(db, uid)
    accounts = [(a["name"], balances.get(a["id"], 0)) for a in await repo.accounts(db, uid)]
    income, expense = await repo.totals(db, uid, start, end)
    month_title = f"{MONTHS[today.month - 1]} {today.year}"
    await show(
        event,
        *views.finance_menu(accounts, income, expense, month_title, settings["month_limit"], settings["currency"]),
    )


async def show_report(
    event: Message | CallbackQuery, db: Connection, settings: Row, period: str, anchor: date | None = None
) -> None:
    uid, currency, today = settings["user_id"], settings["currency"], _today(settings)
    anchor = anchor or today
    start, end, title = reports.period_range(period, today, anchor)
    income, expense = await repo.totals(db, uid, start, end)
    expense_by_cat = await repo.by_category(db, uid, repo.EXPENSE, start, end)
    income_by_cat = await repo.by_category(db, uid, repo.INCOME, start, end)
    limits = []
    if period == "month" and start <= today <= end:
        if settings["month_limit"]:
            limits.append("Общий: " + reports.limit_progress(expense, settings["month_limit"], currency))
        limits += [
            f"{esc(row['name'])}: " + reports.limit_progress(row["total"], row["month_limit"], currency)
            for row in expense_by_cat
            if row["month_limit"]
        ]
    text = views.report(title, income, expense, expense_by_cat, income_by_cat, currency, limits)
    await show(event, text, views.report_kb(period, anchor, today))


async def show_history(event: Message | CallbackQuery, db: Connection, settings: Row, page: int) -> None:
    uid = settings["user_id"]
    pages = max(1, math.ceil(await repo.count_ops(db, uid) / PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    ops = await repo.recent_ops(db, uid, page * PAGE_SIZE, PAGE_SIZE)
    await show(event, *views.history(ops, page, pages, settings["currency"], _today(settings)))


async def show_limits(event: Message | CallbackQuery, db: Connection, settings: Row) -> None:
    uid = settings["user_id"]
    start, end = reports.month_range(_today(settings))
    month_spent = await repo.spent(db, uid, start, end)
    categories = await repo.categories(db, uid, repo.EXPENSE)
    by_cat = {row["id"]: row["total"] for row in await repo.by_category(db, uid, repo.EXPENSE, start, end)}
    await show(
        event, *views.limits_view(settings["month_limit"], month_spent, categories, by_cat, settings["currency"])
    )


async def show_categories(
    event: Message | CallbackQuery, db: Connection, settings: Row, kind: str, header: str | None = None
) -> None:
    uid = settings["user_id"]
    active = await repo.categories(db, uid, kind)
    archived = await repo.categories(db, uid, kind, archived=True)
    text, markup = views.categories_view(kind, active, archived)
    await show(event, f"{header}\n\n{text}" if header else text, markup)


async def show_op(
    event: Message | CallbackQuery,
    db: Connection,
    settings: Row,
    op_id: int,
    notes: list[str] | None = None,
    header: str | None = None,
) -> None:
    op = await repo.op(db, settings["user_id"], op_id)
    if not op:
        await show(event, "Операция не найдена — возможно, её удалили.", BACK_TO_FINANCE)
        return
    text, markup = views.op_card(op, settings["currency"], _today(settings), notes or [])
    await show(event, f"{header}\n\n{text}" if header else text, markup)


# --- главное меню финансов ---
@router.callback_query(FinCb.filter())
async def fin_action(
    callback: CallbackQuery,
    callback_data: FinCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    act = callback_data.act
    await state.clear()
    if act == "menu":
        await open_menu(callback, db, settings)
    elif act == "add":
        await start_add(callback, state, callback_data.v)
    elif act == "report":
        anchor = tu.unpack_day(callback_data.id) if callback_data.id else None
        await show_report(callback, db, settings, callback_data.v or "month", anchor)
    elif act == "history":
        await show_history(callback, db, settings, callback_data.id)
    elif act == "limits":
        await show_limits(callback, db, settings)
    elif act == "limit":
        await ask_limit(callback, state, db, settings, callback_data.id)
    elif act == "cats":
        await show_categories(callback, db, settings, callback_data.v or repo.EXPENSE)
    elif act == "export":
        await show(callback, "📤 <b>Выгрузка</b>\nВыбери период и формат — пришлю файл.", views.export_kb())
    elif act in ("xlsx", "csv"):
        await send_export(callback, db, settings, callback_data.v, act, callback_answer)


# --- добавление операции ---
async def start_add(event: Message | CallbackQuery, state: FSMContext, kind: str) -> None:
    await state.set_state(FinForm.amount)
    await state.update_data(kind=kind)
    await show(event, views.amount_prompt(kind), kb(nav_row(back=False)))


async def ask_category(event: Message | CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    data = await state.get_data()
    await state.set_state(FinForm.category)
    kind = data["kind"]
    categories = await repo.categories(db, settings["user_id"], kind)
    text = f"{views.KIND_SIGNS[kind]} {fmt_money(data['amount'], settings['currency'])}"
    if data.get("comment"):
        text += f" — {esc(data['comment'])}"
    text += "\n🗂 <b>Категория?</b>"
    if data.get("quick_mid"):
        text += "\nЗапомню выбор: в следующий раз такую запись сохраню сразу."
    await show(event, text, views.pick_category_kb(categories, kind, data.get("quick_mid")))


@router.message(FinForm.amount, F.text)
async def got_amount(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("Не понял сумму. Напиши число, например <code>1500</code> или <code>2 350,50</code>.")
        return
    await state.update_data(amount=amount)
    await ask_category(message, state, db, settings)


@router.callback_query(CatCb.filter(F.act == "pick"), FinForm.category)
async def got_category(
    callback: CallbackQuery,
    callback_data: CatCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    data = await state.get_data()
    category = await repo.category(db, settings["user_id"], callback_data.id)
    if not category or category["archived"] or category["kind"] != data.get("kind"):
        callback_answer.text = "Эта категория недоступна"
        return
    if data.get("quick_mid"):
        # Комментарий уже есть — из быстрого ввода.
        await state.clear()
        await record_op(callback, db, settings, data["kind"], data["amount"], category["id"], data.get("comment"))
        return
    await state.update_data(category_id=category["id"])
    await state.set_state(FinForm.comment)
    await show(
        callback,
        "💬 <b>Комментарий?</b>\nНапример: «кофе с коллегами». Или нажми «Пропустить».",
        kb(nav_row(skip=True)),
    )


@router.message(FinForm.comment, F.text)
async def got_comment(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await save_op(message, state, db, settings, message.text.strip()[:300])


@router.callback_query(NavCb.filter(F.action == "skip"), FinForm.comment)
async def skip_comment(callback: CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    await save_op(callback, state, db, settings, None)


async def save_op(
    event: Message | CallbackQuery, state: FSMContext, db: Connection, settings: Row, comment: str | None
) -> None:
    data = await state.get_data()
    await state.clear()
    await record_op(event, db, settings, data["kind"], data["amount"], data["category_id"], comment)


async def record_op(
    event: Message | CallbackQuery,
    db: Connection,
    settings: Row,
    kind: str,
    amount: int,
    category_id: int,
    comment: str | None,
    account_id: int | None = None,
    header: str = "✅ Записал",
) -> None:
    """Записывает операцию за сегодня и показывает её карточку с предупреждениями о лимитах.

    Счёт — основной из настроек, если не передан другой.
    """
    uid, currency, today = settings["user_id"], settings["currency"], _today(settings)
    category = await repo.category(db, uid, category_id)
    start, end = reports.month_range(today)
    before_total = await repo.spent(db, uid, start, end)
    before_cat = await repo.spent(db, uid, start, end, category_id)
    op_id = await repo.add_op(
        db, uid, kind, amount, category_id, comment, today, tu.utcnow(), account_id or settings["default_account_id"]
    )
    sign, title = views.KIND_SIGNS[kind], views.KIND_TITLES[kind]
    entry = f"{sign} {title} {fmt_money(amount, currency)} · {clip(category['name'])}"
    await journal.add(db, uid, entry + (f" — {clip(comment)}" if comment else ""))

    notes = []
    if kind == repo.EXPENSE:
        limits = [
            (before_total, settings["month_limit"], "Общий лимит", "на месяц"),
            (before_cat, category["month_limit"], esc(category["name"]), f"по «{esc(category['name'])}»"),
        ]
        for before, limit, label, short in limits:
            if not limit:
                continue
            alert = reports.limit_alert(before, before + amount, limit, label, currency)
            if alert:
                notes.append(alert)
            elif before + amount < limit:
                notes.append(f"🎯 Осталось {short}: {fmt_money(limit - before - amount, currency)}")
    await show_op(event, db, settings, op_id, notes, header)


@router.callback_query(NavCb.filter(F.action == "back"), StateFilter(FinForm))
async def back(callback: CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    current = await state.get_state()
    data = await state.get_data()
    if current == FinForm.category.state:
        await start_add(callback, state, data["kind"])
    elif current == FinForm.comment.state:
        await ask_category(callback, state, db, settings)
    elif current == FinForm.op_date.state:
        await state.clear()
        await show_op(callback, db, settings, data["op_id"])


# --- операция: дата, удаление ---
@router.callback_query(OpCb.filter())
async def op_action(
    callback: CallbackQuery, callback_data: OpCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    await state.clear()
    op = await repo.op(db, settings["user_id"], callback_data.id)
    if not op:
        await show(callback, "Операция не найдена — возможно, её удалили.", BACK_TO_FINANCE)
        return
    act, today = callback_data.act, _today(settings)
    if act == "open":
        await show_op(callback, db, settings, op["id"])
    elif act == "date":
        await show(callback, "📅 <b>Когда это было?</b>", views.op_date_kb(op["id"]))
    elif act == "cat":
        categories = await repo.categories(db, settings["user_id"], op["kind"])
        await show(callback, "🗂 <b>Другая категория</b>", views.op_category_kb(op["id"], categories))
    elif act == "setcat":
        category = await repo.category(db, settings["user_id"], int(callback_data.v or 0))
        if category and not category["archived"] and category["kind"] == op["kind"]:
            await repo.set_op_category(db, op["id"], category["id"])
            name = _op_name(op["kind"], op["amount"], op["category"], settings["currency"])
            await journal.add(db, settings["user_id"], f"🗂 {name} → {clip(category['name'])}")
        await show_op(callback, db, settings, op["id"], header="🗂 Категория изменена")
    elif act == "acc":
        accounts = await repo.accounts(db, settings["user_id"])
        await show(callback, "👛 <b>С какого счёта?</b>", views.op_account_kb(op["id"], accounts))
    elif act == "setacc":
        account = await repo.account(db, settings["user_id"], int(callback_data.v or 0))
        if account and not account["archived"]:
            await repo.set_op_account(db, op["id"], account["id"])
            name = _op_name(op["kind"], op["amount"], op["category"], settings["currency"])
            await journal.add(db, settings["user_id"], f"👛 {name} → {quote(account['name'])}")
        await show_op(callback, db, settings, op["id"], header="👛 Счёт изменён")
    elif act == "setdate" and callback_data.v == "cal":
        await state.set_state(FinForm.op_date)
        await state.update_data(op_id=op["id"])
        await show(callback, "📅 <b>Выбери дату</b>", calendar_kb(today.year, today.month, today, PAST))
    elif act == "setdate":
        await _set_op_date(db, settings, op, today - timedelta(days=int(callback_data.v)))
        await show_op(callback, db, settings, op["id"], header="📅 Дата изменена")
    elif act == "del":
        sign = views.KIND_SIGNS[op["kind"]]
        await show(
            callback,
            f"🗑 Удалить операцию {sign} {fmt_money(op['amount'], settings['currency'])} · {esc(op['category'])}?",
            confirm_kb(OpCb(act="delok", id=op["id"]), OpCb(act="open", id=op["id"])),
        )
    elif act == "delok":
        await repo.delete_op(db, op["id"])
        name = _op_name(op["kind"], op["amount"], op["category"], settings["currency"])
        await journal.add(db, settings["user_id"], f"🗑 Удалена операция {name}")
        await show(
            callback,
            "🗑 Операция удалена.",
            kb([btn("🧾 История", FinCb(act="history")), btn("◀ Финансы", FinCb(act="menu"))]),
        )


@router.callback_query(CalCb.filter(F.action == "pick"), FinForm.op_date)
async def got_op_date(
    callback: CallbackQuery, callback_data: CalCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    op_id = (await state.get_data())["op_id"]
    await state.clear()
    op = await repo.op(db, settings["user_id"], op_id)
    if op:
        await _set_op_date(db, settings, op, picked_date(callback_data))
    await show_op(callback, db, settings, op_id, header="📅 Дата изменена")


async def _set_op_date(db: Connection, settings: Row, op: Row, day: date) -> None:
    await repo.set_op_date(db, op["id"], day)
    name = _op_name(op["kind"], op["amount"], op["category"], settings["currency"])
    await journal.add(db, settings["user_id"], f"📅 {name}: дата {fmt_date_long(day)}")


# --- лимиты ---
async def ask_limit(event: CallbackQuery, state: FSMContext, db: Connection, settings: Row, category_id: int) -> None:
    if category_id:
        category = await repo.category(db, settings["user_id"], category_id)
        if not category:
            await show(event, "Категория не найдена.", BACK_TO_FINANCE)
            return
        title, current = f"категории {esc(category['name'])}", category["month_limit"]
    else:
        title, current = "на все расходы", settings["month_limit"]
    await state.set_state(FinForm.limit)
    await state.update_data(category_id=category_id)
    now_text = fmt_money(current, settings["currency"]) if current else "не задан"
    text = f"🎯 <b>Лимит {title} на месяц</b>\nСейчас: {now_text}\n\nНапиши новую сумму, например <code>200000</code>."
    await show(event, text, views.limit_input_kb(bool(current)))


async def _save_limit(
    event: Message | CallbackQuery, state: FSMContext, db: Connection, settings: Row, value: int | None
) -> None:
    category_id = (await state.get_data()).get("category_id")
    await state.clear()
    if category_id:
        await repo.set_category_limit(db, category_id, value)
        category = await repo.category(db, settings["user_id"], category_id)
        title = f"Лимит {quote(category['name'])}"
    else:
        await settings_repo.update(db, settings["user_id"], month_limit=value)
        title = "Общий лимит"
    amount = fmt_money(value, settings["currency"]) if value else "убран"
    await journal.add(db, settings["user_id"], f"🎯 {title} на месяц: {amount}")
    await show_limits(event, db, await settings_repo.get(db, settings["user_id"]))


@router.message(FinForm.limit, F.text)
async def got_limit(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    value = parse_amount(message.text, allow_zero=True)
    if value is None:
        await message.answer("Не понял сумму. Напиши число, например <code>200000</code>, или 0, чтобы убрать лимит.")
        return
    await _save_limit(message, state, db, settings, value or None)


@router.callback_query(NavCb.filter(F.action == "skip"), FinForm.limit)
async def remove_limit(callback: CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    await _save_limit(callback, state, db, settings, None)


# --- категории ---
STALE_MOVE = "Кнопка устарела — нажми «Удалить» ещё раз"


async def delete_category(
    callback: CallbackQuery,
    state: FSMContext,
    db: Connection,
    settings: Row,
    category: Row,
    callback_answer: CallbackAnswer,
    confirmed: bool,
) -> None:
    uid = settings["user_id"]
    active = await repo.categories(db, uid, category["kind"])
    if not category["archived"] and len(active) <= 1:
        callback_answer.text = "Должна остаться хотя бы одна категория"
        return
    ops, pays = await repo.category_usage(db, category["id"])
    if not ops and not pays:
        if not confirmed:
            await show(
                callback,
                f"🗑 Удалить категорию {esc(category['name'])}?",
                confirm_kb(CatCb(act="delok", id=category["id"]), CatCb(act="open", id=category["id"])),
            )
            return
        if await repo.delete_category(db, category["id"]):
            await journal.add(db, uid, f"🗑 Удалена категория {quote(category['name'])}")
            await show_categories(callback, db, settings, category["kind"], header="🗑 Категория удалена")
            return
        if not await repo.category(db, uid, category["id"]):  # второе нажатие «Да, удалить»
            await show_categories(callback, db, settings, category["kind"], header="🗑 Категория уже удалена")
            return
        ops, pays = await repo.category_usage(db, category["id"])  # записи появились, пока подтверждали
    await state.set_state(FinForm.cat_move)
    # Кнопка переноса несёт только цель: какую категорию удаляем, берём из состояния, но только для того
    # сообщения, где показан этот экран. Иначе кнопка со старого экрана удалила бы другую категорию.
    await state.update_data(category_id=category["id"], move_mid=callback.message.message_id)
    targets = [c for c in active if c["id"] != category["id"]]
    await show(callback, *views.category_move_view(category, ops, pays, targets))


# Раньше category_action: тот ловит любые кнопки категорий, кроме «pick».
@router.callback_query(CatCb.filter(F.act == "into"), FinForm.cat_move)
async def got_move_target(
    callback: CallbackQuery,
    callback_data: CatCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    data = await state.get_data()
    if data.get("move_mid") != callback.message.message_id:
        callback_answer.text = STALE_MOVE
        return
    await state.clear()
    uid, source_id = settings["user_id"], data.get("category_id") or 0
    source = await repo.category(db, uid, source_id)
    target = await repo.category(db, uid, callback_data.id)
    if not source or not target or target["archived"] or target["kind"] != source["kind"] or target["id"] == source_id:
        await show(callback, "Категория не найдена.", BACK_TO_FINANCE)
        return
    if not await repo.move_category(db, source["id"], target["id"]):
        if await repo.category(db, uid, source_id):
            await show(callback, *views.category_card(source))
        else:
            await show_categories(callback, db, settings, source["kind"], header="🗑 Категория уже удалена")
        return
    await journal.add(db, uid, f"🗑 Удалена категория {quote(source['name'])}, записи → {quote(target['name'])}")
    header = f"🗑 Категория {esc(source['name'])} удалена, записи перенесены в {esc(target['name'])}"
    await show_categories(callback, db, settings, source["kind"], header=header)


@router.callback_query(CatCb.filter(F.act != "pick"))
async def category_action(
    callback: CallbackQuery,
    callback_data: CatCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    await state.clear()
    uid, act = settings["user_id"], callback_data.act
    if act == "add":
        await state.set_state(FinForm.cat_name)
        await state.update_data(category_id=None, kind=callback_data.kind)
        await show(
            callback,
            "➕ <b>Новая категория</b>\nНапиши название, можно с эмодзи: «☕ Кофе».",
            kb([btn("✖ Отмена", FinCb(act="cats", v=callback_data.kind))]),
        )
        return
    category = await repo.category(db, uid, callback_data.id)
    if not category:
        await show(callback, "Категория не найдена.", BACK_TO_FINANCE)
        return
    if act == "open":
        await show(callback, *views.category_card(category))
    elif act == "rename":
        await state.set_state(FinForm.cat_name)
        await state.update_data(category_id=category["id"], kind=category["kind"])
        await show(
            callback,
            f"✏️ Новое название для «{esc(category['name'])}»:",
            kb([btn("✖ Отмена", CatCb(act="open", id=category["id"]))]),
        )
    elif act == "archive":
        if len(await repo.categories(db, uid, category["kind"])) <= 1:
            callback_answer.text = "Должна остаться хотя бы одна категория"
            return
        await repo.set_category_archived(db, category["id"], True)
        await journal.add(db, uid, f"🗄 Категория {quote(category['name'])} скрыта")
        await show(callback, *views.category_card(await repo.category(db, uid, category["id"])))
    elif act == "restore":
        await repo.set_category_archived(db, category["id"], False)
        await journal.add(db, uid, f"👁 Категория {quote(category['name'])} снова в списке")
        await show(callback, *views.category_card(await repo.category(db, uid, category["id"])))
    elif act in ("delete", "delok"):
        await delete_category(callback, state, db, settings, category, callback_answer, confirmed=act == "delok")
    elif act == "into":
        callback_answer.text = STALE_MOVE


@router.message(FinForm.cat_name, F.text)
async def got_category_name(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    data = await state.get_data()
    name, uid = message.text.strip()[:40], settings["user_id"]
    same = await repo.category_named(db, uid, data["kind"], name)
    if same and same["id"] != data.get("category_id"):
        hidden = " среди скрытых" if same["archived"] else ""
        await message.answer(f"Категория {esc(name)} уже есть{hidden}. Напиши другое название.")
        return
    await state.clear()
    try:
        if data.get("category_id"):
            old = await repo.category(db, uid, data["category_id"])
            await repo.rename_category(db, data["category_id"], name)
            entry = f"✏️ Категория {quote(old['name'])} → {quote(name)}"
        else:
            await repo.add_category(db, uid, data["kind"], name)
            kind = "расходов" if data["kind"] == repo.EXPENSE else "доходов"
            entry = f"🗂 Новая категория {kind} {quote(name)}"
    except sqlite3.IntegrityError:  # то же название пришло вторым сообщением одновременно с первым
        await show_categories(message, db, settings, data["kind"])
        return
    await journal.add(db, uid, entry)
    await show_categories(message, db, settings, data["kind"])


# --- счета ---
async def show_accounts(
    event: Message | CallbackQuery, db: Connection, settings: Row, header: str | None = None
) -> None:
    uid = settings["user_id"]
    active, archived = await repo.accounts(db, uid), await repo.accounts(db, uid, archived=True)
    balances = await repo.account_balances(db, uid)
    text, markup = views.accounts_view(active, archived, balances, settings["default_account_id"], settings["currency"])
    await show(event, f"{header}\n\n{text}" if header else text, markup)


async def show_account(event: Message | CallbackQuery, db: Connection, settings: Row, account_id: int) -> None:
    uid = settings["user_id"]
    account = await repo.account(db, uid, account_id)
    if not account:
        await show(event, "Счёт не найден.", BACK_TO_FINANCE)
        return
    balance = (await repo.account_balances(db, uid)).get(account_id, 0)
    is_default = account_id == settings["default_account_id"]
    await show(event, *views.account_card(account, balance, is_default, settings["currency"]))


@router.callback_query(AccCb.filter())
async def account_action(
    callback: CallbackQuery,
    callback_data: AccCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    await state.clear()
    uid, act = settings["user_id"], callback_data.act
    if act == "list":
        await show_accounts(callback, db, settings)
        return
    if act == "new":
        await state.set_state(AccForm.name)
        await show(
            callback,
            "➕ <b>Новый счёт</b>\nНапиши название, можно с эмодзи: «🏦 Депозит».",
            kb([btn("✖ Отмена", AccCb(act="list"))]),
        )
        return
    if act == "transfers":
        transfers = await repo.recent_transfers(db, uid)
        await show(callback, *views.transfers_view(transfers, settings["currency"], _today(settings)))
        return
    if act in ("tdel", "tdelok"):
        transfer = await repo.transfer(db, uid, callback_data.id)
        if not transfer:
            callback_answer.text = "Перевод уже удалён"
            await show_accounts(callback, db, settings)
            return
        money = fmt_money(transfer["amount"], settings["currency"])
        route = f"{clip(transfer['from_name'])} → {clip(transfer['to_name'])}"
        if act == "tdel":
            await show(
                callback,
                f"🗑 Удалить перевод {money}: {esc(route)}? Балансы счетов пересчитаются.",
                confirm_kb(AccCb(act="tdelok", id=transfer["id"]), AccCb(act="transfers")),
            )
            return
        await repo.delete_transfer(db, transfer["id"])
        await journal.add(db, uid, f"🗑 Удалён перевод {money}: {route}")
        await show_accounts(callback, db, settings, "🗑 Перевод удалён")
        return
    if act == "transfer":
        accounts = await repo.accounts(db, uid)
        if len(accounts) < 2:
            callback_answer.text = "Для перевода нужно хотя бы два счёта"
            return
        await show(callback, "🔁 <b>Перевод</b>\nС какого счёта?", views.transfer_pick_kb(accounts, "tfrom"))
        return
    account = await repo.account(db, uid, callback_data.id)
    if not account:
        await show(callback, "Счёт не найден.", BACK_TO_FINANCE)
        return
    if act == "open":
        await show_account(callback, db, settings, account["id"])
    elif act == "rename":
        await state.set_state(AccForm.name)
        await state.update_data(account_id=account["id"])
        await show(
            callback,
            f"✏️ Новое название для «{esc(account['name'])}»:",
            kb([btn("✖ Отмена", AccCb(act="open", id=account["id"]))]),
        )
    elif act == "start":
        await state.set_state(AccForm.start)
        await state.update_data(account_id=account["id"])
        await show(
            callback,
            f"💼 <b>Начальный баланс «{esc(account['name'])}»</b>\n"
            f"Сейчас: {fmt_money(account['start_balance'], settings['currency'])}\n\n"
            "Сколько денег было на счёте до начала учёта? Напиши число (можно отрицательное).",
            kb([btn("✖ Отмена", AccCb(act="open", id=account["id"]))]),
        )
    elif act == "default" and not account["archived"]:
        await settings_repo.update(db, uid, default_account_id=account["id"])
        await journal.add(db, uid, f"⭐ Основной счёт: {quote(account['name'])}")
        callback_answer.text = "Теперь новые операции пишутся сюда"
        await show_account(callback, db, await settings_repo.get(db, uid), account["id"])
    elif act == "archive":
        if account["id"] == settings["default_account_id"]:
            callback_answer.text = "Основной счёт скрыть нельзя"
            return
        await repo.set_account_archived(db, account["id"], True)
        await journal.add(db, uid, f"🗄 Счёт {quote(account['name'])} скрыт")
        await show_account(callback, db, settings, account["id"])
    elif act == "restore":
        await repo.set_account_archived(db, account["id"], False)
        await journal.add(db, uid, f"👁 Счёт {quote(account['name'])} снова в списке")
        await show_account(callback, db, settings, account["id"])
    elif act == "tfrom" and not account["archived"]:
        accounts = await repo.accounts(db, uid)
        text = f"🔁 <b>Перевод</b> с «{esc(account['name'])}»\nНа какой счёт?"
        await show(callback, text, views.transfer_pick_kb(accounts, "tto", account["id"]))
    elif act == "tto" and not account["archived"]:
        source = await repo.account(db, uid, int(callback_data.v or 0))
        if not source or source["archived"] or source["id"] == account["id"]:
            callback_answer.text = "Выбери другой счёт"
            return
        await state.set_state(AccForm.transfer_amount)
        await state.update_data(from_id=source["id"], to_id=account["id"])
        await show(
            callback,
            f"🔁 «{esc(source['name'])}» → «{esc(account['name'])}»\nСколько переводишь? Например <code>20000</code>.",
            kb([btn("✖ Отмена", AccCb(act="list"))]),
        )


@router.message(AccForm.name, F.text)
async def got_account_name(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    account_id = (await state.get_data()).get("account_id")
    await state.clear()
    name, uid = message.text.strip()[:40], settings["user_id"]
    if account_id:
        old = await repo.account(db, uid, account_id)
        if old:
            await repo.rename_account(db, account_id, name)
            await journal.add(db, uid, f"✏️ Счёт {quote(old['name'])} → {quote(name)}")
        await show_account(message, db, settings, account_id)
        return
    account_id = await repo.add_account(db, uid, name)
    await journal.add(db, uid, f"👛 Новый счёт {quote(name)}")
    await show_account(message, db, settings, account_id)


@router.message(AccForm.start, F.text)
async def got_account_start(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    value = parse_amount(message.text, allow_negative=True, allow_zero=True)
    if value is None:
        await message.answer("Не понял сумму. Напиши число, например <code>150000</code>.")
        return
    account_id = (await state.get_data())["account_id"]
    await state.clear()
    account = await repo.account(db, settings["user_id"], account_id)
    if account:
        await repo.set_account_start(db, account_id, value)
        amount = fmt_money(value, settings["currency"])
        await journal.add(db, settings["user_id"], f"💼 Начальный баланс {quote(account['name'])}: {amount}")
    await show_account(message, db, settings, account_id)


@router.message(AccForm.transfer_amount, F.text)
async def got_transfer_amount(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("Не понял сумму. Напиши число, например <code>20000</code>.")
        return
    data = await state.get_data()
    await state.clear()
    uid = settings["user_id"]
    source, target = await repo.account(db, uid, data["from_id"]), await repo.account(db, uid, data["to_id"])
    if not source or not target:
        await show(message, "Счёт не найден.", BACK_TO_FINANCE)
        return
    await repo.add_transfer(db, uid, source["id"], target["id"], amount, _today(settings), tu.utcnow())
    money = fmt_money(amount, settings["currency"])
    await journal.add(db, uid, f"🔁 Перевод {money}: {clip(source['name'])} → {clip(target['name'])}")
    header = f"✅ 🔁 Перевод {money}: {esc(clip(source['name']))} → {esc(clip(target['name']))}"
    await show_accounts(message, db, settings, header)


# --- выгрузка ---
async def send_export(
    callback: CallbackQuery, db: Connection, settings: Row, period: str, fmt: str, callback_answer: CallbackAnswer
) -> None:
    start, end, title = reports.period_range(period, _today(settings))
    ops = await repo.ops_range(db, settings["user_id"], start, end)
    if not ops:
        callback_answer.text = "За этот период операций нет"
        return
    suffix = f"{start}_{end}" if start else "all"
    if fmt == "xlsx":
        content, filename = export.to_xlsx(ops, settings["currency"]), f"finance_{suffix}.xlsx"
    else:
        content, filename = export.to_csv(ops), f"finance_{suffix}.csv"
    caption = f"📤 {title}: {len(ops)} {plural(len(ops), ('операция', 'операции', 'операций'))}"
    await callback.message.answer_document(BufferedInputFile(content, filename=filename), caption=caption)
    callback_answer.text = "Файл готов"
