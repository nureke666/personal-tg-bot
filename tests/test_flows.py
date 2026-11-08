"""Сквозные сценарии: апдейты идут через настоящий Dispatcher, Bot API подменён записью вызовов."""

import asyncio
from datetime import UTC, date, datetime, timedelta

from aiogram.methods import EditMessageText, PinChatMessage, UnpinChatMessage
from aiogram.types import Chat, Message, Update, User

from bot.callbacks import (
    AccCb,
    CalCb,
    CatCb,
    CountdownCb,
    DateQuickCb,
    DaysCb,
    FinCb,
    HabitCb,
    NavCb,
    OpCb,
    PartCb,
    PayCb,
    RemindCb,
    RepeatCb,
    SetCb,
    SnoozeCb,
    SumCb,
    TaskCb,
    TaskFieldCb,
    TaskListCb,
    TimeCb,
)
from bot.keyboards.common import MENU_FINANCE, MENU_SUMMARY
from bot.repo import countdowns, finance, habits, payments, settings, tasks
from bot.services import timeutil as tu
from tests.conftest import OWNER, TZ


def today():
    return tu.local_today(TZ)


async def _all_tasks(db):
    return await db.execute_fetchall("SELECT * FROM tasks")


async def test_without_owner_bot_tells_user_id(db, make_harness):
    harness = make_harness(owner_id=None)
    await harness.send("/start", user_id=777)
    assert "OWNER_ID=777" in harness.text
    assert await settings.get(db, 777) is None


async def test_stranger_is_ignored(db, harness, user):
    await harness.send("/start", user_id=2002)
    await harness.send("1500 такси", user_id=2002)
    await harness.click(TaskListCb(view="menu"), user_id=2002)
    assert harness.session.calls == []
    assert await settings.get(db, 2002) is None


async def test_start_shows_menu(db, harness):
    await harness.send("/start")
    assert "Задачи" in harness.text
    assert await settings.get(db, OWNER) is not None


async def test_create_task_wizard(db, harness):
    await harness.click(TaskListCb(view="new"))
    await harness.send("Купить хлеб")
    await harness.click(NavCb(action="skip"))
    await harness.click(DateQuickCb(v="tomorrow"))
    await harness.click(TimeCb(action="pick", kind="t", h=18, m=0))
    await harness.click(RemindCb(off=60))  # к «в момент» добавляем «за 1 ч»
    await harness.click(RemindCb(off=-2))
    await harness.click(RepeatCb(v="daily"))
    assert "Проверь задачу" in harness.text and "Купить хлеб" in harness.text
    await harness.click(NavCb(action="save"))

    assert "Задача сохранена" in harness.text
    [task] = await _all_tasks(db)
    assert task["title"] == "Купить хлеб" and task["details"] is None
    assert task["due_date"] == (today() + timedelta(days=1)).isoformat()
    assert task["due_time"] == "18:00" and task["repeat"] == "daily"
    assert await tasks.offsets(db, task["id"]) == [0, 60]
    assert await tasks.pending_reminders(db, task["id"]) == 2


async def test_wizard_survives_restart(db, make_harness):
    before = make_harness()
    await before.click(TaskListCb(view="new"))
    await before.send("Купить хлеб")
    await before.close()  # бот перезапустили посреди мастера

    after = make_harness()
    await after.click(NavCb(action="skip"))
    await after.click(DateQuickCb(v="none"))
    await after.click(NavCb(action="save"))
    assert "Задача сохранена" in after.text
    [task] = await _all_tasks(db)
    assert task["title"] == "Купить хлеб"
    assert await db.execute_fetchall("SELECT * FROM fsm") == []  # закончил мастер — строка убрана


async def test_task_without_date_skips_time_and_reminders(db, harness):
    await harness.click(TaskListCb(view="new"))
    await harness.send("Прочитать книгу")
    await harness.send("«Мастер и Маргарита»")
    await harness.click(DateQuickCb(v="none"))
    assert "Проверь задачу" in harness.text
    await harness.click(NavCb(action="save"))
    [task] = await _all_tasks(db)
    assert task["due_date"] is None and task["details"] == "«Мастер и Маргарита»"


async def test_back_and_edit_in_draft(db, harness):
    await harness.click(TaskListCb(view="new"))
    await harness.send("Черновик")
    await harness.click(NavCb(action="back"))  # с подробностей обратно к названию
    assert "Что нужно сделать" in harness.text
    await harness.send("Позвонить маме")
    await harness.click(NavCb(action="skip"))
    await harness.click(DateQuickCb(v="cal"))
    await harness.click(NavCb(action="back"))  # из календаря обратно к быстрым датам
    assert "Когда?" in harness.text
    await harness.click(DateQuickCb(v="today"))
    await harness.click(TimeCb(action="none", kind="t"))
    await harness.click(RemindCb(off=-2))
    await harness.click(RepeatCb(v="none"))
    # На подтверждении меняем название через меню полей.
    await harness.click(TaskFieldCb(f="menu"))
    await harness.click(TaskFieldCb(f="title"))
    await harness.send("Позвонить маме вечером")
    assert "Проверь задачу" in harness.text and "вечером" in harness.text
    await harness.click(NavCb(action="save"))
    [task] = await _all_tasks(db)
    assert task["title"] == "Позвонить маме вечером" and task["due_time"] is None


