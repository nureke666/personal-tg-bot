"""Репозитории и планировщик на фиксированном времени."""

from datetime import date, timedelta

import aiosqlite

from bot import db as database
from bot.repo import countdowns, finance, habits, settings, tasks
from bot.services import scheduler
from bot.services import timeutil as tu
from bot.services.planning import reschedule_all
from tests.conftest import NOW, OWNER, TZ

TODAY = date(2026, 10, 17)


async def _task(db, **draft) -> int:
    base = {"title": "Купить хлеб", "due_date": TODAY.isoformat(), "due_time": "10:00", "offsets": [0]}
    return await tasks.create(db, OWNER, base | draft, TZ, NOW)


async def test_reminder_is_sent_exactly_once(db, user, fake_bot):
    await _task(db, offsets=[0, 60])  # 10:00 и 09:00 по Алматы = 05:00 и 04:00 UTC
    await scheduler.tick(fake_bot, db, NOW, OWNER)
    assert fake_bot.sent == []

    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=1, seconds=1), OWNER)
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=1, seconds=31), OWNER)
    assert len(fake_bot.sent) == 1
    chat_id, text, markup = fake_bot.sent[0]
    assert chat_id == OWNER and "Купить хлеб" in text and "опозданием" not in text
    assert markup.inline_keyboard[0][0].callback_data.startswith("sz:done")

    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=2, seconds=1), OWNER)
    assert len(fake_bot.sent) == 2


async def test_after_downtime_one_late_reminder(db, user, fake_bot):
    task_id = await _task(db, offsets=[0, 15, 60])
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=5), OWNER)
    assert len(fake_bot.sent) == 1
    assert "опозданием" in fake_bot.sent[0][1]
    assert await tasks.pending_reminders(db, task_id) == 0


async def test_past_reminders_are_not_sent_on_create(db, user, fake_bot):
    # Задача на 08:30, напоминание «за 1 ч» (07:30) уже в прошлом.
    await _task(db, due_time="08:30", offsets=[0, 60])
    await scheduler.tick(fake_bot, db, NOW + timedelta(minutes=31), OWNER)
    assert len(fake_bot.sent) == 1


async def test_done_task_is_not_reminded(db, user, fake_bot):
    task_id = await _task(db)
    await tasks.complete(db, await tasks.get(db, OWNER, task_id), TZ, NOW)
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=3), OWNER)
    assert fake_bot.sent == []


async def test_complete_recurring_moves_to_next_date(db, user):
    task_id = await _task(db, repeat=tu.REPEAT_DAILY, offsets=[0, 60])
    next_date = await tasks.complete(db, await tasks.get(db, OWNER, task_id), TZ, NOW)
    task = await tasks.get(db, OWNER, task_id)
    assert next_date == TODAY + timedelta(days=1)
    assert task["status"] == tasks.OPEN and task["due_date"] == "2026-10-18"
    assert await tasks.offsets(db, task_id) == [0, 60]
    assert await tasks.pending_reminders(db, task_id) == 2


async def test_missed_recurring_rolls_over_and_keeps_reminding(db, user, fake_bot):
    # Ежедневная задача на 09:00, которую вчера забыли отметить.
    yesterday = NOW - timedelta(days=1)
    task_id = await tasks.create(
        db,
        OWNER,
        {"title": "Таблетки", "due_date": "2026-10-16", "due_time": "09:00", "offsets": [0], "repeat": "daily"},
        TZ,
        yesterday,
    )
    await scheduler.tick(fake_bot, db, yesterday + timedelta(hours=1, seconds=1), OWNER)
    assert len(fake_bot.sent) == 1

    await scheduler.tick(fake_bot, db, NOW, OWNER)  # наступило 17-е: задача должна переехать сама
    task = await tasks.get(db, OWNER, task_id)
    assert task["due_date"] == TODAY.isoformat() and task["status"] == tasks.OPEN

    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=1, seconds=1), OWNER)
    assert len(fake_bot.sent) == 2 and "Таблетки" in fake_bot.sent[1][1]


