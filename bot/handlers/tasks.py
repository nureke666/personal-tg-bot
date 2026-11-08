from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from aiosqlite import Connection, Row

from bot.callbacks import (
    CalCb,
    DateQuickCb,
    NavCb,
    RemindCb,
    RepeatCb,
    SnoozeCb,
    TaskCb,
    TaskFieldCb,
    TaskListCb,
    TimeCb,
)
from bot.handlers.ui import show
from bot.keyboards import timepicker
from bot.keyboards.calendar import FUTURE, calendar_kb, picked_date
from bot.keyboards.common import btn, confirm_kb, kb, nav_row
from bot.repo import journal
from bot.repo import tasks as tasks_repo
from bot.services import morning
from bot.services import timeutil as tu
from bot.services.texts import esc, fmt_date, quote
from bot.views import tasks as views

router = Router(name="tasks")


class TaskForm(StatesGroup):
    title = State()
    details = State()
    date = State()
    time = State()
    reminders = State()
    repeat = State()
    confirm = State()


STEPS = {
    "title": TaskForm.title,
    "details": TaskForm.details,
    "date": TaskForm.date,
    "time": TaskForm.time,
    "reminders": TaskForm.reminders,
    "repeat": TaskForm.repeat,
    "confirm": TaskForm.confirm,
}
NEXT = {"title": "details", "details": "date", "time": "reminders", "reminders": "repeat", "repeat": "confirm"}
PREV = {"details": "title", "date": "details", "time": "date", "reminders": "time", "repeat": "reminders"}
NEW_DRAFT = {"offsets": [0], "repeat": tu.REPEAT_NONE}
BACK_TO_TASKS = kb([btn("◀ К задачам", TaskListCb(view="menu"))])
# Что записать в журнал после правки поля сохранённой задачи.
EDITED = {
    "title": "новое название",
    "details": "изменены подробности",
    "date": "новый срок",
    "time": "новый срок",
    "reminders": "изменены напоминания",
    "repeat": "изменён повтор",
}


def _today(settings: Row) -> date:
    return tu.local_today(settings["tz"])


def _now_hm(settings: Row) -> str:
    return tu.local_now(settings["tz"]).strftime("%H:%M")


# --- экраны ---
async def open_menu(event: Message | CallbackQuery, db: Connection, settings: Row) -> None:
    counts = await tasks_repo.counts(db, settings["user_id"], _today(settings), _now_hm(settings))
    await show(event, *views.tasks_menu(counts))


async def show_card(
    event: Message | CallbackQuery, db: Connection, settings: Row, task_id: int, header: str | None = None
) -> None:
    task = await tasks_repo.get(db, settings["user_id"], task_id)
    if not task:
        await show(event, "Задача не найдена — возможно, её удалили.", BACK_TO_TASKS)
        return
    text = views.task_text(dict(task), await tasks_repo.offsets(db, task_id), _today(settings))
    await show(event, f"{header}\n\n{text}" if header else text, views.task_card_kb(task))