async def test_menu_button_interrupts_wizard(db, harness):
    await harness.click(TaskListCb(view="new"))
    await harness.send(MENU_FINANCE)
    assert "Финансы" in harness.text
    assert await _all_tasks(db) == []
    await harness.send("Просто текст")
    assert "Сохранить как задачу" in harness.text


async def test_edit_saved_task_and_complete(db, harness, user):
    task_id = await tasks.create(
        db, OWNER, {"title": "Старое", "due_date": today().isoformat(), "offsets": [0]}, TZ, tu.utcnow()
    )
    await harness.click(TaskCb(act="edit", id=task_id))
    await harness.click(TaskFieldCb(f="title", id=task_id))
    await harness.send("Новое")
    assert "Сохранено" in harness.text
    assert (await tasks.get(db, OWNER, task_id))["title"] == "Новое"

    await harness.click(TaskCb(act="done", id=task_id))
    assert (await tasks.get(db, OWNER, task_id))["status"] == tasks.DONE
    await harness.click(TaskCb(act="del", id=task_id))
    await harness.click(TaskCb(act="delok", id=task_id))
    assert await _all_tasks(db) == []


async def test_task_lists(db, harness, user):
    await tasks.create(db, OWNER, {"title": "Сегодня", "due_date": today().isoformat()}, TZ, tu.utcnow())
    await tasks.create(db, OWNER, {"title": "Когда-нибудь"}, TZ, tu.utcnow())
    await harness.click(TaskListCb(view="menu"))
    assert harness.button("Сегодня (1)") and harness.button("Без даты (1)")
    await harness.click(TaskListCb(view="nodate"))
    assert harness.button("Когда-нибудь")


async def test_snooze_and_stale_occurrence(db, harness, user):
    day = today().isoformat()
    task_id = await tasks.create(
        db, OWNER, {"title": "Таблетки", "due_date": day, "due_time": "23:59", "repeat": "daily"}, TZ, tu.utcnow()
    )
    await harness.click(SnoozeCb(act="m10", id=task_id, d=day), message_text="🔔 Таблетки")
    assert "Напомню" in harness.text
    assert await tasks.pending_reminders(db, task_id) == 1

    await harness.click(SnoozeCb(act="done", id=task_id, d="2000-01-01"), message_text="🔔 Таблетки")
    assert "Этот раз уже прошёл" in harness.text
    assert (await tasks.get(db, OWNER, task_id))["due_date"] == day

    await harness.click(SnoozeCb(act="done", id=task_id, d=day), message_text="🔔 Таблетки")
    assert "Следующий раз" in harness.text
    assert (await tasks.get(db, OWNER, task_id))["due_date"] == (today() + timedelta(days=1)).isoformat()


async def test_expense_with_limit_warning(db, harness, user):
    await harness.click(FinCb(act="limit", id=0))
    await harness.send("1000")
    assert (await settings.get(db, OWNER))["month_limit"] == 100_000

    food = (await finance.categories(db, OWNER, finance.EXPENSE))[0]
    await harness.click(FinCb(act="add", v="expense"))
    await harness.send("сто")
    assert "Не понял сумму" in harness.text
    await harness.send("900")
    await harness.click(CatCb(act="pick", id=food["id"]))
    await harness.send("продукты")
    assert "Записал" in harness.text and "⚠️" in harness.text and "90%" in harness.text

    await harness.click(FinCb(act="menu"))
    assert "Баланс: <b>-900 ₸</b>" in harness.text


async def test_income_and_report_and_export(db, harness, user):
    salary = (await finance.categories(db, OWNER, finance.INCOME))[0]
    await harness.click(FinCb(act="add", v="income"))
    await harness.send("500 000")
    await harness.click(CatCb(act="pick", id=salary["id"]))
    await harness.click(NavCb(action="skip"))
    await harness.click(FinCb(act="report", v="month"))
    assert "Доходы: 500 000 ₸" in harness.text

    await harness.click(FinCb(act="xlsx", v="month"))
    await harness.click(FinCb(act="csv", v="all"))
    assert [d.document.filename.split(".")[-1] for d in harness.documents] == ["xlsx", "csv"]
    await harness.click(FinCb(act="xlsx", v="prev"))
    assert harness.toasts[-1] == "За этот период операций нет"


async def test_change_operation_date_and_delete(db, harness, user):
    food = (await finance.categories(db, OWNER, finance.EXPENSE))[0]
    op_id = await finance.add_op(db, OWNER, "expense", 10_000, food["id"], None, today(), tu.utcnow())
    await harness.click(OpCb(act="setdate", id=op_id, v="1"))
    assert (await finance.op(db, OWNER, op_id))["op_date"] == (today() - timedelta(days=1)).isoformat()
    await harness.click(OpCb(act="delok", id=op_id))
    assert await finance.op(db, OWNER, op_id) is None


async def test_categories_add_and_archive(db, harness, user):
    await harness.click(CatCb(act="add", kind="expense"))
    await harness.send("☕ Кофе")
    names = [c["name"] for c in await finance.categories(db, OWNER, "expense")]
    assert "☕ Кофе" in names
    coffee = next(c for c in await finance.categories(db, OWNER, "expense") if c["name"] == "☕ Кофе")
    await harness.click(CatCb(act="archive", id=coffee["id"]))
    assert "☕ Кофе" not in [c["name"] for c in await finance.categories(db, OWNER, "expense")]


