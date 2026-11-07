import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.types import BotCommand, BotCommandScopeChat
from aiogram.utils.callback_answer import CallbackAnswerMiddleware
from aiogram.utils.token import TokenValidationError
from aiosqlite import Connection
from pydantic import ValidationError

from bot import db as database
from bot.config import Config
from bot.handlers import (
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
from bot.middlewares.owner import OwnerMiddleware
from bot.services import scheduler
from bot.storage import SQLiteStorage

log = logging.getLogger("bot")

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
    BotCommand(command="help", description="Что я умею"),
]


def build_dispatcher(db: Connection, config: Config) -> Dispatcher:
    # db и config попадают в хендлеры как одноимённые аргументы.
    dp = Dispatcher(storage=SQLiteStorage(db), db=db, config=config)
    dp.update.outer_middleware(OwnerMiddleware(db, config.owner_id, config.default_tz))
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    # Порядок важен: меню перехватывает свои кнопки в любом состоянии, quick — текст вне мастеров,
    # fallback — всё остальное.
    dp.include_routers(
        menu.router,
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
        # Прежняя версия ставила владельцу /users, /allow, /deny отдельным списком — убираем его.
        with contextlib.suppress(TelegramAPIError):
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=config.owner_id))

    ticker = asyncio.create_task(scheduler.run(bot, db, config.tick_seconds, config.owner_id))
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
