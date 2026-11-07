import math
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from aiosqlite import Connection, Row

from bot.callbacks import CalCb, CatCb, FinCb, NavCb, OpCb
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


def _today(settings: Row) -> date:
    return tu.local_today(settings["tz"])


def _op_name(kind: str, amount: int, category: str, currency: str) -> str:
    """«➖ 1 500 ₸ · 🚕 Транспорт» — операция в журнале действий."""
    return f"{views.KIND_SIGNS[kind]} {fmt_money(amount, currency)} · {clip(category)}"


# --- экраны ---
async def open_menu(event: Message | CallbackQuery, db: Connection, settings: Row) -> None:
    uid, today = settings["user_id"], _today(settings)
    start, end = reports.month_range(today)
    balance = await repo.balance(db, uid, settings["start_balance"])
    income, expense = await repo.totals(db, uid, start, end)
    month_title = f"{MONTHS[today.month - 1]} {today.year}"
    await show(
        event, *views.finance_menu(balance, income, expense, month_title, settings["month_limit"], settings["currency"])
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


async def show_categories(event: Message | CallbackQuery, db: Connection, settings: Row, kind: str) -> None:
    uid = settings["user_id"]
    active = await repo.categories(db, uid, kind)
    archived = await repo.categories(db, uid, kind, archived=True)
    await show(event, *views.categories_view(kind, active, archived))


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
) -> None:
    """Записывает операцию за сегодня и показывает её карточку с предупреждениями о лимитах."""
    uid, currency, today = settings["user_id"], settings["currency"], _today(settings)
    category = await repo.category(db, uid, category_id)
    start, end = reports.month_range(today)
    before_total = await repo.spent(db, uid, start, end)
    before_cat = await repo.spent(db, uid, start, end, category_id)
    op_id = await repo.add_op(db, uid, kind, amount, category_id, comment, today, tu.utcnow())
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
    await show_op(event, db, settings, op_id, notes, "✅ Записал")


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


@router.message(FinForm.cat_name, F.text)
async def got_category_name(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    data = await state.get_data()
    await state.clear()
    name, uid = message.text.strip()[:40], settings["user_id"]
    if data.get("category_id"):
        old = await repo.category(db, uid, data["category_id"])
        await repo.rename_category(db, data["category_id"], name)
        await journal.add(db, uid, f"✏️ Категория {quote(old['name'])} → {quote(name)}")
    else:
        await repo.add_category(db, uid, data["kind"], name)
        kind = "расходов" if data["kind"] == repo.EXPENSE else "доходов"
        await journal.add(db, uid, f"🗂 Новая категория {kind} {quote(name)}")
    await show_categories(message, db, settings, data["kind"])


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