async def test_category_name_must_be_unique(db, harness, user):
    food = (await finance.categories(db, OWNER, "expense"))[0]
    await harness.click(CatCb(act="add", kind="expense"))
    await harness.send(food["name"])
    assert "уже есть" in harness.text
    await harness.send("☕ Кофе")  # мастер не сбросился, можно ввести другое название
    names = [c["name"] for c in await finance.categories(db, OWNER, "expense")]
    assert names.count(food["name"]) == 1 and "☕ Кофе" in names


async def test_empty_category_is_deleted_after_confirm(db, harness, user):
    coffee = await finance.add_category(db, OWNER, "expense", "☕ Кофе")
    await harness.click(CatCb(act="open", id=coffee))
    await harness.click(harness.button("Удалить"))
    assert "Удалить категорию ☕ Кофе?" in harness.text
    assert await finance.category(db, OWNER, coffee) is not None
    await harness.click(harness.button("Да, удалить"))
    assert await finance.category(db, OWNER, coffee) is None
    assert "Категория удалена" in harness.text and "☕ Кофе" not in harness.labels


async def test_category_with_records_is_deleted_after_moving_them(db, harness, user):
    food, transport = (await finance.categories(db, OWNER, "expense"))[:2]
    op_id = await finance.add_op(db, OWNER, "expense", 10_000, food["id"], None, today(), tu.utcnow())
    pid = await payments.create(db, OWNER, "🍕 Пицца", 50_000, food["id"], None, "monthly", today(), TZ)
    await harness.click(CatCb(act="delete", id=food["id"]))
    assert "1 операция и 1 регулярный платёж" in harness.text
    assert food["name"] not in harness.labels  # переносить в саму себя нельзя
    assert not any("Подарок" in label for label in harness.labels)  # и в категорию доходов
    await harness.click(harness.button(transport["name"]))
    assert await finance.category(db, OWNER, food["id"]) is None
    assert (await finance.op(db, OWNER, op_id))["category_id"] == transport["id"]
    assert (await payments.get(db, OWNER, pid))["category_id"] == transport["id"]
    assert "перенесены" in harness.text


async def test_last_category_cannot_be_deleted(db, harness, user):
    *others, last = await finance.categories(db, OWNER, "income")
    for category in others:
        await harness.click(CatCb(act="delok", id=category["id"]))
    await harness.click(CatCb(act="delete", id=last["id"]))
    assert harness.toasts[-1] == "Должна остаться хотя бы одна категория"
    assert [c["id"] for c in await finance.categories(db, OWNER, "income")] == [last["id"]]


async def test_stale_move_button_changes_nothing(db, harness, user):
    food, transport = (await finance.categories(db, OWNER, "expense"))[:2]
    await harness.click(CatCb(act="into", id=transport["id"]))
    assert "устарела" in harness.toasts[-1]
    assert await finance.category(db, OWNER, food["id"]) is not None


async def test_same_name_sent_twice_at_once_makes_one_category(db, harness, user):
    await harness.click(CatCb(act="add", kind="expense"))
    await asyncio.gather(harness.send("☕ Кофе"), harness.send("☕ Кофе"))
    names = [c["name"] for c in await finance.categories(db, OWNER, "expense")]
    assert names.count("☕ Кофе") == 1
    assert all("Категории расходов" in screen.text for screen in harness.screens()[-2:])


async def test_category_rename_checks_other_names_only(db, harness, user):
    food, transport = (await finance.categories(db, OWNER, "expense"))[:2]
    await harness.click(CatCb(act="rename", id=food["id"]))
    await harness.send(transport["name"])
    assert "уже есть" in harness.text
    await harness.send(food["name"])  # своё же название — не дубль
    assert (await finance.category(db, OWNER, food["id"]))["name"] == food["name"]
    assert "уже есть" not in harness.text


async def test_move_button_from_another_delete_screen_is_stale(db, harness, user):
    food, transport, housing = (await finance.categories(db, OWNER, "expense"))[:3]
    for category in (food, housing):
        await finance.add_op(db, OWNER, "expense", 10_000, category["id"], None, today(), tu.utcnow())
    await harness.click(CatCb(act="delete", id=food["id"]), message_id=500)
    await harness.click(CatCb(act="delete", id=housing["id"]), message_id=600)
    await harness.click(CatCb(act="into", id=transport["id"]), message_id=500)
    assert "устарела" in harness.toasts[-1]
    assert await finance.category(db, OWNER, food["id"]) and await finance.category(db, OWNER, housing["id"])
    await harness.click(CatCb(act="into", id=transport["id"]), message_id=600)
    assert await finance.category(db, OWNER, housing["id"]) is None
    assert await finance.category(db, OWNER, food["id"]) is not None
    assert f"Категория {housing['name']} удалена" in harness.text


async def test_hidden_category_with_records_is_deleted_after_moving_them(db, harness, user):
    food, transport = (await finance.categories(db, OWNER, "expense"))[:2]
    op_id = await finance.add_op(db, OWNER, "expense", 10_000, food["id"], None, today(), tu.utcnow())
    await finance.set_category_archived(db, food["id"], True)
    await harness.click(CatCb(act="delete", id=food["id"]))
    await harness.click(harness.button(transport["name"]))
    assert await finance.category(db, OWNER, food["id"]) is None
    assert (await finance.op(db, OWNER, op_id))["category_id"] == transport["id"]


