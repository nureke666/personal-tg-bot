"""Регулярные платежи: список, мастер добавления, «Оплачено» / «Завтра» / «Пропустить» из напоминания."""

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from aiosqlite import Connection, Row

from bot.callbacks import CalCb, PayCb
from bot.handlers import finance as finance_handlers
from bot.handlers.ui import show
from bot.keyboards.calendar import FUTURE, calendar_kb, picked_date
from bot.keyboards.common import btn, confirm_kb, kb
from bot.repo import finance, journal
from bot.repo import payments as repo
from bot.services import timeutil as tu
from bot.services.texts import esc, fmt_date, fmt_money, parse_amount, quote
from bot.views import payments as views

router = Router(name="payments")

CANCEL = kb([btn("✖ Отмена", PayCb(act="list"))])


class PayForm(StatesGroup):
    title = State()
    amount = State()
    category = State()
    period = State()
    due = State()
    new_amount = State()


def _today(settings: Row) -> date:
    return tu.local_today(settings["tz"])


async def show_list(event: Message | CallbackQuery, db: Connection, settings: Row, header: str | None = None) -> None:
    text, markup = views.list_view(await repo.list_all(db, settings["user_id"]), settings["currency"], _today(settings))
    await show(event, f"{header}\n\n{text}" if header else text, markup)


async def show_card(event: Message | CallbackQuery, db: Connection, settings: Row, payment_id: int) -> None:
    payment = await repo.get(db, settings["user_id"], payment_id)
    if not payment:
        await show(event, "Платёж не найден — возможно, его удалили.", views.BACK_TO_LIST)
        return
    await show(event, *views.card(payment, settings["currency"], _today(settings)))


