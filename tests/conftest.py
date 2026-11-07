import itertools
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.filters.callback_data import CallbackData
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    PinChatMessage,
    SendDocument,
    SendMessage,
    UnpinChatMessage,
)
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User

from bot import db as database
from bot.__main__ import build_dispatcher
from bot.config import Config
from bot.repo import access, settings

OWNER = 1001
TZ = "Asia/Almaty"
# Суббота, 17 октября 2026, 08:00 по Алматы (UTC+5).
NOW = datetime(2026, 10, 17, 3, 0, tzinfo=UTC)


@pytest.fixture
async def db():
    conn = await database.connect(":memory:")
    yield conn
    await conn.close()


@pytest.fixture
async def user(db):
    # Как при старте бота: владелец из OWNER_ID записывается в список доступа.
    await access.sync_owner(db, OWNER, NOW)
    return await settings.ensure(db, OWNER, TZ, NOW)


class FakeBot:
    """Для планировщика: просто запоминает отправленное."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Any]] = []
        self.documents: list[tuple[int, Any, str | None]] = []
        self.pinned: list[tuple[int, int]] = []
        self.unpinned: list[tuple[int, int]] = []
        self._ids = itertools.count(700)

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> SimpleNamespace:
        self.sent.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=next(self._ids))

    async def pin_chat_message(self, chat_id: int, message_id: int, disable_notification: bool | None = None) -> None:
        self.pinned.append((chat_id, message_id))

    async def unpin_chat_message(self, chat_id: int, message_id: int | None = None) -> None:
        self.unpinned.append((chat_id, message_id))

    async def send_document(
        self, chat_id: int, document: Any, caption: str | None = None, disable_notification: bool | None = None
    ) -> None:
        self.documents.append((chat_id, document, caption))


@pytest.fixture
def fake_bot() -> FakeBot:
    return FakeBot()


class RecordingSession(BaseSession):
    """Сессия без сети: записывает вызовы Bot API и отвечает правдоподобными заглушками."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Any] = []
        self._ids = itertools.count(10_000)

    async def make_request(self, bot: Bot, method: Any, timeout: int | None = None) -> Any:
        self.calls.append(method)
        if isinstance(method, (AnswerCallbackQuery, PinChatMessage, UnpinChatMessage)):
            return True
        return Message(
            message_id=next(self._ids),
            date=datetime.now(UTC),
            chat=Chat(id=OWNER, type="private"),
            text=getattr(method, "text", None),
        )

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError
        yield b""

    async def close(self) -> None:
        pass


class Harness:
    """Гоняет апдейты через настоящий Dispatcher со всеми роутерами и мидлварями."""

    def __init__(self, db, owner_id: int | None = OWNER) -> None:
        self.session = RecordingSession()
        self.bot = Bot("123456:TEST", session=self.session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = build_dispatcher(db, Config(bot_token="123456:TEST", owner_id=owner_id))
        self._ids = itertools.count(1)

    def _user(self, user_id: int) -> User:
        return User(id=user_id, is_bot=False, first_name="Тест")

    async def send(self, text: str, user_id: int = OWNER) -> None:
        message = Message(
            message_id=next(self._ids),
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=self._user(user_id),
            text=text,
        )
        await self.dp.feed_update(self.bot, Update(update_id=next(self._ids), message=message))

    async def click(
        self, data: CallbackData | str, message_text: str = "…", user_id: int = OWNER, message_id: int = 500
    ) -> None:
        message = Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=123456, is_bot=True, first_name="Бот"),
            text=message_text,
        )
        callback = CallbackQuery(
            id=str(next(self._ids)),
            from_user=self._user(user_id),
            chat_instance="test",
            data=data.pack() if isinstance(data, CallbackData) else data,
            message=message,
        )
        await self.dp.feed_update(self.bot, Update(update_id=next(self._ids), callback_query=callback))

    def screens(self) -> list[Any]:
        return [c for c in self.session.calls if isinstance(c, (SendMessage, EditMessageText))]

    @property
    def text(self) -> str:
        return self.screens()[-1].text

    @property
    def markup(self) -> InlineKeyboardMarkup:
        return self.screens()[-1].reply_markup

    def button(self, label_part: str) -> str:
        """callback_data кнопки на последнем экране, в надписи которой есть label_part."""
        for row in self.markup.inline_keyboard:
            for button in row:
                if label_part in button.text:
                    return button.callback_data
        labels = [b.text for row in self.markup.inline_keyboard for b in row]
        raise AssertionError(f"Нет кнопки «{label_part}» среди {labels}")

    @property
    def labels(self) -> list[str]:
        return [button.text for row in self.markup.inline_keyboard for button in row]

    @property
    def toasts(self) -> list[str | None]:
        return [c.text for c in self.session.calls if isinstance(c, AnswerCallbackQuery)]

    @property
    def documents(self) -> list[SendDocument]:
        return [c for c in self.session.calls if isinstance(c, SendDocument)]

    def calls(self, method: type) -> list[Any]:
        return [c for c in self.session.calls if isinstance(c, method)]

    async def close(self) -> None:
        await self.bot.session.close()
        # Роутеры — синглтоны модулей, а aiogram разрешает подключить роутер только к одному
        # диспетчеру. Отцепляем их, чтобы следующий тест собрал свой диспетчер.
        for router in self.dp.sub_routers:
            router._parent_router = None
        self.dp.sub_routers.clear()


@pytest.fixture
async def make_harness(db):
    created: list[Harness] = []

    def factory(owner_id: int | None = OWNER) -> Harness:
        created.append(Harness(db, owner_id))
        return created[-1]

    yield factory
    for h in created:
        await h.close()


@pytest.fixture
def harness(make_harness) -> Harness:
    return make_harness()