@router.callback_query(TaskListCb.filter())
async def list_view(
    callback: CallbackQuery, callback_data: TaskListCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    view = callback_data.view
    if view == "new":
        await start_wizard(callback, state, settings)
        return
    await state.clear()
    if view == "menu":
        await open_menu(callback, db, settings)
        return
    today = _today(settings)
    tasks = await tasks_repo.list_view(db, settings["user_id"], view, today, _now_hm(settings))
    await show(callback, *views.task_list(view, tasks, today))


# --- мастер: создание и правка ---
async def start_wizard(
    event: Message | CallbackQuery, state: FSMContext, settings: Row, title: str | None = None
) -> None:
    """Мастер новой задачи. С готовым названием (из быстрого ввода) начинаем сразу с даты."""
    await state.clear()
    draft = dict(NEW_DRAFT) | ({"title": title[:200]} if title else {})
    await state.update_data(draft=draft, return_to=None, edit_id=None)
    await ask(event, state, settings, "date" if title else "title")


async def _draft(state: FSMContext) -> dict:
    return dict((await state.get_data()).get("draft") or {})


async def _set_draft(state: FSMContext, **changes) -> dict:
    draft = await _draft(state)
    draft.update(changes)
    await state.update_data(draft=draft)
    return draft


async def ask(event: Message | CallbackQuery, state: FSMContext, settings: Row, step: str) -> None:
    await state.set_state(STEPS[step])
    data = await state.get_data()
    draft, editing = data.get("draft") or {}, bool(data.get("return_to"))
    today = _today(settings)
    if step == "title":
        text = "✏️ <b>Что нужно сделать?</b>\nНапиши коротко, например: «Позвонить маме»."
        markup = kb(nav_row(back=editing))
    elif step == "details":
        text = "📝 <b>Подробности</b>\nНапиши, что важно не забыть: адрес, номер, список."
        text += "\nИли нажми «Пропустить», чтобы " + ("убрать подробности." if editing else "оставить без них.")
        markup = kb(nav_row(skip=True))
    elif step == "date":
        await state.update_data(calendar=False)
        text, markup = "📅 <b>Когда?</b>", views.date_quick_kb()
    elif step == "time":
        day = fmt_date(date.fromisoformat(draft["due_date"]), today)
        text, markup = f"🕐 <b>Во сколько?</b> ({day})", timepicker.presets_kb(timepicker.KIND_TASK)
    elif step == "reminders":
        text = "🔔 <b>Когда напомнить?</b>\nМожно выбрать несколько, потом нажми «Готово»."
        if not draft.get("due_time"):
            text += f"\nЗадача на весь день: «в момент» — это {tu.fmt_hm(tu.ALL_DAY_REMIND)} в этот день."
        markup = views.reminders_kb(draft.get("offsets", []))
    elif step == "repeat":
        text, markup = "🔁 <b>Повторять задачу?</b>", views.repeat_kb()
    else:
        text = "Проверь задачу:\n\n" + views.task_text(draft, draft.get("offsets", []), today)
        markup = views.confirm_kb()
    await show(event, text, markup)


async def advance(event: Message | CallbackQuery, state: FSMContext, db: Connection, settings: Row, done: str) -> None:
    data = await state.get_data()
    draft, return_to = data.get("draft") or {}, data.get("return_to")
    if done == "date":
        step = "time" if draft.get("due_date") else (return_to or "confirm")
    else:
        step = return_to or NEXT[done]
    if step == "card":
        await tasks_repo.update(db, data["edit_id"], draft, settings["tz"], tu.utcnow())
        await journal.add(db, settings["user_id"], f"✏️ Задача {quote(draft['title'])}: {EDITED[done]}")
        await morning.refresh(event.bot, db, settings["user_id"])
        await state.clear()
        await show_card(event, db, settings, data["edit_id"], "✅ Сохранено")
        return
    await ask(event, state, settings, step)


@router.callback_query(NavCb.filter(F.action == "back"), StateFilter(TaskForm))
async def back(callback: CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    step = (await state.get_state()).split(":")[1]
    data = await state.get_data()
    if step == "date" and data.get("calendar"):
        await ask(callback, state, settings, "date")
        return
    target = "date" if step == "time" else (data.get("return_to") or PREV.get(step))
    if target == "card":
        await state.clear()
        await show_card(callback, db, settings, data["edit_id"])
    elif target:
        await ask(callback, state, settings, target)


@router.message(TaskForm.title, F.text)
async def got_title(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await _set_draft(state, title=message.text.strip()[:200])
    await advance(message, state, db, settings, "title")


@router.message(TaskForm.details, F.text)
async def got_details(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await _set_draft(state, details=message.text.strip()[:1500])
    await advance(message, state, db, settings, "details")


@router.callback_query(NavCb.filter(F.action == "skip"), TaskForm.details)
async def skip_details(callback: CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    await _set_draft(state, details=None)
    await advance(callback, state, db, settings, "details")


async def _set_date(state: FSMContext, day: date) -> None:
    draft = await _draft(state)
    offsets = draft.get("offsets") or ([0] if not draft.get("due_date") else [])
    await _set_draft(state, due_date=day.isoformat(), offsets=offsets)


@router.callback_query(DateQuickCb.filter(), TaskForm.date)
async def got_quick_date(
    callback: CallbackQuery, callback_data: DateQuickCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    today = _today(settings)
    if callback_data.v == "cal":
        await state.update_data(calendar=True)
        await show(callback, "📅 <b>Выбери дату</b>", calendar_kb(today.year, today.month, today, FUTURE))
        return
    if callback_data.v == "none":
        await _set_draft(state, due_date=None, due_time=None, offsets=[], repeat=tu.REPEAT_NONE)
    else:
        shift = {"today": 0, "tomorrow": 1, "after": 2}[callback_data.v]
        await _set_date(state, today + timedelta(days=shift))
    await advance(callback, state, db, settings, "date")


@router.callback_query(CalCb.filter(F.action == "pick"), TaskForm.date)
async def got_calendar_date(
    callback: CallbackQuery, callback_data: CalCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    await _set_date(state, picked_date(callback_data))
    await advance(callback, state, db, settings, "date")


@router.callback_query(TimeCb.filter(F.action.in_({"pick", "none"})), TaskForm.time)
async def got_time(
    callback: CallbackQuery, callback_data: TimeCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    due_time = tu.fmt_hm(timepicker.picked_time(callback_data)) if callback_data.action == "pick" else None
    await _set_draft(state, due_time=due_time)
    await advance(callback, state, db, settings, "time")


@router.callback_query(RemindCb.filter(), TaskForm.reminders)
async def got_reminder(
    callback: CallbackQuery, callback_data: RemindCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    offset = callback_data.off
    if offset == -1:
        await _set_draft(state, offsets=[])
    elif offset >= 0:
        selected = set((await _draft(state)).get("offsets", []))
        selected ^= {offset}
        await _set_draft(state, offsets=sorted(selected))
        await callback.message.edit_reply_markup(reply_markup=views.reminders_kb(sorted(selected)))
        return
    await advance(callback, state, db, settings, "reminders")


@router.callback_query(RepeatCb.filter(F.v.in_(views.REPEAT_LABELS)), TaskForm.repeat)
async def got_repeat(
    callback: CallbackQuery, callback_data: RepeatCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    await _set_draft(state, repeat=callback_data.v)
    await advance(callback, state, db, settings, "repeat")


@router.callback_query(NavCb.filter(F.action == "save"), TaskForm.confirm)
async def save(callback: CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    draft = await _draft(state)
    task_id = await tasks_repo.create(db, settings["user_id"], draft, settings["tz"], tu.utcnow())
    await journal.add(db, settings["user_id"], f"📋 Новая задача {quote(draft['title'])}")
    await morning.refresh(callback.bot, db, settings["user_id"])
    await state.clear()
    await show_card(callback, db, settings, task_id, "✅ Задача сохранена")


@router.callback_query(TaskFieldCb.filter())
async def edit_field(
    callback: CallbackQuery,
    callback_data: TaskFieldCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    field, task_id = callback_data.f, callback_data.id
    if task_id:
        task = await tasks_repo.get(db, settings["user_id"], task_id)
        if not task:
            await show(callback, "Задача не найдена — возможно, её удалили.", BACK_TO_TASKS)
            return
        if field == "back":
            await state.clear()
            await show_card(callback, db, settings, task_id)
            return
        draft = tasks_repo.to_draft(task, await tasks_repo.offsets(db, task_id))
        await state.update_data(draft=draft, return_to="card", edit_id=task_id)
    else:
        draft = await _draft(state)
        if not draft.get("title"):
            callback_answer.text = "Черновик потерялся (бот перезапускался). Начни заново."
            await state.clear()
            return
        if field == "back":
            await ask(callback, state, settings, "confirm")
            return
        await state.update_data(return_to="confirm")
    if field == "menu":
        await show(callback, "✏️ <b>Что изменить?</b>", views.fields_kb(0, bool(draft.get("due_date"))))
        return
    await ask(callback, state, settings, field)


# --- карточка задачи ---
@router.callback_query(TaskCb.filter())
async def task_action(
    callback: CallbackQuery,
    callback_data: TaskCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    act, task_id = callback_data.act, callback_data.id
    task = await tasks_repo.get(db, settings["user_id"], task_id)
    if not task:
        await show(callback, "Задача не найдена — возможно, её удалили.", BACK_TO_TASKS)
        return
    tz, now = settings["tz"], tu.utcnow()
    if act == "open":
        await show_card(callback, db, settings, task_id)
    elif act == "done":
        next_date = await tasks_repo.complete(db, task, tz, now)
        await journal.add(db, task["user_id"], f"✅ Выполнена задача {quote(task['title'])}")
        await morning.refresh(callback.bot, db, task["user_id"])
        header = f"✅ Готово! Следующий раз: {fmt_date(next_date, _today(settings))}" if next_date else "✅ Выполнено!"
        callback_answer.text = "Готово ✅"
        await show_card(callback, db, settings, task_id, header)
    elif act == "undo":
        await tasks_repo.reopen(db, task, tz, now)
        await journal.add(db, task["user_id"], f"↩️ Задача {quote(task['title'])} снова в работе")
        await morning.refresh(callback.bot, db, task["user_id"])
        await show_card(callback, db, settings, task_id, "↩️ Задача снова в работе")
    elif act == "move":
        draft = tasks_repo.to_draft(task, await tasks_repo.offsets(db, task_id))
        await state.update_data(draft=draft, return_to="card", edit_id=task_id)
        await ask(callback, state, settings, "date")
    elif act == "edit":
        await show(callback, "✏️ <b>Что изменить?</b>", views.fields_kb(task_id, bool(task["due_date"])))
    elif act == "del":
        await show(
            callback,
            f"🗑 Удалить задачу «{esc(task['title'])}»?",
            confirm_kb(TaskCb(act="delok", id=task_id), TaskCb(act="open", id=task_id)),
        )
    elif act == "delok":
        await tasks_repo.delete(db, task_id)
        await journal.add(db, task["user_id"], f"🗑 Удалена задача {quote(task['title'])}")
        await morning.refresh(callback.bot, db, task["user_id"])
        await show(callback, "🗑 Задача удалена.", BACK_TO_TASKS)


# --- кнопки под напоминанием ---
SNOOZE = {"m10": timedelta(minutes=10), "h1": timedelta(hours=1), "d1": timedelta(days=1)}
SNOOZE_LABELS = {"m10": "на 10 минут", "h1": "на час", "d1": "до завтра"}


@router.callback_query(SnoozeCb.filter())
async def reminder_action(
    callback: CallbackQuery, callback_data: SnoozeCb, db: Connection, settings: Row, callback_answer: CallbackAnswer
) -> None:
    tz, now, today = settings["tz"], tu.utcnow(), _today(settings)
    task = await tasks_repo.get(db, settings["user_id"], callback_data.id)
    original = callback.message.html_text
    if not task or task["status"] == tasks_repo.DONE:
        callback_answer.text = "Задача уже закрыта или удалена"
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    if callback_data.d and task["due_date"] != callback_data.d:
        # Повторяющаяся задача уже переехала дальше: не отмечаем по ошибке следующий раз.
        callback_answer.text = "Этот раз уже прошёл"
        note = f"Этот раз уже прошёл, задача перенесена на {fmt_date(date.fromisoformat(task['due_date']), today)}."
        await callback.message.edit_text(f"{original}\n\n<i>{note}</i>", reply_markup=None)
        return
    if callback_data.act == "done":
        next_date = await tasks_repo.complete(db, task, tz, now)
        await journal.add(db, task["user_id"], f"✅ Выполнена задача {quote(task['title'])}")
        await morning.refresh(callback.bot, db, task["user_id"])
        note = "✅ Сделано"
        if next_date:
            note += f". Следующий раз: {fmt_date(next_date, today)}"
        await callback.message.edit_text(f"{original}\n\n{note}", reply_markup=None)
        return
    fire_at = now + SNOOZE[callback_data.act]
    await tasks_repo.add_snooze(db, task["id"], fire_at)
    await journal.add(
        db, task["user_id"], f"⏰ Отложена задача {quote(task['title'])} {SNOOZE_LABELS[callback_data.act]}"
    )
    local = tu.local_now(tz, fire_at)
    when = fmt_date(local.date(), today)
    await callback.message.edit_text(f"{original}\n\n⏰ Напомню {when} в {local:%H:%M}", reply_markup=None)