async def test_records_are_not_moved_into_hidden_or_income_category(db, harness, user):
    food, transport = (await finance.categories(db, OWNER, "expense"))[:2]
    salary = (await finance.categories(db, OWNER, "income"))[0]
    op_id = await finance.add_op(db, OWNER, "expense", 10_000, food["id"], None, today(), tu.utcnow())
    await finance.set_category_archived(db, transport["id"], True)
    for target in (salary, transport):
        await harness.click(CatCb(act="delete", id=food["id"]))
        await harness.click(CatCb(act="into", id=target["id"]))
        assert harness.text == "Категория не найдена."
    assert (await finance.op(db, OWNER, op_id))["category_id"] == food["id"]


async def test_double_delete_confirm_says_already_deleted(db, harness, user):
    coffee = await finance.add_category(db, OWNER, "expense", "☕ Кофе")
    await asyncio.gather(*(harness.click(CatCb(act="delok", id=coffee)) for _ in range(2)))
    assert await finance.category(db, OWNER, coffee) is None
    texts = [screen.text for screen in harness.screens()]
    assert any("уже удалена" in text for text in texts) and not any("перенести" in text for text in texts)


async def test_countdown_wizard_and_send_now(db, harness):
    target = today() + timedelta(days=10)
    await harness.click(CountdownCb(act="new"))
    await harness.send("🏖 Отпуск")
    await harness.click(CalCb(action="pick", mode="f", y=target.year, m=target.month, d=target.day))
    assert "каждый год" in harness.text
    await harness.click(CountdownCb(act="rep", f="once"))
    await harness.click(TimeCb(action="pick", kind="p", h=9, m=0))
    await harness.send("До отпуска {дни}!")
    assert "До отпуска 10 дней!" in harness.text and "каждый день в 09:00" in harness.text
    await harness.click(CountdownCb(act="save"))
    assert "Отсчёт создан" in harness.text

    [row] = await countdowns.list_all(db, OWNER)
    assert row["target_date"] == target.isoformat() and row["send_time"] == "09:00"
    await harness.click(CountdownCb(act="now", id=row["id"]))
    assert harness.text == "⏳ <b>🏖 Отпуск</b>\n\nДо отпуска 10 дней!"

    await harness.click(CountdownCb(act="edit", id=row["id"], f="send_time"))
    await harness.click(TimeCb(action="pick", kind="p", h=20, m=30))
    assert (await countdowns.get(db, OWNER, row["id"]))["send_time"] == "20:30"


async def test_yearly_countdown_wizard_and_revive(db, harness, user):
    target = today() + timedelta(days=40)
    await harness.click(CountdownCb(act="new"))
    await harness.send("🎂 ДР мамы")
    await harness.click(CalCb(action="pick", mode="f", y=target.year, m=target.month, d=target.day))
    await harness.click(CountdownCb(act="rep", f="yearly7"))
    await harness.click(TimeCb(action="pick", kind="p", h=9, m=0))
    await harness.click(NavCb(action="skip"))
    assert "в 09:00 за 7 дней · 🔁 каждый год" in harness.text
    await harness.click(CountdownCb(act="save"))
    [row] = await countdowns.list_all(db, OWNER)
    assert row["yearly"] == 1 and row["lead_days"] == 7

    # Разовый отсчёт, который уже завершился, делаем ежегодным — он оживает на следующий год.
    past = today() - timedelta(days=3)
    old_id = await countdowns.create(db, OWNER, "Годовщина", None, past.isoformat(), "10:00", TZ, tu.utcnow())
    await countdowns.set_status(db, await countdowns.get(db, OWNER, old_id), countdowns.DONE, TZ, tu.utcnow())
    await harness.click(CountdownCb(act="edit", id=old_id, f="repeat"))
    await harness.click(CountdownCb(act="rep", f="yearly"))
    revived = await countdowns.get(db, OWNER, old_id)
    assert revived["status"] == countdowns.ACTIVE and revived["target_date"] == tu.add_years(past, 1).isoformat()


async def test_habit_wizard_toggle_and_stats(db, harness):
    await harness.click(HabitCb(act="new"))
    assert "Какую привычку добавим" in harness.text
    await harness.click(HabitCb(act="new", f="check"))
    await harness.send("💧 Вода")
    await harness.click(DaysCb(v="custom"))
    off = (today().weekday() + 1) % 7  # снимаем завтрашний день недели, чтобы сегодня привычка была по плану
    await harness.click(DaysCb(v="custom", bit=off))
    await harness.click(DaysCb(v="done"))
    assert "Когда делаешь" in harness.text
    await harness.click(PartCb(v="evening"))
    await harness.click(TimeCb(action="pick", kind="h", h=10, m=0))
    assert "добавлена" in harness.text

    [habit] = await habits.list_active(db, OWNER)
    assert habit["days_mask"] == tu.ALL_DAYS ^ (1 << off) and habit["remind_time"] == "10:00"
    assert habit["part"] == "evening"
    assert "<b>🌙 Вечером</b>\n1. ⬜ 💧 Вода · ⏰ 10:00" in harness.text
    await harness.click(harness.button("1"))
    assert harness.toasts[-1] == "✅ Отмечено"
    assert "✅ 1" in harness.labels and "1. ✅ 💧 Вода  🔥1 · ⏰ 10:00" in harness.text
    # Значок части дня в начале ряда ничего не делает.
    screens = len(harness.screens())
    await harness.click(harness.button("🌙"))
    assert len(harness.screens()) == screens
    await harness.click(HabitCb(act="stats"))
    assert "Серия" in harness.text