async def test_countdown_daily_until_the_day(db, user, fake_bot):
    countdown_id = await countdowns.create(db, OWNER, "🏖 Отпуск", None, "2026-10-19", "09:00", TZ, NOW)
    for day, expected in ((0, "2 дня"), (1, "1 день")):
        await scheduler.tick(fake_bot, db, NOW + timedelta(days=day, hours=1, seconds=1), OWNER)
        assert expected in fake_bot.sent[-1][1]
    await scheduler.tick(fake_bot, db, NOW + timedelta(days=1, hours=2), OWNER)  # повторно в тот же день не шлём
    assert len(fake_bot.sent) == 2

    await scheduler.tick(fake_bot, db, NOW + timedelta(days=2, hours=1, seconds=1), OWNER)
    assert "сегодня" in fake_bot.sent[-1][1]
    assert (await countdowns.get(db, OWNER, countdown_id))["status"] == countdowns.DONE
    await scheduler.tick(fake_bot, db, NOW + timedelta(days=3, hours=1, seconds=1), OWNER)
    assert len(fake_bot.sent) == 3


async def test_yearly_countdown_writes_only_before_and_rolls_over(db, user, fake_bot):
    countdown_id = await countdowns.create(
        db, OWNER, "🎂 ДР", None, "2026-10-27", "09:00", TZ, NOW, yearly=True, lead_days=7
    )
    at_nine = NOW + timedelta(hours=1, seconds=1)  # 09:00 по Алматы
    await scheduler.tick(fake_bot, db, at_nine, OWNER)  # осталось 10 дней — молчим
    await scheduler.tick(fake_bot, db, at_nine + timedelta(days=2), OWNER)  # 8 дней — молчим
    assert fake_bot.sent == []
    await scheduler.tick(fake_bot, db, at_nine + timedelta(days=3), OWNER)
    assert "7 дней" in fake_bot.sent[-1][1]

    await scheduler.tick(fake_bot, db, at_nine + timedelta(days=10), OWNER)
    assert "сегодня" in fake_bot.sent[-1][1]
    row = await countdowns.get(db, OWNER, countdown_id)
    assert row["target_date"] == "2027-10-27" and row["status"] == countdowns.ACTIVE
    await scheduler.tick(fake_bot, db, at_nine + timedelta(days=11), OWNER)
    assert len(fake_bot.sent) == 2


async def test_yearly_countdown_missed_while_offline_moves_to_next_year(db, user, fake_bot):
    countdown_id = await countdowns.create(db, OWNER, "Годовщина", None, "2026-10-10", "09:00", TZ, NOW, yearly=True)
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=2), OWNER)
    assert fake_bot.sent == []
    assert (await countdowns.get(db, OWNER, countdown_id))["target_date"] == "2027-10-10"


async def test_paused_countdown_is_silent(db, user, fake_bot):
    countdown_id = await countdowns.create(db, OWNER, "Экзамен", None, "2026-11-01", "09:00", TZ, NOW)
    countdown = await countdowns.get(db, OWNER, countdown_id)
    await countdowns.set_status(db, countdown, countdowns.PAUSED, TZ, NOW)
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=2), OWNER)
    assert fake_bot.sent == []


async def test_habit_reminder_skipped_when_already_marked(db, user, fake_bot):
    water = await habits.create(db, OWNER, "💧 Вода", tu.ALL_DAYS, "10:00", TZ, NOW)
    read = await habits.create(db, OWNER, "📚 Чтение", tu.ALL_DAYS, "10:00", TZ, NOW)
    await habits.set_mark(db, water, TODAY)
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=2, seconds=1), OWNER)
    assert [text for _, text, _ in fake_bot.sent] == ["⏰ Время для привычки: <b>📚 Чтение</b>"]
    next_runs = {h["id"]: h["next_run_at"] for h in await habits.list_active(db, OWNER)}
    assert next_runs[water] == next_runs[read] == "2026-10-18 05:00:00"


async def test_morning_digest(db, user, fake_bot):
    await _task(db, title="Сегодняшняя")
    await _task(db, title="Старая", due_date="2026-10-10")
    await scheduler.tick(fake_bot, db, NOW + timedelta(days=1), OWNER)  # 18-е, 08:00
    text = fake_bot.sent[-1][1]
    assert "Доброе утро" in text and "Просрочено: 2" in text


