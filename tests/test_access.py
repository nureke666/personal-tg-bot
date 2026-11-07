"""Доступ: запросы, /allow, /deny, /users и изоляция данных между людьми."""

from datetime import timedelta

from aiogram.methods import SendMessage

from bot.callbacks import AccessCb, CatCb, FinCb, TaskCb, TaskListCb
from bot.repo import access, finance, settings, tasks
from bot.services import scheduler
from bot.services import timeutil as tu
from tests.conftest import NOW, OWNER, TZ

FRIEND = 2002


def messages_to(harness, chat_id: int) -> list[str]:
    return [c.text for c in harness.session.calls if isinstance(c, SendMessage) and c.chat_id == chat_id]


async def test_stranger_gets_id_and_owner_gets_one_request(db, harness, user):
    await harness.send("/start", user_id=FRIEND)
    await harness.send("привет", user_id=FRIEND)

    replies = messages_to(harness, FRIEND)
    assert len(replies) == 2 and all(f"<code>{FRIEND}</code>" in r for r in replies)
    [request] = messages_to(harness, OWNER)  # второе сообщение не дёргает владельца ещё раз
    assert "просит доступ" in request
    assert await settings.get(db, FRIEND) is None


async def test_strangers_buttons_do_nothing(db, harness, user):
    await harness.click(TaskListCb(view="menu"), user_id=FRIEND)
    assert harness.session.calls == []


async def test_allow_from_request_button(db, harness, user):
    await harness.send("/start", user_id=FRIEND)
    await harness.click(AccessCb(act="allow", id=FRIEND))
    assert "Доступ открыт" in harness.text
    assert "открыл тебе доступ" in messages_to(harness, FRIEND)[-1]
    assert await access.get_request(db, FRIEND) is None

    await harness.send("/start", user_id=FRIEND)
    assert "Привет" in harness.text and "Только для тебя" not in harness.text
    assert await settings.get(db, FRIEND) is not None


async def test_allow_and_deny_commands(db, harness, user):
    await harness.send(f"/allow {FRIEND}")
    assert "Доступ открыт" in harness.text
    assert await access.is_allowed(db, FRIEND)
    await harness.send(f"/allow {FRIEND}")
    assert "уже есть" in harness.text

    await harness.send("/users")
    assert f"<code>{FRIEND}</code>" in harness.text

    await harness.send(f"/deny {FRIEND}")
    assert "Доступ закрыт" in harness.text
    assert not await access.is_allowed(db, FRIEND)
    await harness.send("/start", user_id=FRIEND)
    assert "личный бот" in messages_to(harness, FRIEND)[-1]


async def test_owner_cannot_lock_himself_out(db, harness, user):
    await harness.send(f"/deny {OWNER}")
    assert "нельзя" in harness.text
    await harness.send(f"/allow {OWNER}")
    assert "владелец" in harness.text
    await harness.send("/allow abc")
    assert "Укажи id" in harness.text


async def test_owner_sees_admin_help(db, harness, user):
    await harness.send("/start")
    assert "/allow" in harness.text


async def test_only_owner_can_manage_access(db, harness, user):
    await access.allow(db, FRIEND, "Друг", None, NOW)
    await harness.send("/allow 3003", user_id=FRIEND)
    await harness.send("/users", user_id=FRIEND)
    await harness.click(AccessCb(act="allow", id=3003), user_id=FRIEND)
    assert not await access.is_allowed(db, 3003)
    assert all("Доступ к боту" not in text for text in messages_to(harness, FRIEND))


async def test_each_user_has_own_space(db, harness, user):
    await access.allow(db, FRIEND, "Друг", None, NOW)
    owner_task = await tasks.create(
        db, OWNER, {"title": "Секрет владельца", "due_date": tu.local_today(TZ).isoformat()}, TZ, tu.utcnow()
    )
    await harness.click(TaskListCb(view="today"), user_id=FRIEND)
    assert "Секрет" not in str(harness.markup)
    # Даже подделанная кнопка с чужим id ничего не открывает и не меняет.
    await harness.click(TaskCb(act="open", id=owner_task), user_id=FRIEND)
    assert "не найдена" in harness.text
    await harness.click(TaskCb(act="delok", id=owner_task), user_id=FRIEND)
    assert await tasks.get(db, OWNER, owner_task) is not None


async def test_foreign_category_is_rejected(db, harness, user):
    await access.allow(db, FRIEND, "Друг", None, NOW)
    owner_food = (await finance.categories(db, OWNER, finance.EXPENSE))[0]
    await harness.click(FinCb(act="add", v="expense"), user_id=FRIEND)
    await harness.send("500", user_id=FRIEND)
    await harness.click(CatCb(act="pick", id=owner_food["id"]), user_id=FRIEND)
    assert harness.toasts[-1] == "Эта категория недоступна"
    assert await finance.count_ops(db, FRIEND) == 0


async def test_scheduler_skips_revoked_users_without_backlog(db, user, fake_bot):
    await access.allow(db, FRIEND, "Друг", None, NOW)
    await settings.ensure(db, FRIEND, TZ, NOW)
    task_id = await tasks.create(
        db, FRIEND, {"title": "Его дело", "due_date": "2026-10-17", "due_time": "10:00", "offsets": [0]}, TZ, NOW
    )
    await access.revoke(db, FRIEND)
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=3))
    assert fake_bot.sent == []
    assert await tasks.pending_reminders(db, task_id) == 0  # не копится: после /allow не придёт пачкой

    await access.allow(db, FRIEND, "Друг", None, NOW)
    await scheduler.tick(fake_bot, db, NOW + timedelta(hours=4))
    assert fake_bot.sent == []


async def test_changing_owner_id_drops_old_owner(db):
    await access.sync_owner(db, OWNER, NOW)
    await access.sync_owner(db, FRIEND, NOW)
    assert await access.allowed_ids(db) == {FRIEND}
