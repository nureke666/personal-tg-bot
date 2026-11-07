from datetime import date

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from aiosqlite import Connection, Row

from bot.callbacks import CalCb, CountdownCb, NavCb, TimeCb
from bot.handlers.ui import show
from bot.keyboards import timepicker
from bot.keyboards.calendar import FUTURE, calendar_kb, picked_date
from bot.keyboards.common import btn, confirm_kb, kb, nav_row
from bot.repo import countdowns as repo
from bot.repo import journal
from bot.services import timeutil as tu
from bot.services.texts import esc, fmt_date_long, quote
from bot.views import countdowns as views

router = Router(name="countdowns")


class CountdownForm(StatesGroup):
    title = State()
    target_date = State()
    repeat = State()
    send_time = State()
    text = State()
    confirm = State()


STEPS = {
    "title": CountdownForm.title,
    "target_date": CountdownForm.target_date,
    "repeat": CountdownForm.repeat,
    "send_time": CountdownForm.send_time,
    "text": CountdownForm.text,
    "confirm": CountdownForm.confirm,
}
NEXT = {"title": "target_date", "target_date": "repeat", "repeat": "send_time", "send_time": "text", "text": "confirm"}
PREV = {"target_date": "title", "repeat": "target_date", "send_time": "repeat", "text": "send_time"}
BACK_TO_LIST = kb([btn("◀ К отсчётам", CountdownCb(act="list"))])
# Что записать в журнал после правки сохранённого отсчёта.
EDITED = {
    "title": "новое название",
    "target_date": "новая дата",
    "repeat": "изменён повтор",
    "send_time": "новое время",
    "text": "изменён текст",
}


def _today(settings: Row) -> date:
    return tu.local_today(settings["tz"])


async def open_list(event: Message | CallbackQuery, db: Connection, settings: Row) -> None:
    items = await repo.list_all(db, settings["user_id"])
    await show(event, *views.countdown_list(items, _today(settings)))


async def show_card(
    event: Message | CallbackQuery, db: Connection, settings: Row, countdown_id: int, header: str | None = None
) -> None:
    countdown = await repo.get(db, settings["user_id"], countdown_id)
    if not countdown:
        await show(event, "Отсчёт не найден — возможно, его удалили.", BACK_TO_LIST)
        return
    text, markup = views.countdown_card(countdown, _today(settings))
    await show(event, f"{header}\n\n{text}" if header else text, markup)


async def ask(event: Message | CallbackQuery, state: FSMContext, settings: Row, step: str) -> None:
    await state.set_state(STEPS[step])
    data = await state.get_data()
    draft, editing = data.get("draft") or {}, bool(data.get("edit_id"))
    today = _today(settings)
    if step == "title":
        text = "⏳ <b>Как назовём отсчёт?</b>\nНапример: «🏖 Отпуск» или «🎓 Защита диплома». Эмодзи приветствуются."
        markup = kb(nav_row(back=editing))
    elif step == "target_date":
        text, markup = "📅 <b>Дата события</b>", calendar_kb(today.year, today.month, today, FUTURE)
    elif step == "repeat":
        text = (
            "🔁 <b>Это событие каждый год?</b>\n"
            "Для дня рождения или годовщины: после события сам перейду на следующий год.\n"
            "Чтобы не писать весь год, могу начинать за месяц или за неделю."
        )
        markup = views.repeat_kb()
    elif step == "send_time":
        text = "🕐 <b>Во сколько присылать сообщение?</b>"
        markup = timepicker.presets_kb(timepicker.KIND_PLAIN)
    elif step == "text":
        text = (
            "💬 <b>Текст сообщения</b>\n"
            "Что добавить к счётчику? Например: «Пора бронировать билеты!»\n\n"
            "Можно вставить <code>{дни}</code> — подставлю число с нужным словом: "
            "«До отпуска {дни}!» → «До отпуска 12 дней!»\n\n"
            "Или нажми «Пропустить»" + (", чтобы убрать текст." if editing else ".")
        )
        markup = kb(nav_row(skip=True))
    else:
        left = (date.fromisoformat(draft["target_date"]) - today).days
        preview = views.countdown_message(draft["title"], draft.get("text"), left)
        text = (
            f"Так будет выглядеть сообщение:\n\n{preview}\n\n"
            f"Буду присылать {views.schedule_text(draft)}, событие — "
            f"{fmt_date_long(date.fromisoformat(draft['target_date']))}."
        )
        markup = views.confirm_kb()
    await show(event, text, markup)


async def advance(event: Message | CallbackQuery, state: FSMContext, db: Connection, settings: Row, done: str) -> None:
    data = await state.get_data()
    draft, edit_id = data.get("draft") or {}, data.get("edit_id")
    if edit_id:
        countdown = await repo.get(db, settings["user_id"], edit_id)
        await state.clear()
        if countdown and done == "repeat":
            await repo.set_repeat(
                db, countdown, bool(draft["yearly"]), draft.get("lead_days"), settings["tz"], tu.utcnow()
            )
        elif countdown:
            await repo.update_field(db, countdown, done, draft.get(done), settings["tz"], tu.utcnow())
        if countdown:
            await journal.add(db, countdown["user_id"], f"✏️ Отсчёт {quote(draft['title'])}: {EDITED[done]}")
        await show_card(event, db, settings, edit_id, "✅ Сохранено")
        return
    await ask(event, state, settings, NEXT[done])