async def test_habit_part_change_moves_it_between_sections(db, harness, user):
    water = await habits.create(db, OWNER, "💧 Вода", tu.ALL_DAYS, None, TZ, tu.utcnow())
    await habits.create(db, OWNER, "📚 Чтение", tu.ALL_DAYS, None, TZ, tu.utcnow())
    await harness.click(HabitCb(act="open", id=water))
    assert "🌅 Утром" in harness.text
    await harness.click(harness.button("Часть дня"))
    assert "✅ 🌅 Утром" in harness.labels
    await harness.click(PartCb(v="evening"))
    assert "Сохранено" in harness.text and "🌙 Вечером" in harness.text
    assert (await habits.get(db, OWNER, water))["part"] == "evening"

    await harness.click(HabitCb(act="day"))
    # Номера идут сверху вниз, поэтому утреннее «Чтение» стало первым.
    assert "<b>🌅 Утром</b>\n1. ⬜ 📚 Чтение" in harness.text and "<b>🌙 Вечером</b>\n2. ⬜ 💧 Вода" in harness.text
    assert [[b.text for b in row] for row in harness.markup.inline_keyboard][:2] == [["🌅", "1"], ["🌙", "2"]]


async def test_settings_timezone_and_schedule(db, harness, user):
    task_id = await tasks.create(
        db, OWNER, {"title": "Встреча", "due_date": "2099-01-01", "due_time": "10:00", "offsets": [0]}, TZ, tu.utcnow()
    )
    await harness.click(SetCb(act="tzset", v="Europe/Moscow"))
    assert (await settings.get(db, OWNER))["tz"] == "Europe/Moscow"
    rows = await db.execute_fetchall("SELECT fire_at FROM task_reminders WHERE task_id = ?", (task_id,))
    assert [r[0] for r in rows] == ["2099-01-01 07:00:00"]

    await harness.click(SetCb(act="toggle", v="digest"))
    assert (await settings.get(db, OWNER))["digest_on"] == 0
    await harness.click(SetCb(act="time", v="checkin"))
    await harness.click(TimeCb(action="pick", kind="p", h=22, m=15))
    assert (await settings.get(db, OWNER))["checkin_time"] == "22:15"


async def test_accounts_balances_transfer_and_default(db, harness, user):
    card, cash = await finance.accounts(db, OWNER)
    assert user["default_account_id"] == card["id"]
    await harness.click(AccCb(act="start", id=card["id"]))
    await harness.send("100000")
    assert (await finance.account(db, OWNER, card["id"]))["start_balance"] == 10_000_000

    await harness.click(FinCb(act="add", v="expense"))
    await harness.send("1500")
    await harness.click(harness.button("Еда"))
    await harness.click(NavCb(action="skip"))
    assert "✅ Записал" in harness.text and "💳 Карта" in harness.text  # основной счёт

    await harness.click(AccCb(act="transfer"))
    await harness.click(AccCb(act="tfrom", id=card["id"]))
    await harness.click(AccCb(act="tto", id=cash["id"], v=str(card["id"])))
    await harness.send("20000")
    assert "✅ 🔁 Перевод" in harness.text
    balances = await finance.account_balances(db, OWNER)
    assert balances == {card["id"]: 10_000_000 - 150_000 - 2_000_000, cash["id"]: 2_000_000}
    assert await finance.balance(db, OWNER) == 10_000_000 - 150_000  # перевод общую сумму не меняет
    assert await finance.totals(db, OWNER, None, None) == (0, 150_000)  # и в доходы/расходы не попадает

    await harness.click(AccCb(act="archive", id=card["id"]))
    assert harness.toasts[-1] == "Основной счёт скрыть нельзя"
    await harness.click(AccCb(act="default", id=cash["id"]))
    assert (await settings.get(db, OWNER))["default_account_id"] == cash["id"]
    await harness.send("700 кофе")
    await harness.click(harness.button("Еда"))
    [op] = [o for o in await finance.recent_ops(db, OWNER, 0, 10) if o["comment"] == "кофе"]
    assert op["account"] == "💵 Наличные"

    await harness.click(OpCb(act="setacc", id=op["id"], v=str(card["id"])))
    assert "👛 Счёт изменён" in harness.text and (await finance.op(db, OWNER, op["id"]))["account_id"] == card["id"]

    await harness.click(AccCb(act="transfers"))
    [transfer] = await finance.recent_transfers(db, OWNER)
    await harness.click(AccCb(act="tdel", id=transfer["id"]))
    await harness.click(AccCb(act="tdelok", id=transfer["id"]))
    assert "Перевод удалён" in harness.text and await finance.recent_transfers(db, OWNER) == []
    assert (await finance.account_balances(db, OWNER))[cash["id"]] == 0

    await harness.click(AccCb(act="new"))
    await harness.send("Kaspi <Gold>")
    assert "Kaspi &lt;Gold&gt;" in harness.text  # имя экранируется
    gold = (await finance.accounts(db, OWNER))[-1]
    await harness.click(AccCb(act="transfer"))
    await harness.click(AccCb(act="tfrom", id=card["id"]))
    await harness.click(AccCb(act="tto", id=gold["id"], v=str(card["id"])))
    await harness.send("100")
    assert "Kaspi &lt;Gold&gt;" in harness.text and "<Gold>" not in harness.text

    await harness.click(AccCb(act="new"))
    await harness.send("🏦 Депозит")
    assert "🏦 Депозит" in harness.text and len(await finance.accounts(db, OWNER)) == 4
    await harness.click(FinCb(act="menu"))
    assert "🏦 Депозит" in harness.text and "💵 Наличные" in harness.text