async def test_digest_without_tasks_is_silent_but_rescheduled(db, user, fake_bot):
    await scheduler.tick(fake_bot, db, NOW + timedelta(days=1), OWNER)
    assert fake_bot.sent == []
    assert (await settings.get(db, OWNER))["digest_next_at"] == "2026-10-19 03:00:00"


async def test_evening_checkin_lists_unmarked_habits(db, user, fake_bot):
    await habits.create(db, OWNER, "💧 Вода", tu.ALL_DAYS, None, TZ, NOW)
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=13), OWNER)  # 21:00 по Алматы
    assert len(fake_bot.sent) == 1
    _, text, markup = fake_bot.sent[0]
    assert "Как прошёл день" in text and "1. ⬜ 💧 Вода" in text
    icon, button = markup.inline_keyboard[0]
    assert icon.text == "🌅" and button.text == "1" and button.callback_data.endswith(":pm")


def _mornings(fake_bot) -> list:
    return [markup for _, text, markup in fake_bot.sent if text.startswith("🌅")]


def _labels(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


async def test_morning_list_is_pinned_instead_of_yesterdays(db, user, fake_bot):
    await habits.create(db, OWNER, "💧 Вода", tu.ALL_DAYS, None, TZ, NOW)
    await habits.create(db, OWNER, "🇬🇧 Английский", tu.WEEKDAYS, None, TZ, NOW)
    await habits.create(db, OWNER, "📚 Чтение", tu.ALL_DAYS, "21:00", TZ, NOW)
    sunday_6am = NOW + timedelta(hours=22)  # 18-е, 06:00 по Алматы
    await scheduler.tick(fake_bot, db, sunday_6am, OWNER)
    [(_, text, markup)] = [m for m in fake_bot.sent if m[1].startswith("🌅")]
    assert text.startswith("🌅 <b>Привычки · вс, 18 окт</b>")
    assert "1. ⬜ 💧 Вода" in text and "3. ⬜ 📚 Чтение · ⏰ 21:00" in text and "Английский" not in text
    # Привычка «по будням» в воскресенье пропускается, её номер не достаётся соседке.
    assert _labels(markup) == ["🌅", "1", "3"]
    [(chat_id, first_id)] = fake_bot.pinned
    row = await settings.get(db, OWNER)
    assert chat_id == OWNER and row["morning_msg_id"] == first_id and row["morning_msg_day"] == "2026-10-18"
    assert row["morning_next_at"] == "2026-10-19 01:00:00"

    await scheduler.tick(fake_bot, db, sunday_6am + timedelta(days=1), OWNER)
    assert [_labels(m) for m in _mornings(fake_bot)] == [["🌅", "1", "3"], ["🌅", "1", "2", "3"]]
    assert fake_bot.unpinned == [(OWNER, first_id)]
    second_id = fake_bot.pinned[-1][1]
    assert second_id != first_id and (await settings.get(db, OWNER))["morning_msg_id"] == second_id


async def test_morning_list_skips_free_day_but_unpins_yesterdays(db, user, fake_bot):
    await habits.create(db, OWNER, "🇬🇧 Английский", tu.WEEKDAYS, None, TZ, NOW)
    await settings.update(db, OWNER, morning_msg_id=555, morning_msg_day="2026-10-17")
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=22), OWNER)  # воскресенье, 06:00
    assert fake_bot.sent == [] and fake_bot.pinned == []
    assert fake_bot.unpinned == [(OWNER, 555)]
    row = await settings.get(db, OWNER)
    assert row["morning_msg_id"] is None and row["morning_next_at"] == "2026-10-19 01:00:00"


async def test_morning_list_right_after_update_is_only_scheduled(db, user, fake_bot):
    await habits.create(db, OWNER, "💧 Вода", tu.ALL_DAYS, None, TZ, NOW)
    await settings.update(db, OWNER, morning_next_at=None)  # так будет у всех сразу после миграции
    await scheduler.tick(fake_bot, db, NOW, OWNER)
    assert fake_bot.sent == [] and fake_bot.pinned == []
    assert (await settings.get(db, OWNER))["morning_next_at"] == "2026-10-18 01:00:00"


