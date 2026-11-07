"""Быстрый ввод: текст вне мастеров.

С датой или временем («завтра в 10 позвонить маме») — сразу задача, с суммой — операция,
остальное — предлагаем сделать задачей.
"""

from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from aiosqlite import Connection, Row

from bot.callbacks import QuickCb
from bot.handlers import finance, tasks
from bot.handlers.finance import FinForm
from bot.handlers.ui import show
from bot.repo import finance as finance_repo
from bot.repo import journal
from bot.repo import tasks as tasks_repo
from bot.services import quick, smart_task
from bot.services import timeutil as tu
from bot.services.texts import quote
from bot.views import tasks as task_views

router = Router(name="quick")


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def quick_text(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    text, uid, mid = message.text.strip(), settings["user_id"], message.message_id
    if guess := smart_task.parse(text, tu.local_now(settings["tz"])):
        await state.clear()
        await save_guessed_task(message, db, settings, guess)
        return
    await state.set_data({"quick_text": text, "quick_mid": mid})
    op = quick.parse_op(text)
    if op is None:
        await show(message, *task_views.quick_offer(text, mid))
        return
    history = await finance_repo.comment_history(db, uid)
    guess = quick.guess_category(op, history, await finance_repo.all_categories(db, uid))
    if guess:
        await state.clear()
        kind, category_id = guess
        await finance.record_op(message, db, settings, kind, op.amount, category_id, op.comment)
        return
    kind = finance_repo.INCOME if op.sign == "+" else finance_repo.EXPENSE
    await state.update_data(kind=kind, amount=op.amount, comment=op.comment)
    await finance.ask_category(message, state, db, settings)


@router.callback_query(QuickCb.filter())
async def quick_action(
    callback: CallbackQuery,
    callback_data: QuickCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    data = await state.get_data()
    text, act = data.get("quick_text"), callback_data.act
    if not text or data.get("quick_mid") != callback_data.mid:
        callback_answer.text = "Эта кнопка устарела. Напиши текст ещё раз."
        return
    if act == "kind":
        if await state.get_state() != FinForm.category.state:
            callback_answer.text = "Эта кнопка устарела. Напиши текст ещё раз."
            return
        kind = finance_repo.EXPENSE if data["kind"] == finance_repo.INCOME else finance_repo.INCOME
        await state.update_data(kind=kind)
        await finance.ask_category(callback, state, db, settings)
        return
    if act == "task":
        await state.set_state(None)
        await show(callback, *task_views.quick_offer(text, callback_data.mid))
        return
    await state.clear()
    if act == "wizard":
        await tasks.start_wizard(callback, state, settings, title=text)
        return
    today = tu.local_today(settings["tz"])
    due = {"today": today, "tomorrow": today + timedelta(days=1)}.get(act)
    draft = {"title": text[:200], "due_date": due.isoformat() if due else None, "offsets": [0] if due else []}
    task_id = await tasks_repo.create(db, settings["user_id"], draft, settings["tz"], tu.utcnow())
    await journal.add(db, settings["user_id"], f"📋 Новая задача {quote(draft['title'])}")
    await tasks.show_card(callback, db, settings, task_id, "✅ Задача сохранена")


async def save_guessed_task(message: Message, db: Connection, settings: Row, guess: smart_task.TaskGuess) -> None:
    draft = {
        "title": guess.title[:200],
        "due_date": guess.due_date.isoformat(),
        "due_time": tu.fmt_hm(guess.due_time) if guess.due_time else None,
        "repeat": guess.repeat,
        "offsets": [0],
    }
    task_id = await tasks_repo.create(db, settings["user_id"], draft, settings["tz"], tu.utcnow())
    await journal.add(db, settings["user_id"], f"📋 Новая задача {quote(draft['title'])}")
    await tasks.show_card(message, db, settings, task_id, "✅ Задача сохранена — понял из текста, поправь, если не так")
