"""Управление доступом: /allow, /deny, /users. Всё это видит и может только владелец."""

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, MagicData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.callback_answer import CallbackAnswer
from aiosqlite import Connection

from bot.callbacks import AccessCb
from bot.config import Config
from bot.handlers.ui import show
from bot.repo import access, journal
from bot.services import timeutil as tu
from bot.views.access import USAGE, person, users_view

router = Router(name="admin")
# Для всех остальных этих команд нет: их сообщения уйдут дальше, в обычные роутеры.
router.message.filter(MagicData(F.is_owner))
router.callback_query.filter(MagicData(F.is_owner))


def _parse_id(command: CommandObject) -> int | None:
    value = (command.args or "").strip()
    return int(value) if value.isdigit() else None


async def grant(db: Connection, bot: Bot, user_id: int, owner_id: int) -> str:
    if user_id == owner_id:
        return "Ты владелец — у тебя и так полный доступ."
    request = await access.get_request(db, user_id)
    name, username = (request["name"], request["username"]) if request else (None, None)
    if not await access.allow(db, user_id, name, username, tu.utcnow()):
        return f"У {person(name, username, user_id)} доступ уже есть."
    await journal.add(
        db, owner_id, f"🔓 Открыт доступ: {name} (id {user_id})" if name else f"🔓 Открыт доступ: id {user_id}"
    )
    text = f"✅ Доступ открыт: {person(name, username, user_id)}"
    try:
        await bot.send_message(user_id, "✅ Владелец открыл тебе доступ к боту. Нажми /start, чтобы начать.")
    except TelegramAPIError:
        text += "\nНаписать ему я не смог — пусть сам откроет бота и нажмёт /start."
    return text


async def revoke(db: Connection, user_id: int, owner_id: int) -> str:
    if user_id == owner_id:
        return "Себе доступ закрыть нельзя."
    if not await access.revoke(db, user_id):
        return f"У id <code>{user_id}</code> доступа и так нет."
    await journal.add(db, owner_id, f"🔒 Закрыт доступ: id {user_id}")
    return (
        f"🚫 Доступ закрыт для id <code>{user_id}</code>.\n"
        "Его данные остались в базе: если снова разрешишь, всё вернётся."
    )


async def users_screen(db: Connection) -> tuple[str, InlineKeyboardMarkup]:
    return users_view(await access.list_allowed(db), await access.list_requests(db))


@router.message(Command("allow"))
async def allow_command(message: Message, command: CommandObject, db: Connection, bot: Bot, config: Config) -> None:
    user_id = _parse_id(command)
    if user_id is None:
        text, markup = await users_screen(db)
        await message.answer(f"Укажи id: <code>/allow 123456789</code>\n\n{text}", reply_markup=markup)
        return
    await message.answer(await grant(db, bot, user_id, config.owner_id))


@router.message(Command("deny"))
async def deny_command(message: Message, command: CommandObject, db: Connection, config: Config) -> None:
    user_id = _parse_id(command)
    if user_id is None:
        text, markup = await users_screen(db)
        await message.answer(f"Укажи id: <code>/deny 123456789</code>\n\n{text}", reply_markup=markup)
        return
    await message.answer(await revoke(db, user_id, config.owner_id))


@router.message(Command("users"))
async def users_command(message: Message, db: Connection) -> None:
    text, markup = await users_screen(db)
    await message.answer(text, reply_markup=markup)


@router.callback_query(AccessCb.filter())
async def access_button(
    callback: CallbackQuery,
    callback_data: AccessCb,
    db: Connection,
    bot: Bot,
    config: Config,
    callback_answer: CallbackAnswer,
) -> None:
    act, user_id = callback_data.act, callback_data.id
    if act == "allow":
        result = await grant(db, bot, user_id, config.owner_id)
    elif act == "deny":
        result = await revoke(db, user_id, config.owner_id)
    elif act == "reject":
        await access.drop_request(db, user_id)
        await journal.add(db, config.owner_id, f"🚫 Отклонён запрос доступа: id {user_id}")
        result = f"Запрос от id <code>{user_id}</code> отклонён."
    else:
        result = None
    text, markup = await users_screen(db)
    await show(callback, f"{result}\n\n{text}" if result else text, markup)


HELP = f"\n\n👑 <b>Только для тебя</b>\n{USAGE}"
