from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer
from aiosqlite import Connection, Row

from bot.callbacks import NavCb, SetCb, TimeCb
from bot.handlers.ui import show
from bot.keyboards import timepicker
from bot.keyboards.common import btn, kb
from bot.repo import habits, journal
from bot.repo import settings as repo
from bot.services import backup, morning
from bot.services import timeutil as tu
from bot.services.planning import reschedule_all
from bot.services.texts import fmt_money, parse_amount
from bot.views import settings as views

router = Router(name="settings")

BACK = kb([btn("✖ Отмена", SetCb(act="menu"))])


class SettingsForm(StatesGroup):
    tz = State()
    balance = State()
    time = State()


async def open_settings(
    event: Message | CallbackQuery, db: Connection, user_id: int, header: str | None = None
) -> None:
    text, markup = views.settings_view(await repo.get(db, user_id))
    await show(event, f"{header}\n\n{text}" if header else text, markup)


async def _set_tz(event: Message | CallbackQuery, db: Connection, user_id: int, tz: str) -> None:
    await repo.update(db, user_id, tz=tz)
    await reschedule_all(db, user_id, tu.utcnow())
    header = f"🌍 Часовой пояс: {views.tz_title(tz)}"
    await journal.add(db, user_id, header)
    await open_settings(event, db, user_id, header)


@router.callback_query(SetCb.filter())
async def settings_action(
    callback: CallbackQuery,
    callback_data: SetCb,
    state: FSMContext,
    db: Connection,
    settings: Row,
    callback_answer: CallbackAnswer,
) -> None:
    act, value, uid = callback_data.act, callback_data.v, settings["user_id"]
    schedules = views.SCHEDULES
    await state.clear()
    if act == "menu":
        await open_settings(callback, db, uid)
    elif act == "tz":
        await show(callback, "🌍 <b>Часовой пояс</b>\nОт него зависит, когда приходят напоминания.", views.tz_kb())
    elif act == "tzset" and tu.is_valid_tz(value):
        await _set_tz(callback, db, uid, value)
    elif act == "tzinput":
        await state.set_state(SettingsForm.tz)
        await show(
            callback,
            "⌨️ Напиши часовой пояс в формате IANA, например <code>Asia/Almaty</code> или <code>Europe/Moscow</code>.",
            BACK,
        )
    elif act == "cur":
        await show(callback, "💱 <b>Валюта</b>", views.currency_kb())
    elif act == "curset" and value in views.CURRENCIES:
        await repo.update(db, uid, currency=value)
        await journal.add(db, uid, f"💱 Валюта: {value}")
        await open_settings(callback, db, uid, f"💱 Валюта: {value}")
    elif act == "balance":
        await state.set_state(SettingsForm.balance)
        await show(
            callback,
            "💼 <b>Начальный баланс</b>\n"
            f"Сейчас: {fmt_money(settings['start_balance'], settings['currency'])}\n\n"
            "Сколько денег было до того, как ты начал вести учёт? Напиши число (можно отрицательное).",
            BACK,
        )
    elif act == "sched" and value in schedules:
        await show(callback, *views.schedule_view(settings, value))
    elif act == "toggle" and value in schedules:
        on = not settings[f"{value}_on"]
        next_at = repo.next_at(value, settings[f"{value}_time"], settings["tz"], tu.utcnow())
        await repo.update(db, uid, **{f"{value}_on": int(on), f"{value}_next_at": next_at})
        if value == "morning" and not on:
            await morning.replace_pin(callback.bot, db, settings)
        await journal.add(db, uid, f"{views.SCHEDULES[value][0]}: {'включено' if on else 'выключено'}")
        callback_answer.text = "Включено" if on else "Выключено"
        await show(callback, *views.schedule_view(await repo.get(db, uid), value))
    elif act == "backupnow":
        day = tu.local_today(settings["tz"])
        await callback.message.answer_document(await backup.snapshot(db, day), caption=backup.caption(day))
        callback_answer.text = "Бэкап отправлен"
    elif act == "morningnow":
        tz = settings["tz"]
        today = tu.local_today(tz)
        if not await habits.planned(db, uid, today):
            callback_answer.text = "На сегодня привычек нет"
            return
        text, markup = await morning.screen(db, uid, tz, today, today)
        sent = await callback.message.answer(text, reply_markup=markup)
        await morning.replace_pin(callback.bot, db, settings, sent.message_id, today)
        callback_answer.text = "Список на сегодня закреплён"
    elif act == "time" and value in schedules:
        await state.set_state(SettingsForm.time)
        await state.update_data(key=value)
        await show(
            callback,
            f"🕐 <b>{views.SCHEDULES[value][0]}</b>: во сколько?",
            timepicker.presets_kb(timepicker.KIND_PLAIN),
        )


@router.message(SettingsForm.tz, F.text)
async def got_tz(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    tz = message.text.strip()
    if not tu.is_valid_tz(tz):
        await message.answer("Не знаю такой пояс. Пример: <code>Asia/Almaty</code>.", reply_markup=BACK)
        return
    await state.clear()
    await _set_tz(message, db, settings["user_id"], tz)


@router.message(SettingsForm.balance, F.text)
async def got_balance(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    value = parse_amount(message.text, allow_negative=True, allow_zero=True)
    if value is None:
        await message.answer("Не понял сумму. Напиши число, например <code>150000</code>.", reply_markup=BACK)
        return
    await state.clear()
    await repo.update(db, settings["user_id"], start_balance=value)
    header = f"💼 Начальный баланс: {fmt_money(value, settings['currency'])}"
    await journal.add(db, settings["user_id"], header)
    await open_settings(message, db, settings["user_id"], header)


@router.callback_query(TimeCb.filter(F.action == "pick"), SettingsForm.time)
async def got_time(
    callback: CallbackQuery, callback_data: TimeCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    key = (await state.get_data())["key"]
    await state.clear()
    at = tu.fmt_hm(timepicker.picked_time(callback_data))
    next_at = repo.next_at(key, at, settings["tz"], tu.utcnow())
    await repo.update(db, settings["user_id"], **{f"{key}_time": at, f"{key}_on": 1, f"{key}_next_at": next_at})
    await journal.add(db, settings["user_id"], f"{views.SCHEDULES[key][0]}: {at}")
    await show(callback, *views.schedule_view(await repo.get(db, settings["user_id"]), key))


@router.callback_query(NavCb.filter(F.action == "back"), SettingsForm.time)
async def back_from_time(callback: CallbackQuery, state: FSMContext, settings: Row) -> None:
    key = (await state.get_data())["key"]
    await state.clear()
    await show(callback, *views.schedule_view(settings, key))
