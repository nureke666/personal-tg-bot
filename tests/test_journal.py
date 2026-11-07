"""Журнал действий: что записывается, кнопки 10/25/50, у каждого свой журнал."""

from datetime import timedelta

from bot.callbacks import CatCb, CountdownCb, HabitCb, JournalCb, NavCb, SetCb, TaskCb, TaskListCb
from bot.keyboards.common import MENU_JOURNAL, main_menu
from bot.repo import access, countdowns, finance, habits, journal, tasks
from bot.services import timeutil as tu
from bot.views.journal import journal_view
from tests.conftest import NOW, OWNER, TZ

FRIEND = 2002


def _entries(text: str) -> list[str]:
    """Строки журнала без заголовков дней: «14:05 ✅ …» → «✅ …»."""
    return [line[6:] for line in text.splitlines() if line[:2].isdigit() and line[2] == ":"]


async def test_menu_has_journal_button():
    assert MENU_JOURNAL in [button.text for row in main_menu().keyboard for button in row]


async def test_empty_journal(db, harness, user):
    await harness.send(MENU_JOURNAL)
    assert "Пока пусто" in harness.text
    assert harness.labels == ["• 10", "25", "50"]


async def test_actions_show_up_newest_first(db, harness, user):
    transport = next(c for c in await finance.categories(db, OWNER, "expense") if "Транспорт" in c["name"])
    await harness.send("1500 такси")
    await harness.click(CatCb(act="pick", id=transport["id"]))
    await harness.click(TaskListCb(view="new"))
    await harness.send("Купить хлеб")
    await harness.click(NavCb(action="skip"))
    await harness.click(harness.button("Без даты"))
    await harness.click(NavCb(action="save"))
    [task] = await db.execute_fetchall("SELECT id FROM tasks")
    await harness.click(TaskCb(act="done", id=task["id"]))

    await harness.send(MENU_JOURNAL)
    assert "последние 3 действия" in harness.text and "<b>Сегодня</b>" in harness.text
    assert _entries(harness.text) == [
        "✅ Выполнена задача «Купить хлеб»",
        "📋 Новая задача «Купить хлеб»",
        "➖ Расход 1 500 ₸ · 🚕 Транспорт — такси",
    ]


async def test_other_sections_are_logged(db, harness, user):
    now = tu.utcnow()
    habit_id = await habits.create(db, OWNER, "Зарядка", tu.ALL_DAYS, None, TZ, now)
    countdown_id = await countdowns.create(db, OWNER, "Отпуск", None, "2027-01-01", "09:00", TZ, now)
    await harness.click(HabitCb(act="toggle", id=habit_id))
    await harness.click(HabitCb(act="toggle", id=habit_id))
    await harness.click(CountdownCb(act="pause", id=countdown_id))
    await harness.click(SetCb(act="curset", v="$"))

    await harness.send(MENU_JOURNAL)
    assert _entries(harness.text) == [
        "💱 Валюта: $",
        "⏸ Отсчёт «Отпуск» на паузе",
        "⬜ Снята отметка: «Зарядка»",
        "☑️ Отмечена привычка «Зарядка»",
    ]


async def test_limit_buttons(db, harness, user):
    for i in range(30):
        await journal.add(db, OWNER, f"Действие {i}", NOW + timedelta(minutes=i))

    await harness.send(MENU_JOURNAL)
    assert "последние 10 действий" in harness.text
    assert _entries(harness.text)[0] == "Действие 29" and len(_entries(harness.text)) == 10

    await harness.click(JournalCb(n=25))
    assert len(_entries(harness.text)) == 25 and harness.labels == ["10", "• 25", "50"]

    await harness.click(JournalCb(n=50))  # записей меньше, чем просили, — показываем все
    assert "последние 30 действий" in harness.text and len(_entries(harness.text)) == 30

    await harness.click(JournalCb(n=7))  # такой кнопки нет — значит, старая или подделанная
    assert "Эта кнопка устарела" in harness.toasts[-1]


async def test_each_user_sees_only_own_journal(db, harness, user):
    await access.allow(db, FRIEND, "Друг", None, NOW)
    await harness.send("500 кофе", user_id=FRIEND)
    await harness.click(harness.button("Еда"), user_id=FRIEND)
    await tasks.create(db, OWNER, {"title": "Задача владельца"}, TZ, NOW)
    await harness.click(TaskListCb(view="nodate"))
    await harness.click(harness.button("Задача владельца"))
    await harness.click(harness.button("Выполнено"))

    await harness.send(MENU_JOURNAL, user_id=FRIEND)
    assert _entries(harness.text) == ["➖ Расход 500 ₸ · 🍔 Еда — кофе"]
    await harness.send(MENU_JOURNAL)
    assert _entries(harness.text) == ["✅ Выполнена задача «Задача владельца»"]


async def test_owner_access_actions_are_logged(db, harness, user):
    await harness.send(f"/allow {FRIEND}")
    await harness.send(f"/deny {FRIEND}")
    await harness.send(MENU_JOURNAL)
    assert _entries(harness.text) == [f"🔒 Закрыт доступ: id {FRIEND}", f"🔓 Открыт доступ: id {FRIEND}"]


async def test_old_entries_are_trimmed(db):
    for i in range(journal.KEEP + 5):
        await journal.add(db, OWNER, f"Действие {i}", NOW)
    await journal.add(db, FRIEND, "Чужое", NOW)

    rows = await journal.recent(db, OWNER, 1000)
    assert len(rows) == journal.KEEP
    assert rows[0]["text"] == f"Действие {journal.KEEP + 4}" and rows[-1]["text"] == "Действие 5"
    assert len(await journal.recent(db, FRIEND, 1000)) == 1


def test_journal_fits_one_telegram_message():
    # Длинные записи за разные дни (у каждой свой заголовок дня): лишние не показываем, а не падаем.
    entries = [{"at": tu.to_db(NOW - timedelta(days=i)), "text": "Очень длинная запись " * 20} for i in range(50)]
    text, _ = journal_view(entries, 50, TZ, tu.local_today(TZ, NOW))
    assert len(text) <= 4096
    assert "не влезли в одно сообщение" in text


async def test_task_title_is_clipped_in_journal(db, harness, user):
    title = "Очень длинное название задачи, которое никак не влезает в одну строку журнала"
    task_id = await tasks.create(db, OWNER, {"title": title}, TZ, NOW)
    await harness.click(TaskCb(act="done", id=task_id))
    [entry] = await journal.recent(db, OWNER, 10)
    assert entry["text"].startswith("✅ Выполнена задача «Очень длинное") and entry["text"].endswith("…»")
    assert len(entry["text"]) < 70