async def test_quit_habits_do_not_trigger_checkin(db, user, fake_bot):
    await habits.create(db, OWNER, "🚭 Не курю", tu.ALL_DAYS, None, TZ, NOW, kind=habits.QUIT, since=TODAY)
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=13), OWNER)
    assert fake_bot.sent == []


async def test_timezone_change_keeps_local_times(db, user):
    task_id = await _task(db, due_date="2026-10-20")
    await settings.update(db, OWNER, tz="Europe/Moscow")
    await reschedule_all(db, OWNER, NOW)
    rows = await db.execute_fetchall("SELECT fire_at FROM task_reminders WHERE task_id = ?", (task_id,))
    assert [r[0] for r in rows] == ["2026-10-20 07:00:00"]  # 10:00 по Москве
    assert (await settings.get(db, OWNER))["digest_next_at"] == "2026-10-17 05:00:00"


async def test_finance_totals_and_balance(db, user):
    food = (await finance.categories(db, OWNER, finance.EXPENSE))[0]
    salary = (await finance.categories(db, OWNER, finance.INCOME))[0]
    await finance.add_op(db, OWNER, finance.INCOME, 50_000_000, salary["id"], None, TODAY, NOW)
    await finance.add_op(db, OWNER, finance.EXPENSE, 150_000, food["id"], "кофе", TODAY, NOW)
    await finance.add_op(db, OWNER, finance.EXPENSE, 300_000, food["id"], None, date(2026, 9, 30), NOW)

    assert await finance.totals(db, OWNER, date(2026, 10, 1), date(2026, 10, 31)) == (50_000_000, 150_000)
    assert await finance.spent(db, OWNER, date(2026, 10, 1), date(2026, 10, 31), food["id"]) == 150_000
    card = (await finance.accounts(db, OWNER))[0]
    await finance.set_account_start(db, card["id"], 1_000_000)
    assert await finance.balance(db, OWNER) == 1_000_000 + 50_000_000 - 450_000
    by_cat = await finance.by_category(db, OWNER, finance.EXPENSE, None, None)
    assert [(r["name"], r["total"]) for r in by_cat] == [(food["name"], 450_000)]


async def test_default_categories_seeded_once(db, user):
    await settings.ensure(db, OWNER, TZ, NOW)
    names = [c["name"] for c in await finance.categories(db, OWNER, finance.EXPENSE)]
    assert len(names) == len(set(names)) == len(finance.DEFAULT_CATEGORIES[finance.EXPENSE])


async def test_habit_mark_toggles_on_and_off(db, user):
    habit_id = await habits.create(db, OWNER, "🏃 Бег", tu.ALL_DAYS, None, TZ, NOW)
    assert await habits.toggle_mark(db, habit_id, TODAY) is True
    assert await habits.marked_ids(db, OWNER, TODAY) == {habit_id}
    assert await habits.toggle_mark(db, habit_id, TODAY) is False
    assert await habits.marks(db, habit_id) == set()
    await habits.set_mark(db, habit_id, TODAY)
    await habits.set_mark(db, habit_id, TODAY)  # повторная отметка из напоминания не ломается
    assert await habits.marks(db, habit_id) == {TODAY}


async def test_owner_gets_nightly_backup(db, user, fake_bot):
    await settings.update(db, OWNER, backup_next_at=None)  # как у владельца сразу после обновления
    await scheduler.tick(fake_bot, db, NOW, OWNER)
    [(chat_id, file, caption)] = fake_bot.documents
    assert chat_id == OWNER and file.filename == "bot-2026-10-17.db" and "Бэкап базы" in caption
    assert file.data.startswith(b"SQLite format 3\x00")
    assert (await settings.get(db, OWNER))["backup_next_at"] == "2026-10-17 22:00:00"  # 03:00 по Алматы

    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=1), OWNER)
    assert len(fake_bot.documents) == 1
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=19), OWNER)
    assert len(fake_bot.documents) == 2


async def test_backup_only_for_owner_and_can_be_disabled(db, user, fake_bot):
    guest = 2002
    await settings.ensure(db, guest, TZ, NOW)
    await settings.update(db, guest, backup_next_at=None)
    await settings.update(db, OWNER, backup_on=0, backup_next_at=None)
    await scheduler.tick(fake_bot, db, NOW + timedelta(days=1), OWNER)
    assert fake_bot.documents == []


