from datetime import date

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from aiosqlite import Connection, Row

from bot.callbacks import CalCb, DaysCb, HabitCb, NavCb, PartCb, TimeCb
from bot.handlers.ui import show
from bot.keyboards import timepicker
from bot.keyboards.calendar import PAST, calendar_kb, picked_date
from bot.keyboards.common import btn, confirm_kb, kb, nav_row
from bot.repo import habits as repo
from bot.repo import journal
from bot.repo.habits import CHECK, QUIT
from bot.services import morning
from bot.services import timeutil as tu
from bot.services.texts import esc, fmt_date_long, quote
from bot.views import habits as views

router = Router(name="habits")


class HabitForm(StatesGroup):
    title = State()
    days = State()
    part = State()
    time = State()
    since = State()


STEPS = {
    "title": HabitForm.title,
    "days": HabitForm.days,
    "part": HabitForm.part,
    "time": HabitForm.time,
    "since": HabitForm.since,
}
NEXT = {CHECK: {"title": "days", "days": "part", "part": "time"}, QUIT: {"title": "since"}}
PREV = {"days": "title", "part": "days", "time": "part", "since": "title"}
BACK_TO_MANAGE = kb([btn("◀ К привычкам", HabitCb(act="manage"))])
# Что записать в журнал после правки сохранённой привычки.
EDITED = {
    "title": "новое название",
    "days": "новые дни",
    "part": "другая часть дня",
    "time": "изменено напоминание",
    "since": "новая дата начала",
}
# После этих действий меняется утренний закреп: отметки, счётчики отказов, состав и номера привычек.
CHANGES = {"toggle", "mark", "calset", "relapseok", "unrelapse", "archive", "restore", "delok"}


def _today(settings: Row) -> date:
    return tu.local_today(settings["tz"])


def _on_day(day: date, today: date) -> str:
    return "" if day == today else f" за {fmt_date_long(day)}"


async def _log_mark(db: Connection, habit: Row, day: date, today: date, marked: bool) -> None:
    title = quote(habit["title"])
    text = f"☑️ Отмечена привычка {title}" if marked else f"⬜ Снята отметка: {title}"
    await journal.add(db, habit["user_id"], text + _on_day(day, today))


async def open_today(
    event: Message | CallbackQuery,
    db: Connection,
    settings: Row,
    day: date | None = None,
    header: str | None = None,
    view: str = views.MENU,
) -> None:
    today = _today(settings)
    uid, tz = settings["user_id"], settings["tz"]
    await show(event, *await views.day_screen(db, uid, tz, day or today, today, header, view))


async def show_card(
    event: Message | CallbackQuery, db: Connection, settings: Row, habit_id: int, header: str | None = None
) -> None:
    habit = await repo.get(db, settings["user_id"], habit_id)
    if not habit:
        await show(event, "Привычка не найдена — возможно, её удалили.", BACK_TO_MANAGE)
        return
    if habit["kind"] == QUIT:
        today = _today(settings)
        stats = await views.habit_stats(db, habit, settings["tz"], today)
        text, markup = views.quit_card(habit, stats, today)
    else:
        text, markup = views.habit_card(habit)
    await show(event, f"{header}\n\n{text}" if header else text, markup)


async def show_calendar(event: Message | CallbackQuery, db: Connection, settings: Row, habit: Row, month: date) -> None:
    marks = await repo.marks(db, habit["id"])
    await show(event, *views.calendar_view(habit, marks, month.replace(day=1), _today(settings), settings["tz"]))


# --- мастер ---
async def ask(event: Message | CallbackQuery, state: FSMContext, settings: Row, step: str) -> None:
    await state.set_state(STEPS[step])
    data = await state.get_data()
    draft, editing = data.get("draft") or {}, bool(data.get("edit_id"))
    if step == "title" and draft.get("kind") == QUIT:
        text = "🚫 <b>От чего отказываешься?</b>\nНапример: «🍬 Без сладкого» или «🚭 Не курю»."
        markup = kb(nav_row(back=editing))
    elif step == "title":
        text = "✅ <b>Как назовём привычку?</b>\nНапример: «💧 Выпить 2 литра воды» или «📚 Читать 20 минут»."
        markup = kb(nav_row(back=editing))
    elif step == "since":
        today = _today(settings)
        text = "📅 <b>С какого дня держишься?</b>\nЕсли начинаешь сегодня — жми «Сегодня»."
        markup = calendar_kb(today.year, today.month, today, PAST)
    elif step == "days":
        text = "📅 <b>В какие дни?</b>"
        markup = views.days_kb(draft.get("mask", tu.ALL_DAYS), draft.get("custom", False))
    elif step == "part":
        text = "🕐 <b>Когда делаешь?</b>\nПо этому список на день делится на утро, день и вечер."
        markup = views.part_kb(draft.get("part"))
    else:
        text = "⏰ <b>Напоминать?</b>\nВыбери время — в этот момент пришлю напоминание. Или «Без напоминания»."
        markup = timepicker.presets_kb(timepicker.KIND_HABIT)
    await show(event, text, markup)