# --- мастер ---
@router.callback_query(PayCb.filter(F.act == "cat"), PayForm.category)
async def got_category(
    callback: CallbackQuery,
    callback_data: PayCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    category = await finance.category(db, settings["user_id"], callback_data.id)
    if not category or category["archived"] or category["kind"] != finance.EXPENSE:
        callback_answer.text = "Эта категория недоступна"
        return
    await state.update_data(category_id=category["id"])
    await state.set_state(PayForm.period)
    await show(callback, "🔁 <b>Как часто?</b>", views.period_kb())


@router.callback_query(PayCb.filter(F.act == "period"), PayForm.period)
async def got_period(callback: CallbackQuery, callback_data: PayCb, state: FSMContext, settings: Row) -> None:
    if callback_data.v not in repo.PERIODS:
        return
    await state.update_data(period=callback_data.v)
    await state.set_state(PayForm.due)
    today = _today(settings)
    await show(callback, "🗓 <b>Когда ближайший платёж?</b>", calendar_kb(today.year, today.month, today, FUTURE))


@router.callback_query(CalCb.filter(F.action == "pick"), PayForm.due)
async def got_due(
    callback: CallbackQuery, callback_data: CalCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    data = await state.get_data()
    await state.clear()
    uid, due = settings["user_id"], picked_date(callback_data)
    await repo.create(
        db,
        uid,
        data["title"],
        data["amount"],
        data["category_id"],
        settings["default_account_id"],
        data["period"],
        due,
        settings["tz"],
    )
    amount = fmt_money(data["amount"], settings["currency"])
    await journal.add(
        db, uid, f"📅 Новый платёж {quote(data['title'])} · {amount}, {views.PERIOD_TITLES[data['period']]}"
    )
    await show_list(callback, db, settings, "✅ Платёж добавлен")


@router.message(PayForm.title, F.text)
async def got_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip()[:40])
    await state.set_state(PayForm.amount)
    await message.answer("💰 <b>Сколько платишь?</b> Например <code>150000</code>.", reply_markup=CANCEL)


@router.message(PayForm.amount, F.text)
async def got_amount(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("Не понял сумму. Напиши число, например <code>150000</code>.", reply_markup=CANCEL)
        return
    await state.update_data(amount=amount)
    await state.set_state(PayForm.category)
    categories = await finance.categories(db, settings["user_id"], finance.EXPENSE)
    await message.answer("🗂 <b>Категория?</b>", reply_markup=views.categories_kb(categories))


@router.message(PayForm.new_amount, F.text)
async def got_new_amount(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer("Не понял сумму. Напиши число, например <code>150000</code>.", reply_markup=CANCEL)
        return
    payment_id = (await state.get_data())["payment_id"]
    await state.clear()
    payment = await repo.get(db, settings["user_id"], payment_id)
    if payment:
        await repo.set_amount(db, payment_id, amount)
        money = fmt_money(amount, settings["currency"])
        await journal.add(db, settings["user_id"], f"✏️ Платёж {quote(payment['title'])}: {money}")
    await show_card(message, db, settings, payment_id)


# --- список, карточка, напоминание ---
@router.callback_query(
    PayCb.filter(F.act.in_({"list", "new", "open", "paid", "snooze", "skip", "amount", "del", "delok"}))
)
async def payment_action(
    callback: CallbackQuery,
    callback_data: PayCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    await state.clear()
    uid, act = settings["user_id"], callback_data.act
    if act == "list":
        await show_list(callback, db, settings)
        return
    if act == "new":
        await state.set_state(PayForm.title)
        await show(
            callback, "📅 <b>Новый регулярный платёж</b>\nКак назвать? Например: «🏠 Аренда», «📺 Netflix».", CANCEL
        )
        return
    payment = await repo.get(db, uid, callback_data.id)
    if not payment:
        await show(callback, "Платёж не найден — возможно, его удалили.", views.BACK_TO_LIST)
        return
    due, today, tz = date.fromisoformat(payment["due_date"]), _today(settings), settings["tz"]
    if act in ("paid", "snooze", "skip") and callback_data.d != tu.pack_day(due):
        # Кнопка с прошлого напоминания: этот раз уже оплачен или пропущен.
        callback_answer.text = "Этот платёж уже отмечен"
        await show_card(callback, db, settings, payment["id"])
        return
    if act == "open":
        await show_card(callback, db, settings, payment["id"])
    elif act == "paid":
        new_due = await repo.advance(db, payment, tz)
        account = payment["account_id"] or settings["default_account_id"]
        await journal.add(db, uid, f"📅 Оплачен платёж {quote(payment['title'])}")
        await finance_handlers.record_op(
            callback,
            db,
            settings,
            finance.EXPENSE,
            payment["amount"],
            payment["category_id"],
            payment["title"],
            account_id=account,
            header=f"✅ Записал. Следующий платёж — {fmt_date(new_due, today)}",
        )
    elif act == "snooze":
        await repo.set_next_run(db, payment["id"], repo.remind_at(today + timedelta(days=1), tz))
        callback_answer.text = "Напомню завтра"
        await show(callback, f"⏰ {esc(payment['title'])}: напомню завтра.", views.BACK_TO_LIST)
    elif act == "skip":
        new_due = await repo.advance(db, payment, tz)
        await journal.add(db, uid, f"⏭ Пропущен платёж {quote(payment['title'])}")
        await show_list(
            callback, db, settings, f"⏭ {esc(payment['title'])}: пропущен, следующий — {fmt_date(new_due, today)}"
        )
    elif act == "amount":
        await state.set_state(PayForm.new_amount)
        await state.update_data(payment_id=payment["id"])
        await show(callback, f"✏️ Новая сумма для «{esc(payment['title'])}»:", CANCEL)
    elif act == "del":
        await show(
            callback,
            f"🗑 Удалить платёж «{esc(payment['title'])}»? Записанные расходы останутся.",
            confirm_kb(PayCb(act="delok", id=payment["id"]), PayCb(act="open", id=payment["id"])),
        )
    elif act == "delok":
        await repo.delete(db, payment["id"])
        await journal.add(db, uid, f"🗑 Удалён платёж {quote(payment['title'])}")
        await show_list(callback, db, settings, "🗑 Платёж удалён")