SUNDAY_EVENING = NOW + timedelta(days=1, hours=12, seconds=1)  # 18 октября, 20:00 по Алматы


async def test_weekly_summary_on_sunday(db, user, fake_bot):
    task_id = await _task(db, title="Зарядка", repeat=tu.REPEAT_DAILY)
    await tasks.complete(db, await tasks.get(db, OWNER, task_id), TZ, NOW)
    await tasks.complete(db, await tasks.get(db, OWNER, task_id), TZ, NOW + timedelta(days=1))
    food = (await finance.categories(db, OWNER, finance.EXPENSE))[0]
    await finance.add_op(db, OWNER, finance.EXPENSE, 300_000, food["id"], None, TODAY, NOW)
    await finance.add_op(db, OWNER, finance.EXPENSE, 200_000, food["id"], None, date(2026, 10, 5), NOW)

    await scheduler.tick(fake_bot, db, SUNDAY_EVENING - timedelta(minutes=1), OWNER)
    assert not [text for _, text, _ in fake_bot.sent if "Итоги" in text]
    await scheduler.tick(fake_bot, db, SUNDAY_EVENING, OWNER)
    [text] = [text for _, text, _ in fake_bot.sent if "Итоги" in text]
    assert "Неделя 12.10–18.10" in text and "выполнено 2" in text and "Зарядка ×2" in text
    assert "Расходы: 3 000 ₸ (↑50% к прошлой неделе)" in text
    assert (await settings.get(db, OWNER))["weekly_next_at"] == "2026-10-25 15:00:00"


async def test_weekly_summary_skips_empty_week_and_first_run_after_update(db, user, fake_bot):
    await scheduler.tick(fake_bot, db, SUNDAY_EVENING, OWNER)
    assert not [text for _, text, _ in fake_bot.sent if "Итоги" in text]

    # Сразу после обновления weekly_next_at пустой: итоги не шлём, только планируем.
    food = (await finance.categories(db, OWNER, finance.EXPENSE))[0]
    await finance.add_op(db, OWNER, finance.EXPENSE, 100, food["id"], None, TODAY, NOW)
    await settings.update(db, OWNER, weekly_next_at=None)
    await scheduler.tick(fake_bot, db, NOW, OWNER)
    assert not [text for _, text, _ in fake_bot.sent if "Итоги" in text]
    assert (await settings.get(db, OWNER))["weekly_next_at"] == "2026-10-18 15:00:00"


async def test_task_log_keeps_completions_and_undo(db, user):
    task_id = await _task(db, title="Разовая")
    task = await tasks.get(db, OWNER, task_id)
    await tasks.complete(db, task, TZ, NOW)
    await tasks.reopen(db, await tasks.get(db, OWNER, task_id), TZ, NOW)
    await tasks.complete(db, await tasks.get(db, OWNER, task_id), TZ, NOW)
    await tasks.delete(db, task_id)  # задачу удалили — в истории она остаётся
    rows = await tasks.done_between(db, OWNER, NOW - timedelta(days=1), NOW + timedelta(days=1))
    assert [r["title"] for r in rows] == ["Разовая"]


async def test_migration_fills_task_log_from_done_tasks():
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    for number, script in enumerate(database.MIGRATIONS[:5], start=1):
        await conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
    await conn.execute(
        "INSERT INTO tasks (user_id, title, status, created_at, done_at)"
        " VALUES (1, 'Старое', 'done', '2026-01-01 10:00:00', '2026-01-02 10:00:00')"
    )
    await database.migrate(conn)
    rows = await conn.execute_fetchall("SELECT user_id, title, done_at FROM task_log")
    assert [tuple(r) for r in rows] == [(1, "Старое", "2026-01-02 10:00:00")]
    await conn.close()


