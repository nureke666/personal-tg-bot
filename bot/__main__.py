import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from aiogram.utils.token import TokenValidationError
from aiosqlite import Connection
from pydantic import ValidationError

from bot import db as database
from bot.config import Config
from bot.handlers import (
    admin,
    common,
    countdowns,
    fallback,
    finance,
    habits,
    journal,
    menu,
    quick,
    settings,
    summary,
    tasks,
)
from bot.middlewares.access import AccessMiddleware
from bot.repo import access
from bot.services import scheduler
from bot.services import timeutil as tu

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
    BotCommand(command="help", description="Что я умею"),
]
OWNER_COMMANDS = [
    *COMMANDS,
    BotCommand(command="users", description="Кто пользуется ботом"),
    BotCommand(command="allow", description="Открыть доступ: /allow id"),
    BotCommand(command="deny", description="Закрыть доступ: /deny id"),
]


def build_dispatcher(db: Connection, config: Config) -> Dispatcher:
    # db и config попадают в хендлеры как одноимённые аргументы.
    dp = Dispatcher(storage=MemoryStorage(), db=db, config=config)
    dp.update.outer_middleware(AccessMiddleware(db, config.owner_id, config.default_tz))
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    # Порядок важен: меню перехватывает свои кнопки в любом состоянии, quick — текст вне мастеров,
    # fallback — всё остальное.
    dp.include_routers(
        menu.router,
        admin.router,
        common.router,
        tasks.router,
        countdowns.router,
        finance.router,
        habits.router,
        settings.router,
        summary.router,
        journal.router,
        quick.router,
        fallback.router,
    )
    return dp


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = Config()
    except ValidationError:
        log.error("Не задан BOT_TOKEN. Скопируй .env.example в .env и впиши токен от @BotFather.")
        return 1
    bad_token = "Telegram не принял BOT_TOKEN. Проверь токен в .env (его выдаёт @BotFather)."
    try:
        bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    except TokenValidationError:
        log.error(bad_token)
        return 1
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        log.error(bad_token)
        await bot.session.close()
        return 1

    db = await database.connect(config.db_path)
    dp = build_dispatcher(db, config)
    log.info("Бот @%s запущен, владелец: %s", me.username, config.owner_id or "не задан")
    if config.owner_id is None:
        log.warning("OWNER_ID не задан: напиши боту /start, он пришлёт твой id")
    await bot.set_my_commands(COMMANDS)
    if config.owner_id is not None:
        await access.sync_owner(db, config.owner_id, tu.utcnow())
        try:
            await bot.set_my_commands(OWNER_COMMANDS, scope=BotCommandScopeChat(chat_id=config.owner_id))
        except TelegramAPIError:
            log.warning("Не удалось показать владельцу команды доступа: он ещё не писал боту")

    ticker = asyncio.create_task(scheduler.run(bot, db, config.tick_seconds))
    try:
        await dp.start_polling(bot)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        await db.close()
        await bot.session.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