async def test_backup_from_settings(db, harness, user):
    await harness.click(SetCb(act="menu"))
    assert "Бэкап базы: 03:00" in harness.text
    await harness.click(SetCb(act="sched", v="backup"))
    await harness.click(SetCb(act="backupnow"))
    [doc] = harness.documents
    assert doc.document.filename.endswith(".db") and harness.toasts[-1] == "Бэкап отправлен"


async def test_morning_list_pinned_from_settings_and_kept_in_sync(db, harness, user):
    water = await habits.create(db, OWNER, "💧 Вода", tu.ALL_DAYS, None, TZ, tu.utcnow())
    read = await habits.create(db, OWNER, "📚 Чтение", tu.ALL_DAYS, "21:00", TZ, tu.utcnow())
    await harness.click(SetCb(act="sched", v="morning"))
    assert "План на день" in harness.text
    await harness.click(SetCb(act="morningnow"))
    assert harness.toasts[-1] == "План на сегодня закреплён"
    assert harness.text.startswith("🌅") and harness.labels == ["🌅", "1", "2"]  # в закрепе никакой навигации
    pinned = (await settings.get(db, OWNER))["morning_msg_id"]
    [pin] = harness.calls(PinChatMessage)
    assert pin.message_id == pinned and pin.disable_notification

    # Нажатие на самом закрепе перерисовывает только его.
    edits = len(harness.calls(EditMessageText))
    await harness.click(HabitCb(act="toggle", id=water, d=tu.pack_day(today()), f="am"), message_id=pinned)
    assert harness.text.startswith("🌅") and harness.labels == ["🌅", "1", "✅ 2"]  # «Чтение» с временем выше
    assert len(harness.calls(EditMessageText)) == edits + 1

    # «✅ Сделано» из напоминания — закреп догоняет.
    await harness.click(HabitCb(act="mark", id=read, d=tu.pack_day(today())))
    edit = harness.calls(EditMessageText)[-1]
    assert edit.message_id == pinned and "1. ✅ 📚 Чтение" in edit.text and "🎉 Всё выполнено" in edit.text

    await harness.click(SetCb(act="toggle", v="morning"))
    [unpin] = harness.calls(UnpinChatMessage)
    assert unpin.message_id == pinned
    row = await settings.get(db, OWNER)
    assert row["morning_on"] == 0 and row["morning_msg_id"] is None


async def test_morning_list_now_without_habits(db, harness, user):
    await harness.click(SetCb(act="morningnow"))
    assert harness.toasts[-1] == "На сегодня ни привычек, ни задач" and not harness.calls(PinChatMessage)


async def test_pin_shows_tasks_and_follows_their_changes(db, harness, user):
    await habits.create(db, OWNER, "💧 Вода", tu.ALL_DAYS, None, TZ, tu.utcnow())
    await harness.click(SetCb(act="morningnow"))
    pinned = (await settings.get(db, OWNER))["morning_msg_id"]
    assert "📋 Задачи" not in harness.text  # задач нет — и раздела нет

    # Новая задача на сегодня из быстрого ввода появляется в закрепе.
    await harness.send("позвонить маме")
    await harness.click(harness.button("На сегодня"))

    def pin_text():
        return [e for e in harness.calls(EditMessageText) if e.message_id == pinned][-1].text

    assert "1. ⬜ 💧 Вода" in pin_text() and "<b>📋 Задачи</b>\n• весь день — позвонить маме" in pin_text()
    [task] = await tasks.list_view(db, OWNER, "today", today(), "00:00")

    # Выполнил — из закрепа ушла.
    await harness.click(TaskCb(act="done", id=task["id"]))
    assert "позвонить маме" not in pin_text() and "📋 Задачи" not in pin_text()


async def test_pin_service_message_is_ignored(db, harness, user):
    chat, now = Chat(id=OWNER, type="private"), datetime.now(UTC)
    service = Message(
        message_id=2,
        date=now,
        chat=chat,
        from_user=User(id=OWNER, is_bot=False, first_name="Тест"),
        pinned_message=Message(message_id=1, date=now, chat=chat, text="Список"),
    )
    await harness.dp.feed_update(harness.bot, Update(update_id=1, message=service))
    assert harness.screens() == []