async def test_migration_turns_on_morning_list_only_for_owner():
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    # Незакрытое соединение держит поток aiosqlite, и упавший тест повесил бы весь прогон.
    try:
        for number, script in enumerate(database.MIGRATIONS[:7], start=1):
            await conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
        await conn.execute(
            "INSERT INTO allowed_users (user_id, is_owner, added_at)"
            " VALUES (1, 1, '2026-01-01 00:00:00'), (2, 0, '2026-01-01 00:00:00')"
        )
        await conn.execute("INSERT INTO settings (user_id, tz) VALUES (1, 'Asia/Almaty'), (2, 'Asia/Almaty')")
        # Только миграция 8: следующая удаляет данные гостя целиком.
        await conn.executescript(f"BEGIN; {database.MIGRATIONS[7]}; PRAGMA user_version = 8; COMMIT;")
        rows = await conn.execute_fetchall(
            "SELECT user_id, morning_on, morning_time, morning_next_at, morning_msg_id FROM settings ORDER BY user_id"
        )
        assert [tuple(r) for r in rows] == [(1, 1, "06:00", None, None), (2, 0, "06:00", None, None)]
    finally:
        await conn.close()


async def test_migration_from_single_user_base_turns_on_morning_list():
    # База из времён, когда бот отвечал только владельцу: списка доступа ещё нет.
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.executescript(f"BEGIN; {database.MIGRATIONS[0]}; PRAGMA user_version = 1; COMMIT;")
        await conn.execute("INSERT INTO settings (user_id, tz) VALUES (1, 'Asia/Almaty')")
        await database.migrate(conn)
        [row] = await conn.execute_fetchall("SELECT morning_on FROM settings")
        assert row[0] == 1
    finally:
        await conn.close()


async def test_scheduler_sends_nothing_from_other_users_data(db, user, fake_bot):
    # Чужие данные в базе (например, после смены OWNER_ID): бот им не пишет и ничего не копит.
    guest = 2002
    await settings.ensure(db, guest, TZ, NOW)
    await settings.update(db, guest, morning_msg_id=555, morning_msg_day="2026-10-17")
    await habits.create(db, guest, "💧 Вода", tu.ALL_DAYS, None, TZ, NOW)
    task_id = await tasks.create(
        db, guest, {"title": "Его дело", "due_date": "2026-10-17", "due_time": "10:00", "offsets": [0]}, TZ, NOW
    )
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=22), OWNER)  # 06:00
    assert all(chat_id != guest for chat_id, _, _ in fake_bot.sent) and fake_bot.pinned == []
    assert all(chat_id != guest for chat_id, _ in fake_bot.unpinned)
    assert await tasks.pending_reminders(db, task_id) == 0


async def test_scheduler_without_owner_waits_for_it(db, user, fake_bot):
    task_id = await _task(db)
    await habits.create(db, OWNER, "💧 Вода", tu.ALL_DAYS, None, TZ, NOW)
    await settings.update(db, OWNER, morning_msg_id=555, morning_msg_day="2026-10-17")
    await scheduler.tick(fake_bot, db, NOW + timedelta(days=1), None)
    assert fake_bot.sent == [] and fake_bot.documents == [] and fake_bot.unpinned == []
    assert await tasks.pending_reminders(db, task_id) == 1  # ничего не помечено отправленным

    await scheduler.tick(fake_bot, db, NOW + timedelta(days=1), OWNER)  # OWNER_ID вписали
    assert any("Купить хлеб" in text and "опозданием" in text for _, text, _ in fake_bot.sent)


async def test_migration_drops_access_tables(db):
    rows = await db.execute_fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    assert {"allowed_users", "access_requests"}.isdisjoint(r[0] for r in rows)


USER_TABLES = ("settings", "tasks", "categories", "transactions", "habits", "countdowns", "task_log", "action_log")


