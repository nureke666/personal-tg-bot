"""Старт, помощь, отмена, навигация календаря и выбора времени — общее для всех разделов."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiosqlite import Row

from bot.callbacks import CalCb, NavCb, TimeCb
from bot.handlers.ui import show
from bot.keyboards import timepicker
from bot.keyboards.calendar import calendar_kb
from bot.keyboards.common import main_menu
from bot.services import timeutil as tu

router = Router(name="common")

HELP = """👋 Привет! Я твой личный помощник.

📋 <b>Задачи</b> — что сделать и когда, с напоминаниями и повтором.
⏳ <b>Отсчёты</b> — каждый день пишу, сколько дней осталось до события.
💰 <b>Финансы</b> — расходы и доходы, баланс, лимиты, отчёты, выгрузка в Excel.
✅ <b>Привычки</b> — список на утро, день и вечер в закрепе, отметки по номерам, серии 🔥, отказы.
📊 <b>Итоги</b> — задачи, привычки и деньги за любую неделю, месяц или год.
📜 <b>Журнал</b> — твои последние действия: 10, 25 или 50 за раз.
⚙️ <b>Настройки</b> — часовой пояс, валюта, сводки и итоги недели.

✍️ <b>Быстрый ввод</b> — просто напиши:
<code>1500 такси</code> — запишу расход, <code>+250000 зарплата</code> — доход.
Категорию спрошу один раз и запомню. Текст без суммы предложу сохранить задачей.

Начатое можно отменить командой /cancel."""


@router.message(CommandStart())
@router.message(Command("help", "menu"))
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP, reply_markup=main_menu())


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_menu())


@router.callback_query(NavCb.filter(F.action == "cancel"))
async def cancel_button(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(callback, "❌ Отменено.")


@router.callback_query(CalCb.filter(F.action == "nav"))
async def calendar_nav(callback: CallbackQuery, callback_data: CalCb, settings: Row) -> None:
    today = tu.local_today(settings["tz"])
    await callback.message.edit_reply_markup(
        reply_markup=calendar_kb(callback_data.y, callback_data.m, today, callback_data.mode)
    )


@router.callback_query(CalCb.filter(F.action == "noop"))
async def calendar_noop(callback: CallbackQuery) -> None:
    pass


@router.callback_query(TimeCb.filter(F.action.in_({"exact", "hour", "presets"})))
async def time_nav(callback: CallbackQuery, callback_data: TimeCb) -> None:
    kind = callback_data.kind
    if callback_data.action == "exact":
        markup = timepicker.hours_kb(kind)
    elif callback_data.action == "hour":
        markup = timepicker.minutes_kb(kind, callback_data.h)
    else:
        markup = timepicker.presets_kb(kind)
    await callback.message.edit_reply_markup(reply_markup=markup)
