"""Бэкап базы: целостный снимок через VACUUM INTO, бот при этом продолжает работать."""

import tempfile
from datetime import date
from pathlib import Path

from aiogram.types import BufferedInputFile
from aiosqlite import Connection

from bot.db import execute
from bot.services.texts import fmt_date_long


async def snapshot(db: Connection, day: date) -> BufferedInputFile:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bot.db"
        await execute(db, "VACUUM INTO ?", (str(path),))
        return BufferedInputFile(path.read_bytes(), filename=f"bot-{day.isoformat()}.db")


def caption(day: date) -> str:
    return (
        f"💾 Бэкап базы, {fmt_date_long(day)}.\n"
        "Восстановить: останови бота, положи файл вместо data/bot.db и запусти снова."
    )