async def test_quit_habit_counts_days_and_relapses(db, harness, user):
    since = today() - timedelta(days=10)
    await harness.click(HabitCb(act="new", f="quit"))
    assert "От чего отказываешься" in harness.text
    await harness.send("🍬 Без сладкого")
    await harness.click(CalCb(action="pick", mode="p", y=since.year, m=since.month, d=since.day))
    assert "Отказ добавлен" in harness.text and "Держусь: <b>10 дней</b>" in harness.text
    [habit] = await habits.list_active(db, OWNER)
    assert habit["kind"] == habits.QUIT and habit["since"] == since.isoformat() and habit["remind_time"] is None

    await harness.click(HabitCb(act="day"))
    assert "🍬 Без сладкого · 10 дней" in harness.labels
    await harness.click(HabitCb(act="toggle", id=habit["id"]))  # старая кнопка не ставит срыв
    assert await habits.marks(db, habit["id"]) == set()

    await harness.click(HabitCb(act="relapse", id=habit["id"]))
    await harness.click(harness.button("Это было вчера"))
    assert "Держусь: <b>1 день</b>" in harness.text and "рекорд: 8 дней" in harness.text
    await harness.click(harness.button("Отменить срыв"))
    assert "Держусь: <b>10 дней</b>" in harness.text

    await harness.click(HabitCb(act="stats"))
    assert "Без срывов: 10 дней" in harness.text


async def test_summary_screen_goes_back_in_time(db, harness, user):
    food = (await finance.categories(db, OWNER, finance.EXPENSE))[0]
    year_ago = today().replace(year=today().year - 1, day=1)
    await finance.add_op(db, OWNER, "expense", 1_234_500, food["id"], None, year_ago, tu.utcnow())
    await harness.send(MENU_SUMMARY)
    assert "Итоги: Неделя" in harness.text
    await harness.click(SumCb(p="year"))
    assert f"Итоги: {today().year} год" in harness.text and "ничего не записано" in harness.text
    assert "Позже" not in " ".join(harness.labels)
    await harness.click(harness.button("Раньше"))
    assert f"{today().year - 1} год" in harness.text and "Расходы: 12 345 ₸" in harness.text
    assert harness.button("Позже")


async def test_finance_report_arrows(db, harness, user):
    food = (await finance.categories(db, OWNER, finance.EXPENSE))[0]
    last_month = today().replace(day=1) - timedelta(days=1)
    await finance.add_op(db, OWNER, "expense", 50_000, food["id"], None, last_month, tu.utcnow())
    await harness.click(FinCb(act="report", v="month"))
    assert "операций нет" in harness.text and "▶" not in harness.labels
    await harness.click(harness.button("◀"))
    assert "Расходы: 500 ₸" in harness.text and harness.button("▶")


async def test_habit_calendar_marks_past_days(db, harness, user):
    habit_id = await habits.create(db, OWNER, "🏃 Бег", tu.ALL_DAYS, None, TZ, tu.utcnow() - timedelta(days=5))
    await harness.click(HabitCb(act="open", id=habit_id))
    await harness.click(harness.button("Календарь"))
    assert "🏃 Бег" in harness.text and "❌" in harness.labels
    three_days_ago = today() - timedelta(days=3)
    await harness.click(HabitCb(act="calset", id=habit_id, d=tu.pack_day(three_days_ago)))
    await harness.click(HabitCb(act="calset", id=habit_id, d=tu.pack_day(today() - timedelta(days=30))))  # до старта
    await harness.click(HabitCb(act="calset", id=habit_id, d=tu.pack_day(today() + timedelta(days=1))))  # будущее
    assert await habits.marks(db, habit_id) == {three_days_ago}


async def _ops(db):
    return await db.execute_fetchall(
        "SELECT o.kind, o.amount, o.comment, c.name FROM transactions o JOIN categories c ON c.id = o.category_id"
        " ORDER BY o.id"
    )


async def test_quick_expense_asks_category_once(db, harness, user):
    transport = next(c for c in await finance.categories(db, OWNER, "expense") if "Транспорт" in c["name"])
    await harness.send("1500 такси")
    assert "🗂 <b>Категория?</b>" in harness.text and "такси" in harness.text
    await harness.click(CatCb(act="pick", id=transport["id"]))
    assert "Записал" in harness.text

    await harness.send("такси 700")  # второй раз — сразу, без вопросов
    assert "Записал" in harness.text and "Транспорт" in harness.text
    assert [tuple(r) for r in await _ops(db)] == [
        ("expense", 150_000, "такси", transport["name"]),
        ("expense", 70_000, "такси", transport["name"]),
    ]


async def test_quick_income_and_kind_switch(db, harness, user):
    await harness.send("+250000 зарплата")
    assert "Записал" in harness.text and "Доход" in harness.text

    await harness.send("50000 возврат долга")  # непонятно: по умолчанию расход, переключаем на доход
    await harness.click(harness.button("Это доход"))
    gift = next(c for c in await finance.categories(db, OWNER, "income") if "Подарок" in c["name"])
    assert harness.button("Подарок")
    await harness.click(CatCb(act="pick", id=gift["id"]))
    ops = await _ops(db)
    assert [(r["kind"], r["comment"]) for r in ops] == [("income", "зарплата"), ("income", "возврат долга")]