async def _base_before_owner_only(owners: list[tuple[int, int]]) -> aiosqlite.Connection:
    """База на миграции 9 с данными пользователей 1 и 2 и списком доступа owners: (user_id, is_owner)."""
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    for number, script in enumerate(database.MIGRATIONS[:9], start=1):
        await conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
    for user_id, is_owner in owners:
        await conn.execute(
            "INSERT INTO allowed_users (user_id, is_owner, added_at) VALUES (?, ?, '2026-01-01 00:00:00')",
            (user_id, is_owner),
        )
    now = "2026-01-01 00:00:00"
    for uid in (1, 2):
        await conn.execute("INSERT INTO settings (user_id, tz) VALUES (?, 'Asia/Almaty')", (uid,))
        await conn.execute(
            "INSERT INTO tasks (id, user_id, title, created_at) VALUES (?, ?, 'Дело', ?)", (uid * 10, uid, now)
        )
        await conn.execute("INSERT INTO task_reminders (task_id, fire_at) VALUES (?, ?)", (uid * 10, now))
        await conn.execute(
            "INSERT INTO categories (id, user_id, kind, name) VALUES (?, ?, 'expense', 'Еда')", (uid * 10, uid)
        )
        await conn.execute(
            "INSERT INTO transactions (user_id, kind, amount, category_id, op_date, created_at)"
            " VALUES (?, 'expense', 100, ?, '2026-01-01', ?)",
            (uid, uid * 10, now),
        )
        await conn.execute(
            "INSERT INTO habits (id, user_id, title, created_at) VALUES (?, ?, 'Вода', ?)", (uid * 10, uid, now)
        )
        await conn.execute("INSERT INTO habit_marks (habit_id, date) VALUES (?, '2026-01-01')", (uid * 10,))
        await conn.execute(
            "INSERT INTO countdowns (user_id, title, target_date, send_time)"
            " VALUES (?, 'Отпуск', '2027-01-01', '09:00')",
            (uid,),
        )
        await conn.execute("INSERT INTO task_log (user_id, title, done_at) VALUES (?, 'Дело', ?)", (uid, now))
        await conn.execute("INSERT INTO action_log (user_id, at, text) VALUES (?, ?, 'Создано')", (uid, now))
    return conn


async def _owners_by_table(conn: aiosqlite.Connection) -> dict[str, set[int]]:
    result = {t: {r[0] for r in await conn.execute_fetchall(f"SELECT user_id FROM {t}")} for t in USER_TABLES}
    result["task_reminders"] = {
        r[0]
        for r in await conn.execute_fetchall("SELECT t.user_id FROM task_reminders r JOIN tasks t ON t.id = r.task_id")
    }
    result["habit_marks"] = {
        r[0]
        for r in await conn.execute_fetchall("SELECT h.user_id FROM habit_marks m JOIN habits h ON h.id = m.habit_id")
    }
    return result


async def test_migration_deletes_former_guests_data():
    conn = await _base_before_owner_only([(1, 1), (2, 0)])
    try:
        assert await database.scalar(conn, "SELECT count(*) FROM task_reminders") == 2
        await database.migrate(conn)
        assert all(users == {1} for users in (await _owners_by_table(conn)).values())
        assert await database.scalar(conn, "SELECT count(*) FROM task_reminders") == 1
        assert await database.scalar(conn, "SELECT count(*) FROM habit_marks") == 1
    finally:
        await conn.close()


async def test_migration_deletes_data_of_revoked_guests():
    # Гостю закрыли доступ (/deny): его нет в списке, а данные остались — их тоже удаляем.
    conn = await _base_before_owner_only([(1, 1)])
    try:
        await database.migrate(conn)
        assert all(users == {1} for users in (await _owners_by_table(conn)).values())
    finally:
        await conn.close()


async def test_migration_keeps_owner_data_without_settings_row():
    conn = await _base_before_owner_only([(1, 1), (2, 0)])
    try:
        await conn.execute("DELETE FROM settings WHERE user_id = 1")
        await database.migrate(conn)
        owners = await _owners_by_table(conn)
        assert owners.pop("settings") == set() and all(users == {1} for users in owners.values())
    finally:
        await conn.close()


async def test_migration_keeps_everything_when_only_guests_listed():
    conn = await _base_before_owner_only([(2, 0)])
    try:
        await database.migrate(conn)
        assert all(users == {1, 2} for users in (await _owners_by_table(conn)).values())
    finally:
        await conn.close()


async def test_migration_keeps_everything_when_owner_is_unknown():
    # База из времён, когда бот отвечал только владельцу: список доступа пуст, все данные — его.
    conn = await _base_before_owner_only([])
    try:
        await database.migrate(conn)
        assert all(users == {1, 2} for users in (await _owners_by_table(conn)).values())
    finally:
        await conn.close()


