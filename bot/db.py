from pathlib import Path

import aiosqlite

# Каждый элемент — одна миграция. Номер применённой хранится в PRAGMA user_version.
MIGRATIONS = [
    """
    CREATE TABLE settings (
        user_id         INTEGER PRIMARY KEY,
        tz              TEXT    NOT NULL,
        currency        TEXT    NOT NULL DEFAULT '₸',
        start_balance   INTEGER NOT NULL DEFAULT 0,
        digest_on       INTEGER NOT NULL DEFAULT 1,
        digest_time     TEXT    NOT NULL DEFAULT '08:00',
        digest_next_at  TEXT,
        checkin_on      INTEGER NOT NULL DEFAULT 1,
        checkin_time    TEXT    NOT NULL DEFAULT '21:00',
        checkin_next_at TEXT,
        month_limit     INTEGER
    );

    CREATE TABLE tasks (
        id          INTEGER PRIMARY KEY,
        user_id     INTEGER NOT NULL,
        title       TEXT    NOT NULL,
        details     TEXT,
        due_date    TEXT,
        due_time    TEXT,
        repeat      TEXT    NOT NULL DEFAULT 'none',
        anchor_day  INTEGER,
        status      TEXT    NOT NULL DEFAULT 'open',
        created_at  TEXT    NOT NULL,
        done_at     TEXT
    );
    CREATE INDEX ix_tasks_user ON tasks(user_id, status, due_date);

    CREATE TABLE task_reminders (
        id          INTEGER PRIMARY KEY,
        task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        offset_min  INTEGER,
        fire_at     TEXT    NOT NULL,
        sent_at     TEXT
    );
    CREATE INDEX ix_reminders_due ON task_reminders(sent_at, fire_at);

    CREATE TABLE countdowns (
        id          INTEGER PRIMARY KEY,
        user_id     INTEGER NOT NULL,
        title       TEXT    NOT NULL,
        text        TEXT,
        target_date TEXT    NOT NULL,
        send_time   TEXT    NOT NULL,
        status      TEXT    NOT NULL DEFAULT 'active',
        next_run_at TEXT
    );

    CREATE TABLE categories (
        id          INTEGER PRIMARY KEY,
        user_id     INTEGER NOT NULL,
        kind        TEXT    NOT NULL,
        name        TEXT    NOT NULL,
        month_limit INTEGER,
        archived    INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE transactions (
        id          INTEGER PRIMARY KEY,
        user_id     INTEGER NOT NULL,
        kind        TEXT    NOT NULL,
        amount      INTEGER NOT NULL,
        category_id INTEGER NOT NULL REFERENCES categories(id),
        comment     TEXT,
        op_date     TEXT    NOT NULL,
        created_at  TEXT    NOT NULL
    );
    CREATE INDEX ix_transactions_user ON transactions(user_id, op_date);

    CREATE TABLE habits (
        id          INTEGER PRIMARY KEY,
        user_id     INTEGER NOT NULL,
        title       TEXT    NOT NULL,
        days_mask   INTEGER NOT NULL DEFAULT 127,
        remind_time TEXT,
        next_run_at TEXT,
        archived    INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL
    );

    CREATE TABLE habit_marks (
        habit_id    INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
        date        TEXT    NOT NULL,
        PRIMARY KEY (habit_id, date)
    );
    """,
    """
    CREATE TABLE allowed_users (
        user_id     INTEGER PRIMARY KEY,
        name        TEXT,
        username    TEXT,
        is_owner    INTEGER NOT NULL DEFAULT 0,
        added_at    TEXT    NOT NULL
    );

    CREATE TABLE access_requests (
        user_id      INTEGER PRIMARY KEY,
        name         TEXT,
        username     TEXT,
        requested_at TEXT    NOT NULL
    );
    """,
    # Ночной бэкап базы владельцу. backup_next_at = NULL — прислать при ближайшем тике.
    """
    ALTER TABLE settings ADD COLUMN backup_on      INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE settings ADD COLUMN backup_time    TEXT    NOT NULL DEFAULT '03:00';
    ALTER TABLE settings ADD COLUMN backup_next_at TEXT;
    """,
    # Привычки-отказы («🚫 Без сладкого»): у kind = 'quit' отметки в habit_marks — это срывы,
    # since — день, с которого человек держится.
    """
    ALTER TABLE habits ADD COLUMN kind  TEXT NOT NULL DEFAULT 'check';
    ALTER TABLE habits ADD COLUMN since TEXT;
    """,
    # Ежегодные отсчёты (дни рождения): после события дата сдвигается на год вперёд.
    # lead_days — писать только за столько дней до события, NULL — каждый день.
    """
    ALTER TABLE countdowns ADD COLUMN yearly    INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE countdowns ADD COLUMN lead_days INTEGER;
    """,
    # Журнал выполненных задач — для итогов за прошлые периоды. У повторяющейся задачи строка
    # на каждое выполнение. Задачу можно удалить, журнал останется (поэтому без внешнего ключа).
    # Итоги недели по воскресеньям: weekly_next_at = NULL — просто запланировать, без отправки.
    """
    CREATE TABLE task_log (
        id      INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        task_id INTEGER,
        title   TEXT    NOT NULL,
        done_at TEXT    NOT NULL
    );
    CREATE INDEX ix_task_log_user ON task_log(user_id, done_at);
    INSERT INTO task_log (user_id, task_id, title, done_at)
        SELECT user_id, id, title, done_at FROM tasks WHERE status = 'done' AND done_at IS NOT NULL;

    ALTER TABLE settings ADD COLUMN weekly_on      INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE settings ADD COLUMN weekly_time    TEXT    NOT NULL DEFAULT '20:00';
    ALTER TABLE settings ADD COLUMN weekly_next_at TEXT;
    """,
    # Журнал действий: что человек создал, изменил, отметил или удалил. Текст хранится готовым,
    # поэтому запись остаётся, даже если саму задачу или операцию потом удалили.
    """
    CREATE TABLE action_log (
        id      INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        at      TEXT    NOT NULL,
        text    TEXT    NOT NULL
    );
    CREATE INDEX ix_action_log_user ON action_log(user_id, id);
    """,
    # Утренний список привычек, который бот закрепляет в чате. Включён только у владельца: остальным
    # без спроса ничего не закрепляем. В базе старше списка доступа (миграция 2) владелец ещё не записан,
    # но там все настройки — его: бот тогда отвечал только владельцу.
    # morning_next_at = NULL — просто запланировать, без отправки.
    # morning_msg_id / morning_msg_day — что сейчас закреплено и за какой день.
    """
    ALTER TABLE settings ADD COLUMN morning_on      INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE settings ADD COLUMN morning_time    TEXT    NOT NULL DEFAULT '06:00';
    ALTER TABLE settings ADD COLUMN morning_next_at TEXT;
    ALTER TABLE settings ADD COLUMN morning_msg_id  INTEGER;
    ALTER TABLE settings ADD COLUMN morning_msg_day TEXT;
    UPDATE settings SET morning_on = 1
        WHERE user_id IN (SELECT user_id FROM allowed_users WHERE is_owner = 1)
           OR NOT EXISTS (SELECT 1 FROM allowed_users);
    """,
    # Часть дня у привычки: по ней список на день делится на «Утром», «Днём» и «Вечером». Старым
    # привычкам часть угадываем по времени напоминания, без напоминания — утро.
    """
    ALTER TABLE habits ADD COLUMN part TEXT NOT NULL DEFAULT 'morning';
    UPDATE habits SET part = CASE WHEN remind_time >= '18:00' THEN 'evening' WHEN remind_time >= '12:00' THEN 'day'
        ELSE 'morning' END
        WHERE remind_time IS NOT NULL;
    """,
]


async def connect(path: str) -> aiosqlite.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # Автокоммит: каждая операция фиксируется сразу, транзакции между корутинами не переплетаются.
    conn = await aiosqlite.connect(path, isolation_level=None)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        await conn.execute("PRAGMA journal_mode = WAL")
    await migrate(conn)
    return conn


async def migrate(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA user_version") as cur:
        (version,) = await cur.fetchone()
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        await conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")


async def fetchone(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
    async with conn.execute(sql, params) as cur:
        return await cur.fetchone()


async def fetchall(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
    async with conn.execute(sql, params) as cur:
        return list(await cur.fetchall())


async def scalar(conn: aiosqlite.Connection, sql: str, params: tuple = ()):
    row = await fetchone(conn, sql, params)
    return row[0] if row else None


async def execute(conn: aiosqlite.Connection, sql: str, params: tuple = ()) -> int:
    """Выполняет запрос и возвращает lastrowid."""
    async with conn.execute(sql, params) as cur:
        return cur.lastrowid or 0