async def test_quick_text_becomes_task(db, harness, user):
    await harness.send("Купить хлеб")
    stale = harness.button("На сегодня")
    await harness.send("Позвонить маме")
    await harness.click(stale)  # кнопка под прошлым текстом не создаёт задачу из нового
    assert "устарела" in harness.toasts[-1] and await _all_tasks(db) == []

    await harness.click(harness.button("На сегодня"))
    assert "Задача сохранена" in harness.text
    [task] = await _all_tasks(db)
    assert task["title"] == "Позвонить маме" and task["due_date"] == today().isoformat()

    await harness.send("Записаться к врачу")
    await harness.click(harness.button("Дата и время"))
    assert "Когда?" in harness.text
    await harness.click(DateQuickCb(v="none"))
    await harness.click(NavCb(action="save"))
    assert [t["title"] for t in await _all_tasks(db)] == ["Позвонить маме", "Записаться к врачу"]


async def test_quick_amount_text_can_be_a_task(db, harness, user):
    await harness.send("позвонить 15")  # похоже на сумму, но это задача
    await harness.click(harness.button("Это задача"))
    await harness.click(harness.button("Без даты"))
    [task] = await _all_tasks(db)
    assert task["title"] == "позвонить 15" and task["due_date"] is None
    assert await _ops(db) == []


async def test_quick_phrase_with_date_becomes_task_at_once(db, harness, user):
    await harness.send("завтра в 10 позвонить маме")
    assert "Задача сохранена" in harness.text
    [task] = await _all_tasks(db)
    assert task["title"] == "Позвонить маме" and task["due_date"] == (today() + timedelta(days=1)).isoformat()
    assert task["due_time"] == "10:00" and task["repeat"] == "none"
    assert await tasks.pending_reminders(db, task["id"]) == 1

    await harness.send("каждый понедельник спортзал в 19")
    gym = (await _all_tasks(db))[-1]
    assert gym["title"] == "Спортзал" and gym["repeat"] == "weekly" and gym["due_time"] == "19:00"
    assert date.fromisoformat(gym["due_date"]).weekday() == 0

    await harness.send("1500 такси")  # сумма без даты — по-прежнему расход
    assert "Категория?" in harness.text and len(await _all_tasks(db)) == 2


async def test_change_operation_category(db, harness, user):
    food, transport = (await finance.categories(db, OWNER, "expense"))[:2]
    op_id = await finance.add_op(db, OWNER, "expense", 10_000, food["id"], "такси", today(), tu.utcnow())
    await harness.click(OpCb(act="cat", id=op_id))
    await harness.click(OpCb(act="setcat", id=op_id, v=str(transport["id"])))
    assert "Категория изменена" in harness.text
    assert (await finance.op(db, OWNER, op_id))["category_id"] == transport["id"]


async def test_stale_button_gets_a_hint(db, harness):
    await harness.click(NavCb(action="save"))
    assert "устарела" in harness.toasts[-1]


async def test_regular_payment_wizard_paid_and_skip(db, harness, user):
    due = today() + timedelta(days=3)
    await harness.click(PayCb(act="new"))
    await harness.send("🏠 Аренда")
    await harness.send("150000")
    await harness.click(harness.button("Жильё"))
    await harness.click(PayCb(act="period", v="monthly"))
    await harness.click(CalCb(action="pick", mode="f", y=due.year, m=due.month, d=due.day))
    assert "✅ Платёж добавлен" in harness.text and "🏠 Аренда" in harness.text
    [payment] = await payments.list_all(db, OWNER)
    assert payment["amount"] == 15_000_000 and payment["due_date"] == due.isoformat()
    assert payment["account"] == "💳 Карта" and payment["anchor_day"] == due.day

    await harness.click(PayCb(act="paid", id=payment["id"], d=tu.pack_day(due)))
    assert "✅ Записал. Следующий платёж" in harness.text
    [op] = await finance.recent_ops(db, OWNER, 0, 10)
    assert (op["amount"], op["comment"], op["category"], op["account"]) == (
        15_000_000,
        "🏠 Аренда",
        "🏠 Жильё",
        "💳 Карта",
    )
    after = (await payments.get(db, OWNER, payment["id"]))["due_date"]
    assert after == payments.next_due(due, "monthly", due.day).isoformat()

    # Кнопка со старого напоминания не записывает расход второй раз.
    await harness.click(PayCb(act="paid", id=payment["id"], d=tu.pack_day(due)))
    assert harness.toasts[-1] == "Этот платёж уже отмечен" and await finance.count_ops(db, OWNER) == 1

    await harness.click(PayCb(act="skip", id=payment["id"], d=tu.pack_day(date.fromisoformat(after))))
    assert "пропущен" in harness.text and await finance.count_ops(db, OWNER) == 1


async def test_double_tap_on_paid_records_once(db, harness, user):
    import asyncio

    housing = next(c for c in await finance.categories(db, OWNER, "expense") if "Жильё" in c["name"])
    pid = await payments.create(db, OWNER, "🏠 Аренда", 15_000_000, housing["id"], None, "monthly", today(), TZ)
    button = PayCb(act="paid", id=pid, d=tu.pack_day(today()))
    await asyncio.gather(harness.click(button), harness.click(button))  # нажал дважды, пока не было ответа
    assert await finance.count_ops(db, OWNER) == 1
    assert "Этот платёж уже отмечен" in harness.toasts