async def test_migration_guesses_habit_part_by_reminder_time():
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    try:
        for number, script in enumerate(database.MIGRATIONS[:8], start=1):
            await conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
        for title, at in [("a", "07:30"), ("b", "12:00"), ("c", "17:59"), ("d", "18:00"), ("e", None)]:
            await conn.execute(
                "INSERT INTO habits (user_id, title, remind_time, created_at) VALUES (1, ?, ?, '2026-01-01 00:00:00')",
                (title, at),
            )
        await database.migrate(conn)
        rows = await conn.execute_fetchall("SELECT title, part FROM habits ORDER BY id")
        assert [tuple(r) for r in rows] == [
            ("a", "morning"),
            ("b", "day"),
            ("c", "day"),
            ("d", "evening"),
            ("e", "morning"),
        ]
    finally:
        await conn.close()


async def test_owner_hears_about_broken_scheduler_rarely(db, user, fake_bot, monkeypatch):
    async def broken_tick(*args):
        raise RuntimeError("база недоступна")

    watchdog = scheduler.Watchdog()
    monkeypatch.setattr(scheduler, "tick", broken_tick)
    await scheduler.guarded_tick(fake_bot, db, NOW, OWNER, watchdog)
    await scheduler.guarded_tick(fake_bot, db, NOW + timedelta(minutes=4), OWNER, watchdog)
    assert fake_bot.sent == []  # пара упавших тиков — не повод писать

    await scheduler.guarded_tick(fake_bot, db, NOW + timedelta(minutes=5), OWNER, watchdog)
    await scheduler.guarded_tick(fake_bot, db, NOW + timedelta(minutes=30), OWNER, watchdog)
    [(chat_id, text, _)] = fake_bot.sent
    assert chat_id == OWNER and "падает уже 5 минут" in text and "RuntimeError: база недоступна" in text

    monkeypatch.undo()
    await scheduler.guarded_tick(fake_bot, db, NOW + timedelta(minutes=31), OWNER, watchdog)
    assert fake_bot.sent[-1][1] == "✅ Планировщик снова работает."

    # Новая серия падений в те же сутки — молчим; через сутки после прошлого сообщения — пишем снова.
    monkeypatch.setattr(scheduler, "tick", broken_tick)
    await scheduler.guarded_tick(fake_bot, db, NOW + timedelta(hours=2), OWNER, watchdog)
    await scheduler.guarded_tick(fake_bot, db, NOW + timedelta(hours=3), OWNER, watchdog)
    assert len(fake_bot.sent) == 2
    await scheduler.guarded_tick(fake_bot, db, NOW + timedelta(days=1, minutes=5), OWNER, watchdog)
    assert len(fake_bot.sent) == 3 and "падает" in fake_bot.sent[-1][1]


async def test_short_failure_does_not_report_recovery(db, user, fake_bot, monkeypatch):
    async def broken_tick(*args):
        raise RuntimeError("сбой")

    watchdog = scheduler.Watchdog()
    monkeypatch.setattr(scheduler, "tick", broken_tick)
    await scheduler.guarded_tick(fake_bot, db, NOW, OWNER, watchdog)
    monkeypatch.undo()
    await scheduler.guarded_tick(fake_bot, db, NOW + timedelta(minutes=1), OWNER, watchdog)
    assert fake_bot.sent == []


async def test_migration_moves_start_balance_into_card_account():
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    try:
        for number, script in enumerate(database.MIGRATIONS[:11], start=1):
            await conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
        await conn.execute("INSERT INTO settings (user_id, tz, start_balance) VALUES (1, 'Asia/Almaty', 500000)")
        await conn.execute("INSERT INTO categories (id, user_id, kind, name) VALUES (5, 1, 'expense', 'Еда')")
        await conn.execute(
            "INSERT INTO transactions (user_id, kind, amount, category_id, op_date, created_at)"
            " VALUES (1, 'expense', 100, 5, '2026-01-01', '2026-01-01 00:00:00')"
        )
        await database.migrate(conn)
        accounts = await conn.execute_fetchall("SELECT id, name, start_balance FROM accounts ORDER BY id")
        assert [(r["name"], r["start_balance"]) for r in accounts] == [("💳 Карта", 500000), ("💵 Наличные", 0)]
        card = accounts[0]["id"]
        assert await database.scalar(conn, "SELECT default_account_id FROM settings") == card
        assert await database.scalar(conn, "SELECT account_id FROM transactions") == card
    finally:
        await conn.close()