async def _set_draft(state: FSMContext, **changes) -> dict:
    draft = dict((await state.get_data()).get("draft") or {})
    draft.update(changes)
    await state.update_data(draft=draft)
    return draft


async def advance(event: Message | CallbackQuery, state: FSMContext, db: Connection, settings: Row, done: str) -> None:
    data = await state.get_data()
    draft, edit_id = data.get("draft") or {}, data.get("edit_id")
    tz, now = settings["tz"], tu.utcnow()
    if edit_id:
        await state.clear()
        habit = await repo.get(db, settings["user_id"], edit_id)
        if habit:
            if done == "title":
                await repo.rename(db, edit_id, draft["title"])
            elif done == "days":
                await repo.set_schedule(db, edit_id, draft["mask"], habit["remind_time"], tz, now)
            elif done == "part":
                await repo.set_part(db, edit_id, draft["part"])
            elif done == "time":
                await repo.set_schedule(db, edit_id, habit["days_mask"], draft.get("remind_time"), tz, now)
            elif done == "since":
                await repo.set_since(db, edit_id, date.fromisoformat(draft["since"]))
            await journal.add(db, habit["user_id"], f"✏️ Привычка {quote(draft['title'])}: {EDITED[done]}")
            await morning.refresh(event.bot, db, habit["user_id"])
        await show_card(event, db, settings, edit_id, "✅ Сохранено")
        return
    kind = draft.get("kind", CHECK)
    if done in NEXT[kind]:
        await ask(event, state, settings, NEXT[kind][done])
        return
    await state.clear()
    if kind == QUIT:
        since = date.fromisoformat(draft["since"])
        habit_id = await repo.create(
            db, settings["user_id"], draft["title"], tu.ALL_DAYS, None, tz, now, kind=QUIT, since=since
        )
        await journal.add(db, settings["user_id"], f"🚫 Новый отказ {quote(draft['title'])}")
        await morning.refresh(event.bot, db, settings["user_id"])
        await show_card(event, db, settings, habit_id, "✅ Отказ добавлен. Держись! 💪")
        return
    await repo.create(
        db, settings["user_id"], draft["title"], draft["mask"], draft.get("remind_time"), tz, now, part=draft["part"]
    )
    await journal.add(db, settings["user_id"], f"✅ Новая привычка {quote(draft['title'])}")
    await morning.refresh(event.bot, db, settings["user_id"])
    await open_today(event, db, settings, header=f"✅ Привычка «{esc(draft['title'])}» добавлена!")


@router.callback_query(NavCb.filter(F.action == "back"), StateFilter(HabitForm))
async def back(callback: CallbackQuery, state: FSMContext, db: Connection, settings: Row) -> None:
    data = await state.get_data()
    if data.get("edit_id"):
        await state.clear()
        await show_card(callback, db, settings, data["edit_id"])
        return
    step = (await state.get_state()).split(":")[1]
    if step in PREV:
        await ask(callback, state, settings, PREV[step])


