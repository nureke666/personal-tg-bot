"""Итоги за неделю, месяц или год по всем разделам сразу."""

from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiosqlite import Connection, Row

from bot.callbacks import SumCb
from bot.handlers.ui import show
from bot.services import summary
from bot.services import timeutil as tu
from bot.views import summary as views

router = Router(name="summary")


async def open_summary(
    event: Message | CallbackQuery, db: Connection, settings: Row, period: str = "week", anchor: date | None = None
) -> None:
    today = tu.local_today(settings["tz"])
    anchor = anchor or today
    data = await summary.build(db, settings["user_id"], settings["tz"], period, anchor, today)
    await show(event, views.summary_text(data, settings["currency"]), views.summary_kb(period, anchor, today))


@router.callback_query(SumCb.filter(F.p.in_(views.PERIODS)))
async def summary_period(
    callback: CallbackQuery, callback_data: SumCb, state: FSMContext, db: Connection, settings: Row
) -> None:
    await state.clear()
    anchor = tu.unpack_day(callback_data.d) if callback_data.d else None
    await open_summary(callback, db, settings, callback_data.p, anchor)
