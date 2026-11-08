"""Регулярные платежи: аренда, связь, подписки. В день платежа бот напоминает с кнопкой «Оплачено»."""

from datetime import date, datetime, time

from aiosqlite import Connection, Row

from bot.db import execute, fetchall, fetchone
from bot.services import timeutil as tu

MONTHLY = "monthly"
WEEKLY = "weekly"
YEARLY = "yearly"
PERIODS = (MONTHLY, WEEKLY, YEARLY)
# Во сколько по местному времени напоминать в день платежа; без ответа — снова в это же время завтра.
REMIND_AT = time(10, 0)

_SELECT = (
    "SELECT p.*, c.name AS category, a.name AS account FROM payments p"
    " JOIN categories c ON c.id = p.category_id LEFT JOIN accounts a ON a.id = p.account_id"
)


def next_due(day: date, period: str, anchor_day: int | None) -> date:
    if period == YEARLY:
        return tu.add_years(day, 1)
    return tu.next_repeat_date(day, tu.REPEAT_MONTHLY if period == MONTHLY else tu.REPEAT_WEEKLY, anchor_day)


def remind_at(due: date, tz: str) -> str:
    return tu.to_db(tu.local_to_utc(due, REMIND_AT, tz))


async def create(
    db: Connection,
    user_id: int,
    title: str,
    amount: int,
    category_id: int,
    account_id: int | None,
    period: str,
    due: date,
    tz: str,
) -> int:
    return await execute(
        db,
        "INSERT INTO payments (user_id, title, amount, category_id, account_id, period, anchor_day, due_date,"
        " next_run_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            title,
            amount,
            category_id,
            account_id,
            period,
            due.day if period == MONTHLY else None,
            due.isoformat(),
            remind_at(due, tz),
        ),
    )


async def get(db: Connection, user_id: int, payment_id: int) -> Row | None:
    return await fetchone(db, _SELECT + " WHERE p.id = ? AND p.user_id = ?", (payment_id, user_id))


async def list_all(db: Connection, user_id: int) -> list[Row]:
    return await fetchall(db, _SELECT + " WHERE p.user_id = ? ORDER BY p.due_date, p.id", (user_id,))


async def due(db: Connection, now: datetime) -> list[Row]:
    """Платежи, о которых пора напомнить, с поясом и валютой владельца."""
    return await fetchall(
        db,
        "SELECT p.*, s.tz, s.currency FROM payments p JOIN settings s ON s.user_id = p.user_id"
        " WHERE p.next_run_at IS NOT NULL AND p.next_run_at <= ? ORDER BY p.next_run_at",
        (tu.to_db(now),),
    )


async def set_next_run(db: Connection, payment_id: int, next_run_at: str | None) -> None:
    await execute(db, "UPDATE payments SET next_run_at = ? WHERE id = ?", (next_run_at, payment_id))


async def advance(db: Connection, payment: Row, tz: str) -> date | None:
    """Платёж оплачен или пропущен: переносим на следующий период. Возвращает новую дату.

    None — этот раз уже отметили (второе нажатие, пришедшее одновременно с первым): переносим, только если
    дата в базе всё ещё та, что прочитали.
    """
    new_due = next_due(date.fromisoformat(payment["due_date"]), payment["period"], payment["anchor_day"])
    async with db.execute(
        "UPDATE payments SET due_date = ?, next_run_at = ? WHERE id = ? AND due_date = ?",
        (new_due.isoformat(), remind_at(new_due, tz), payment["id"], payment["due_date"]),
    ) as cursor:
        return new_due if cursor.rowcount == 1 else None


def rescheduled_run(payment: Row, tz: str, now: datetime) -> str | None:
    """next_run_at после смены пояса: тот же день и час по-местному. Уже отправленное напоминание
    (следующее назначено на будущее) не повторяем сегодня — переносим на ближайшие REMIND_AT."""
    if payment["next_run_at"] is None:
        return None
    at_due = tu.local_to_utc(date.fromisoformat(payment["due_date"]), REMIND_AT, tz)
    if payment["next_run_at"] > tu.to_db(now):
        at_due = max(at_due, tu.next_daily_run(REMIND_AT, tz, now))
    return tu.to_db(at_due)


async def set_amount(db: Connection, payment_id: int, amount: int) -> None:
    await execute(db, "UPDATE payments SET amount = ? WHERE id = ?", (amount, payment_id))


async def delete(db: Connection, payment_id: int) -> None:
    await execute(db, "DELETE FROM payments WHERE id = ?", (payment_id,))