async def _set_draft(state: FSMContext, **changes) -> None:
    draft = dict((await state.get_data()).get("draft") or {})
    draft.update(changes)
    await state.update_data(draft=draft)


@router.callback_query(CountdownCb.filter())
async def action(
    callback: CallbackQuery,
    callback_data: CountdownCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    act = callback_data.act
    tz, now = settings["tz"], tu.utcnow()
    if act == "list":
        await state.clear()
        await open_list(callback, db, settings)
        return
    if act == "new":
        await state.clear()
        await state.update_data(draft={}, edit_id=None)
        await ask(callback, state, settings, "title")
        return
    if act == "save":
        draft = (await state.get_data()).get("draft") or {}
        if await state.get_state() != CountdownForm.confirm.state or not draft.get("title"):
            callback_answer.text = "Черновик потерялся. Начни заново."
            return
        countdown_id = await repo.create(
            db,
            settings["user_id"],
            draft["title"],
            draft.get("text"),
            draft["target_date"],
            draft["send_time"],
            tz,
            now,
            yearly=bool(draft.get("yearly")),
            lead_days=draft.get("lead_days"),
        )
        await journal.add(db, settings["user_id"], f"⏳ Новый отсчёт {quote(draft['title'])}")
        await state.clear()
        await show_card(callback, db, settings, countdown_id, "✅ Отсчёт создан")
        return

    if act == "rep":
        if await state.get_state() != CountdownForm.repeat.state or callback_data.f not in repo.REPEATS:
            callback_answer.text = "Черновик потерялся. Начни заново."
            return
        yearly, lead_days = repo.REPEATS[callback_data.f]
        await _set_draft(state, yearly=yearly, lead_days=lead_days)
        await advance(callback, state, db, settings, "repeat")
        return

    countdown = await repo.get(db, settings["user_id"], callback_data.id)
    if not countdown:
        await show(callback, "Отсчёт не найден — возможно, его удалили.", BACK_TO_LIST)
        return
    if act == "open":
        await state.clear()
        await show_card(callback, db, settings, countdown["id"])
    elif act == "edit":
        await state.update_data(draft=dict(countdown), edit_id=countdown["id"])
        await ask(callback, state, settings, callback_data.f)
    elif act in ("pause", "resume"):
        await repo.set_status(db, countdown, repo.PAUSED if act == "pause" else repo.ACTIVE, tz, now)
        title = quote(countdown["title"])
        await journal.add(
            db, countdown["user_id"], f"⏸ Отсчёт {title} на паузе" if act == "pause" else f"▶ Отсчёт {title} снова идёт"
        )
        await show_card(callback, db, settings, countdown["id"], "⏸ На паузе" if act == "pause" else "▶ Снова считаю")
    elif act == "now":
        left = views.days_left(countdown, _today(settings))
        await callback.message.answer(views.countdown_message(countdown["title"], countdown["text"], left))
        callback_answer.text = "Отправил ✉️"
    elif act == "del":
        await show(
            callback,
            f"🗑 Удалить отсчёт «{esc(countdown['title'])}»?",
            confirm_kb(CountdownCb(act="delok", id=countdown["id"]), CountdownCb(act="open", id=countdown["id"])),
        )
    elif act == "delok":
        await repo.delete(db, countdown["id"])
        await journal.add(db, countdown["user_id"], f"🗑 Удалён отсчёт {quote(countdown['title'])}")
        await open_list(callback, db, settings)


@router.callback_query(NavCb.filter(F.action == "back"), StateFilter(CountdownForm))
async def back(callback: CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    data = await state.get_data()
    if data.get("edit_id"):
        await state.clear()
        await show_card(callback, db, settings, data["edit_id"])
        return
    step = (await state.get_state()).split(":")[1]
    if step in PREV:
        await ask(callback, state, settings, PREV[step])


@router.message(CountdownForm.title, F.text)
async def got_title(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await _set_draft(state, title=message.text.strip()[:100])
    await advance(message, state, db, settings, "title")


@router.callback_query(CalCb.filter(F.action == "pick"), CountdownForm.target_date)
async def got_date(
    callback: CallbackQuery, callback_data: CalCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    await _set_draft(state, target_date=picked_date(callback_data).isoformat())
    await advance(callback, state, db, settings, "target_date")


@router.callback_query(TimeCb.filter(F.action == "pick"), CountdownForm.send_time)
async def got_time(
    callback: CallbackQuery, callback_data: TimeCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    await _set_draft(state, send_time=tu.fmt_hm(timepicker.picked_time(callback_data)))
    await advance(callback, state, db, settings, "send_time")


@router.message(CountdownForm.text, F.text)
async def got_text(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await _set_draft(state, text=message.text.strip()[:1000])
    await advance(message, state, db, settings, "text")


@router.callback_query(NavCb.filter(F.action == "skip"), CountdownForm.text)
async def skip_text(callback: CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    await _set_draft(state, text=None)
    await advance(callback, state, db, settings, "text")
