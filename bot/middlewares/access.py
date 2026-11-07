import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, TelegramObject, Update, User
from aiosqlite import Connection

from bot.repo import access, settings
from bot.services import timeutil as tu
from bot.views.access import request_message

log = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    """Пропускает владельца и тех, кому он открыл доступ.

    Кладёт в data настройки пользователя (data["settings"]) и признак владельца (data["is_owner"]).
    Остальным подсказывает их id и отправляет владельцу запрос с кнопками.
    """

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
        message = event.message if isinstance(event, Update) else None
        if self.owner_id is None:
            # Владелец ещё не настроен: подсказываем id, больше ничего не делаем.
            if message:
                log.info("Пишет %s (id=%s), а OWNER_ID не задан", user.full_name, user.id)
                await message.answer(
                    f"Твой Telegram id: <code>{user.id}</code>\n"
                    f"Впиши в файл .env строку <code>OWNER_ID={user.id}</code> и перезапусти бота."
                )
            return None

        is_owner = user.id == self.owner_id
        if not is_owner and not await access.is_allowed(self.db, user.id):
            if message:
                await self._request_access(message, user, data["bot"])
            return None
        if not is_owner:
            await access.touch(self.db, user.id, user.full_name, user.username)

        data["is_owner"] = is_owner
        data["settings"] = await settings.ensure(self.db, user.id, self.default_tz)
        return await handler(event, data)

    async def _request_access(self, message: Message, user: User, bot: Bot) -> None:
        await message.answer(
            "🔒 Это личный бот, доступ к нему открывает владелец.\n"
            f"Твой id: <code>{user.id}</code>. Запрос владельцу отправлен — как только он разрешит, я напишу."
        )
        if not await access.register_request(self.db, user.id, user.full_name, user.username, tu.utcnow()):
            return
        log.info("Запрос доступа от %s (id=%s)", user.full_name, user.id)
        text, markup = request_message(user.id, user.full_name, user.username)
        try:
            await bot.send_message(self.owner_id, text, reply_markup=markup)
        except TelegramAPIError:
            log.warning("Не удалось отправить владельцу запрос доступа от %s", user.id)
