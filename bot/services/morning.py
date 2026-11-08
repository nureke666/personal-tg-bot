"""Утренний план на день — привычки и задачи, закреплённый в чате.

В настройках помним, какое сообщение закреплено и за какой день: отметки из меню или из напоминания
перерисовывают закреп, чтобы он не отставал. Закреп — удобство, поэтому ошибки Telegram (сообщение
удалили, закрепы недоступны) только пишем в лог.
"""

import logging
from collections.abc import Awaitable
from datetime import date

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup
from aiosqlite import Connection, Row

from bot.repo import habits, settings
from bot.services import timeutil as tu
from bot.views import habits as views
from bot.views import tasks as task_views

log = logging.getLogger(__name__)


async def has_plan(db: Connection, user_id: int, day: date) -> bool:
    """Есть что закрепить: привычки на день, задачи на день или просроченные."""
    return bool(await habits.planned(db, user_id, day) or any(await task_views.plan_tasks(db, user_id, day)))


async def screen(db: Connection, user_id: int, tz: str, day: date, today: date) -> tuple[str, InlineKeyboardMarkup]:
    text, markup = await views.day_screen(db, user_id, tz, day, today, view=views.MORNING)
    lines = task_views.pin_lines(*await task_views.plan_tasks(db, user_id, day), day)
    if not lines and not await habits.planned(db, user_id, day):
        lines = ["🎉 На сегодня всё сделано."]  # закреп был ради задач, и их не осталось
    return "\n".join([text, *lines]), markup


async def replace_pin(
    bot: Bot, db: Connection, user: Row, message_id: int | None = None, day: date | None = None
) -> None:
    """Открепляет прошлый список и закрепляет новый. Без message_id — только открепить."""
    uid = user["user_id"]
    if user["morning_msg_id"]:
        # Именно это сообщение, а не все: свои закрепы человек мог сделать сам.
        await _quietly(bot.unpin_chat_message(uid, message_id=user["morning_msg_id"]))
    if message_id:
        await _quietly(bot.pin_chat_message(uid, message_id, disable_notification=True))
    await settings.update(
        db, uid, morning_msg_id=message_id, morning_msg_day=day.isoformat() if message_id and day else None
    )


async def refresh(bot: Bot, db: Connection, user_id: int, pressed_message_id: int | None = None) -> None:
    """Перерисовывает закреп после изменений в привычках и задачах. Если нажали прямо на нём, он уже
    перерисован."""
    user = await settings.get(db, user_id)
    message_id = user["morning_msg_id"]
    if not message_id or message_id == pressed_message_id:
        return
    tz = user["tz"]
    text, markup = await screen(db, user_id, tz, date.fromisoformat(user["morning_msg_day"]), tu.local_today(tz))
    await _quietly(bot.edit_message_text(text, chat_id=user_id, message_id=message_id, reply_markup=markup))


async def _quietly(request: Awaitable[object]) -> None:
    try:
        await request
    except TelegramAPIError as error:
        # «message is not modified» — изменилось то, чего в закрепе не видно (например, отметка за другой день).
        if "message is not modified" not in str(error):
            log.warning("Не получилось обновить закреп: %s", error)
