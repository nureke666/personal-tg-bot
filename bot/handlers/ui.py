"""Общие приёмы отрисовки экранов."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message


async def show(event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """По нажатию кнопки — редактируем то же сообщение, на текст — отвечаем новым."""
    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
        try:
            await event.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                return
        await event.message.answer(text, reply_markup=markup)
        return
    message = event.message if isinstance(event, CallbackQuery) else event
    await message.answer(text, reply_markup=markup)


def chat_message(event: Message | CallbackQuery) -> Message:
    return event.message if isinstance(event, CallbackQuery) else event
