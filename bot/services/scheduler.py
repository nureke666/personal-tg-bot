"""Фоновый планировщик. Раз в tick_seconds берёт из БД всё, чему пора сработать.

Состояние лежит только в БД, поэтому перезапуск бота ничего не теряет: пропущенное за время
простоя отправится один раз при следующем тике, а дальше пересчитается следующее время.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, Message
from aiosqlite import Connection

from bot.repo import access, countdowns, habits, settings, tasks
from bot.services import backup, morning, summary
from bot.services import timeutil as tu
from bot.views import countdowns as countdown_views
from bot.views import habits as habit_views
from bot.views import summary as summary_views
from bot.views import tasks as task_views

log = logging.getLogger(__name__)

# Напоминание, пришедшее позже этого, помечается «с опозданием».
LATE_AFTER = timedelta(minutes=10)


async def run(bot: Bot, db: Connection, interval: int) -> None:
    while True:
        try:
            await tick(bot, db, tu.utcnow())
        except Exception:
            log.exception("Ошибка в планировщике")
        await asyncio.sleep(interval)


class Outbox:
    """Отправка из планировщика. Тем, у кого закрыт доступ, ничего не шлём, но событие считаем обработанным."""

    def __init__(self, bot: Bot, allowed: set[int]) -> None:
        self.bot = bot
        self.allowed = allowed

    async def send(self, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
        """True — обработано (доставлено или повторять бессмысленно), False — повторить в следующий тик."""
        handled, _ = await self.send_message(chat_id, text, markup)
        return handled

    async def send_message(
        self, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None
    ) -> tuple[bool, Message | None]:
        """Как send, плюс само сообщение — None, если ничего не ушло."""
        sent: list[Message] = []

        async def request() -> None:
            sent.append(await self.bot.send_message(chat_id, text, reply_markup=markup))

        handled = await self._deliver(chat_id, request)
        return handled, sent[0] if sent else None

    async def send_file(self, chat_id: int, file: BufferedInputFile, caption: str) -> bool:
        return await self._deliver(
            chat_id, lambda: self.bot.send_document(chat_id, file, caption=caption, disable_notification=True)
        )

    async def _deliver(self, chat_id: int, request: Callable[[], Awaitable[object]]) -> bool:
        if chat_id not in self.allowed:
            return True
        try:
            await request()
        except TelegramRetryAfter as error:
            log.warning("Telegram просит подождать %s с", error.retry_after)
            await asyncio.sleep(error.retry_after)
            return False
        except TelegramNetworkError as error:
            log.warning("Нет связи с Telegram: %s", error)
            return False
        except TelegramAPIError:
            log.exception("Telegram отклонил сообщение для %s", chat_id)
        return True


async def tick(bot: Bot, db: Connection, now: datetime) -> None:
    out = Outbox(bot, await access.allowed_ids(db))
    await roll_recurring(db, now)
    await task_reminders(out, db, now)
    await countdown_messages(out, db, now)
    await habit_reminders(out, db, now)
    await morning_lists(out, db, now)
    await digests(out, db, now)
    await checkins(out, db, now)
    await weekly_summaries(out, db, now)
    await backups(out, db, now)


async def roll_recurring(db: Connection, now: datetime) -> None:
    """Повторяющаяся задача, которую не отметили до конца дня, сама переезжает на следующий раз."""
    for user in await settings.all_users(db):
        for task in await tasks.missed_recurring(db, user["user_id"], tu.local_today(user["tz"], now)):
            next_date = await tasks.move_to_next(db, task, user["tz"], now)
            log.info("Задача %s пропущена, перенесена на %s", task["id"], next_date)


async def task_reminders(out: Outbox, db: Connection, now: datetime) -> None:
    sent_for: set[int] = set()
    for row in await tasks.due_reminders(db, now):
        # После простоя у задачи может набраться несколько напоминаний — отправляем одно.
        if row["id"] not in sent_for:
            late = now - tu.from_db(row["fire_at"]) > LATE_AFTER
            text, markup = task_views.reminder_message(row, tu.local_today(row["tz"], now), late)
            if not await out.send(row["user_id"], text, markup):
                continue
            sent_for.add(row["id"])
        await tasks.mark_reminder_sent(db, row["reminder_id"], now)


async def countdown_messages(out: Outbox, db: Connection, now: datetime) -> None:
    for countdown in await countdowns.due(db, now):
        tz = countdown["tz"]
        today = tu.local_today(tz, now)
        left = countdown_views.days_left(countdown, today)
        if left < 0:
            # День события прошёл, пока бот был выключен: поздравлять поздно. Разовый закрываем,
            # ежегодный переносим на следующий год.
            if countdown["yearly"]:
                await countdowns.roll_year(db, countdown, today, tz, now)
            else:
                await countdowns.set_status(db, countdown, countdowns.DONE, tz, now)
            continue
        lead = countdown["lead_days"]
        if lead is None or left <= lead:
            text = countdown_views.countdown_message(countdown["title"], countdown["text"], left)
            if not await out.send(countdown["user_id"], text):
                continue
        if left == 0 and countdown["yearly"]:
            await countdowns.roll_year(db, countdown, today + timedelta(days=1), tz, now)
        elif left == 0:
            await countdowns.set_status(db, countdown, countdowns.DONE, tz, now)
        else:
            await countdowns.set_next_run(db, countdown["id"], countdowns.next_run(countdown["send_time"], tz, now))


async def habit_reminders(out: Outbox, db: Connection, now: datetime) -> None:
    for habit in await habits.due_reminders(db, now):
        tz = habit["tz"]
        today = tu.local_today(tz, now)
        already_done = habit["id"] in await habits.marked_ids(db, habit["user_id"], today)
        if not already_done:
            text, markup = habit_views.reminder_message(habit, today)
            if not await out.send(habit["user_id"], text, markup):
                continue
        await habits.set_next_run(db, habit["id"], habits.next_run(habit["remind_time"], habit["days_mask"], tz, now))


async def morning_lists(out: Outbox, db: Connection, now: datetime) -> None:
    """Список привычек на день — новым сообщением, закреплённым вместо вчерашнего."""
    for user in await settings.due_mornings(db, now):
        uid, tz = user["user_id"], user["tz"]
        # morning_next_at = NULL бывает сразу после обновления: только планируем, без внепланового списка.
        if user["morning_next_at"] is not None:
            today = tu.local_today(tz, now)
            message = None
            if await habits.planned(db, uid, today):
                handled, message = await out.send_message(uid, *await morning.screen(db, uid, tz, today, today))
                if not handled:
                    continue
            # Нечего делать сегодня — вчерашний список всё равно снимаем, чтобы не висел.
            await morning.replace_pin(out.bot, db, user, message.message_id if message else None, today)
        await settings.update(db, uid, morning_next_at=settings.next_at("morning", user["morning_time"], tz, now))


async def digests(out: Outbox, db: Connection, now: datetime) -> None:
    for user in await settings.due_digests(db, now):
        tz = user["tz"]
        text = await task_views.digest_screen(db, user["user_id"], tz, now)
        if text and not await out.send(user["user_id"], text):
            continue
        next_at = tu.next_daily_run(tu.parse_hm(user["digest_time"]), tz, now)
        await settings.update(db, user["user_id"], digest_next_at=tu.to_db(next_at))


async def checkins(out: Outbox, db: Connection, now: datetime) -> None:
    for user in await settings.due_checkins(db, now):
        tz = user["tz"]
        today = tu.local_today(tz, now)
        planned = await habits.planned(db, user["user_id"], today)
        marked = await habits.marked_ids(db, user["user_id"], today)
        if any(h["id"] not in marked for h in planned):
            text, markup = await habit_views.day_screen(db, user["user_id"], tz, today, today, view=habit_views.EVENING)
            if not await out.send(user["user_id"], text, markup):
                continue
        next_at = tu.next_daily_run(tu.parse_hm(user["checkin_time"]), tz, now)
        await settings.update(db, user["user_id"], checkin_next_at=tu.to_db(next_at))


async def weekly_summaries(out: Outbox, db: Connection, now: datetime) -> None:
    for user in await settings.due_weekly(db, now):
        tz = user["tz"]
        # weekly_next_at = NULL бывает сразу после обновления: только планируем, без внеплановых итогов.
        if user["weekly_next_at"] is not None:
            today = tu.local_today(tz, now)
            data = await summary.build(db, user["user_id"], tz, "week", today, today)
            if not data.empty:
                text = summary_views.summary_text(data, user["currency"])
                if not await out.send(user["user_id"], text, summary_views.summary_kb("week", today, today)):
                    continue
        next_at = settings.next_at("weekly", user["weekly_time"], tz, now)
        await settings.update(db, user["user_id"], weekly_next_at=next_at)


async def backups(out: Outbox, db: Connection, now: datetime) -> None:
    owner = await settings.due_backup(db, now)
    if not owner:
        return
    tz = owner["tz"]
    day = tu.local_today(tz, now)
    if not await out.send_file(owner["user_id"], await backup.snapshot(db, day), backup.caption(day)):
        return
    await settings.update(
        db, owner["user_id"], backup_next_at=settings.next_at("backup", owner["backup_time"], tz, now)
    )
