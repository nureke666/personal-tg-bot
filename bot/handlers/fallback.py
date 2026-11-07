"""Всё, что не поймали остальные роутеры. Подключается последним."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer

from bot.keyboards.common import main_menu

router = Router(name="fallback")


@router.message(F.pinned_message)
async def pinned(message: Message) -> None:
    """Служебное «закрепил сообщение» — человек закрепил что-то сам, отвечать не на что."""


@router.message()
async def unknown_message(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        await message.answer("Здесь нужен текст или кнопка из сообщения выше. Отменить — /cancel.")
        return
    await message.answer("Я понимаю кнопки меню 👇", reply_markup=main_menu())


@router.callback_query()
async def stale_button(callback: CallbackQuery, callback_answer: CallbackAnswer) -> None:
    callback_answer.text = "Эта кнопка устарела. Открой раздел заново через меню."
