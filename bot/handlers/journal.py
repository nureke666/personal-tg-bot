"""Журнал: последние действия пользователя, по 10, 25 или 50 за раз."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiosqlite import Connection, Row

from bot.callbacks import JournalCb
from bot.handlers.ui import show
from bot.repo import journal
from bot.services import timeutil as tu
from bot.views import journal as views

router = Router(name="journal")


async def open_journal(
    event: Message | CallbackQuery, db: Connection, settings: Row, limit: int = views.LIMITS[0]
) -> None:
    entries = await journal.recent(db, settings["user_id"], limit)
    await show(event, *views.journal_view(entries, limit, settings["tz"], tu.local_today(settings["tz"])))


@router.callback_query(JournalCb.filter(F.n.in_(views.LIMITS)))
async def journal_limit(
    callback: CallbackQuery, callback_data: JournalCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    await state.clear()
    await open_journal(callback, db, settings, callback_data.n)