@router.message(HabitForm.title, F.text)
async def got_title(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await _set_draft(state, title=message.text.strip()[:60])
    await advance(message, state, db, settings, "title")


@router.callback_query(DaysCb.filter(), HabitForm.days)
async def got_days(
    callback: CallbackQuery,
    callback_data: DaysCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    draft = (await state.get_data()).get("draft") or {}
    if callback_data.v in ("all", "weekdays"):
        await _set_draft(state, mask=tu.ALL_DAYS if callback_data.v == "all" else tu.WEEKDAYS)
        await advance(callback, state, db, settings, "days")
    elif callback_data.v == "custom":
        mask = draft.get("mask", tu.ALL_DAYS)
        if callback_data.bit >= 0:
            mask ^= 1 << callback_data.bit
        await _set_draft(state, mask=mask, custom=True)
        await callback.message.edit_reply_markup(reply_markup=views.days_kb(mask, custom=True))
    elif callback_data.v == "done":
        if not draft.get("mask"):
            callback_answer.text = "Выбери хотя бы один день"
            return
        await advance(callback, state, db, settings, "days")


@router.callback_query(PartCb.filter(F.v.in_(repo.PARTS)), HabitForm.part)
async def got_part(
    callback: CallbackQuery, callback_data: PartCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    await _set_draft(state, part=callback_data.v)
    await advance(callback, state, db, settings, "part")


@router.callback_query(CalCb.filter(F.action == "pick"), HabitForm.since)
async def got_since(
    callback: CallbackQuery, callback_data: CalCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    await _set_draft(state, since=picked_date(callback_data).isoformat())
    await advance(callback, state, db, settings, "since")


@router.callback_query(TimeCb.filter(F.action.in_({"pick", "none"})), HabitForm.time)
async def got_time(
    callback: CallbackQuery, callback_data: TimeCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    remind_time = tu.fmt_hm(timepicker.picked_time(callback_data)) if callback_data.action == "pick" else None
    await _set_draft(state, remind_time=remind_time)
    await advance(callback, state, db, settings, "time")


# --- экраны и действия ---
@router.callback_query(HabitCb.filter())
async def habit_action(
    callback: CallbackQuery,
    callback_data: HabitCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    act, uid, tz = callback_data.act, settings["user_id"], settings["tz"]
    if act == "noop":
        return  # значок части дня в ряду кнопок; мастер, если он открыт, не сбиваем
    today = _today(settings)
    day = views.unpack_day(callback_data.d) if callback_data.d else today
    await state.clear()

    if act == "day":
        await open_today(callback, db, settings, day)
        return
    if act == "new":
        if callback_data.f not in (CHECK, QUIT):
            await show(callback, *views.kind_choice())
            return
        await state.update_data(draft={"mask": tu.ALL_DAYS, "kind": callback_data.f}, edit_id=None)
        await ask(callback, state, settings, "title")
        return
    if act == "stats":
        items = [(h, await views.habit_stats(db, h, tz, today)) for h in await repo.list_active(db, uid)]
        await show(callback, *views.stats_view(items))
        return
    if act == "manage":
        await show(callback, *views.manage_view(await repo.list_all(db, uid)))
        return

    habit = await repo.get(db, uid, callback_data.id)
    if not habit:
        await show(callback, "Привычка не найдена — возможно, её удалили.", BACK_TO_MANAGE)
        return
    if habit["kind"] == QUIT and act in ("toggle", "mark"):
        act = "open"  # у отказа отметки — это срывы, их ставят только через «💥 Сорвался»
    if act == "toggle":
        marked = await repo.toggle_mark(db, habit["id"], day)
        await _log_mark(db, habit, day, today, marked)
        callback_answer.text = "✅ Отмечено" if marked else "Отметка снята"
        if callback_data.f == views.MORNING:
            await show(callback, *await morning.screen(db, uid, tz, day, today))
        else:
            await open_today(callback, db, settings, day, view=callback_data.f)
    elif act == "mark":
        await repo.set_mark(db, habit["id"], day)
        await _log_mark(db, habit, day, today, True)
        await callback.message.edit_text(f"{callback.message.html_text}\n\n✅ Отмечено", reply_markup=None)
    elif act == "open":
        await show_card(callback, db, settings, habit["id"])
    elif act == "edit" and callback_data.f in STEPS:
        fields = ("title", "kind", "remind_time", "since", "part")
        draft = {key: habit[key] for key in fields} | {"mask": habit["days_mask"]}
        if callback_data.f == "days":
            draft["custom"] = habit["days_mask"] not in (tu.ALL_DAYS, tu.WEEKDAYS)
        await state.update_data(draft=draft, edit_id=habit["id"])
        await ask(callback, state, settings, callback_data.f)
    elif act == "cal":
        await show_calendar(callback, db, settings, habit, day)
    elif act == "calset":
        if views.habit_start(habit, tz) <= day <= today:
            await _log_mark(db, habit, day, today, await repo.toggle_mark(db, habit["id"], day))
        await show_calendar(callback, db, settings, habit, day)
    elif act == "relapse" and habit["kind"] == QUIT:
        await show(callback, *views.relapse_confirm(habit, today))
    elif act == "relapseok" and habit["kind"] == QUIT:
        await repo.set_mark(db, habit["id"], day)
        await journal.add(db, uid, f"💥 Срыв: {quote(habit['title'])}" + _on_day(day, today))
        await show_card(callback, db, settings, habit["id"], "💥 Записал. Счётчик заново — ты справишься!")
    elif act == "unrelapse" and habit["kind"] == QUIT:
        stats = await views.habit_stats(db, habit, tz, today)
        if stats.last:
            await repo.remove_mark(db, habit["id"], stats.last)
            await journal.add(db, uid, f"↩️ Срыв отменён: {quote(habit['title'])}")
        await show_card(callback, db, settings, habit["id"], "↩️ Срыв отменён")
    elif act in ("archive", "restore"):
        await repo.set_archived(db, habit, act == "archive", tz, tu.utcnow())
        title = quote(habit["title"])
        text = f"🗄 Привычка {title} в архиве" if act == "archive" else f"👁 Привычка {title} возвращена из архива"
        await journal.add(db, uid, text)
        await show_card(callback, db, settings, habit["id"])
    elif act == "del":
        await show(
            callback,
            f"🗑 Удалить привычку «{esc(habit['title'])}» вместе со всей историей отметок?\n"
            "Если хочешь просто убрать её с глаз — отправь в архив.",
            confirm_kb(HabitCb(act="delok", id=habit["id"]), HabitCb(act="open", id=habit["id"])),
        )
    elif act == "delok":
        await repo.delete(db, habit["id"])
        await journal.add(db, uid, f"🗑 Удалена привычка {quote(habit['title'])}")
        await show(callback, *views.manage_view(await repo.list_all(db, uid)))
    if act in CHANGES:
        await morning.refresh(callback.bot, db, uid, callback.message.message_id)
