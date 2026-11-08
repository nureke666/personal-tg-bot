"""Кнопки главного меню. Роутер подключается первым: кнопка меню прерывает любой мастер."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiosqlite import Connection, Row

from bot.handlers import countdowns, finance, habits, journal, summary, tasks
from bot.handlers import settings as settings_handlers
from bot.keyboards.common import (
    MENU_COUNTDOWNS,
    MENU_FINANCE,
    MENU_HABITS,
    MENU_JOURNAL,
    MENU_SETTINGS,
    MENU_SUMMARY,
    MENU_TASKS,
)

router = Router(name="menu")


@router.message(F.text == MENU_TASKS)
async def open_tasks(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await state.clear()
    await tasks.open_menu(message, db, settings)


@router.message(F.text == MENU_COUNTDOWNS)
async def open_countdowns(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await state.clear()
    await countdowns.open_list(message, db, settings)


@router.message(F.text == MENU_FINANCE)
async def open_finance(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await state.clear()
    await finance.open_menu(message, db, settings)


@router.message(F.text == MENU_HABITS)
async def open_habits(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await state.clear()
    await habits.open_today(message, db, settings)


@router.message(F.text == MENU_SUMMARY)
async def open_summary(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await state.clear()
    await summary.open_summary(message, db, settings)


@router.message(F.text == MENU_JOURNAL)
async def open_journal(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await state.clear()
    await journal.open_journal(message, db, settings)


@router.message(F.text == MENU_SETTINGS)
async def open_settings(message: Message, state: FSMContext, db: Connection, settings: Row) -> None:
    await state.clear()
    await settings_handlers.open_settings(message, db, settings["user_id"])
