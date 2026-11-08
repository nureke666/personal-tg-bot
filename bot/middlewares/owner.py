import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, User
from aiosqlite import Connection

from bot.repo import settings

log = logging.getLogger(__name__)


class OwnerMiddleware(BaseMiddleware):
    """Пропускает только владельца и кладёт в data его настройки (data["settings"])."""

    def __init__(self, db: Connection, owner_id: int | None, default_tz: str) -> None:
        self.db = db
        self.owner_id = owner_id
        self.default_tz = default_tz

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return None
        if self.owner_id is None:
            # Владелец ещё не настроен: подсказываем id, больше ничего не делаем.
            if isinstance(event, Update) and event.message:
                log.info("Пишет %s (id=%s), а OWNER_ID не задан", user.full_name, user.id)
                await event.message.answer(
                    f"Твой Telegram id: <code>{user.id}</code>\n"
                    f"Впиши в файл .env строку <code>OWNER_ID={user.id}</code> и перезапусти бота."
                )
            return None
        if user.id != self.owner_id:
            return None
        data["settings"] = await settings.ensure(self.db, user.id, self.default_tz)
        return await handler(event, data)
