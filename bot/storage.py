"""Состояние мастеров (FSM) в той же SQLite: перезапуск бота не сбрасывает начатое."""

import json
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from aiosqlite import Connection

from bot.db import execute, fetchone


def _key(key: StorageKey) -> str:
    # Бот один, поэтому bot_id в ключ не входит.
    return f"{key.chat_id}:{key.user_id}:{key.thread_id or ''}:{key.business_connection_id or ''}:{key.destiny}"


class SQLiteStorage(BaseStorage):
    def __init__(self, db: Connection) -> None:
        self.db = db

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await execute(
            self.db,
            "INSERT INTO fsm (key, state) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET state = excluded.state",
            (_key(key), value),
        )
        await self._drop_empty(key)

    async def get_state(self, key: StorageKey) -> str | None:
        row = await fetchone(self.db, "SELECT state FROM fsm WHERE key = ?", (_key(key),))
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await execute(
            self.db,
            "INSERT INTO fsm (key, data) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET data = excluded.data",
            (_key(key), json.dumps(dict(data), ensure_ascii=False)),
        )
        await self._drop_empty(key)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await fetchone(self.db, "SELECT data FROM fsm WHERE key = ?", (_key(key),))
        return json.loads(row["data"]) if row else {}

    async def close(self) -> None:
        # Соединение общее с ботом, его закрывает bot.__main__.
        pass

    async def _drop_empty(self, key: StorageKey) -> None:
        await execute(self.db, "DELETE FROM fsm WHERE key = ? AND state IS NULL AND data = '{}'", (_key(key),))
