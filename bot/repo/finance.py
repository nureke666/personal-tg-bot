from datetime import date, datetime

from aiosqlite import Connection, Row

from bot.db import execute, fetchall, fetchone, scalar
from bot.services import timeutil as tu

EXPENSE = "expense"
INCOME = "income"

DEFAULT_CATEGORIES = {
    EXPENSE: [
        "🍔 Еда",
        "🚕 Транспорт",
        "🏠 Жильё",
        "🛒 Покупки",
        "💊 Здоровье",
        "🎉 Развлечения",
        "📱 Связь",
        "📦 Другое",
    ],
    INCOME: ["💼 Зарплата", "🎁 Подарок", "💸 Другое"],
}

DEFAULT_ACCOUNTS = ["💳 Карта", "💵 Наличные"]

_OP_SELECT = (
    "SELECT o.*, c.name AS category, a.name AS account FROM transactions o"
    " JOIN categories c ON c.id = o.category_id LEFT JOIN accounts a ON a.id = o.account_id"
)


def _range(start: date | None, end: date | None, column: str = "op_date") -> tuple[str, tuple]:
    sql, params = "", ()
    if start:
        sql += f" AND {column} >= ?"
        params += (start.isoformat(),)
    if end:
        sql += f" AND {column} <= ?"
        params += (end.isoformat(),)
    return sql, params


# --- категории ---
async def seed_categories(db: Connection, user_id: int) -> None:
    if await scalar(db, "SELECT COUNT(*) FROM categories WHERE user_id = ?", (user_id,)):
        return
    for kind, names in DEFAULT_CATEGORIES.items():
        for name in names:
            await execute(db, "INSERT INTO categories (user_id, kind, name) VALUES (?, ?, ?)", (user_id, kind, name))


async def categories(db: Connection, user_id: int, kind: str, archived: bool = False) -> list[Row]:
    return await fetchall(
        db,
        "SELECT * FROM categories WHERE user_id = ? AND kind = ? AND archived = ? ORDER BY id",
        (user_id, kind, int(archived)),
    )


async def category(db: Connection, user_id: int, category_id: int) -> Row | None:
    return await fetchone(db, "SELECT * FROM categories WHERE id = ? AND user_id = ?", (category_id, user_id))


async def add_category(db: Connection, user_id: int, kind: str, name: str) -> int:
    return await execute(db, "INSERT INTO categories (user_id, kind, name) VALUES (?, ?, ?)", (user_id, kind, name))


async def rename_category(db: Connection, category_id: int, name: str) -> None:
    await execute(db, "UPDATE categories SET name = ? WHERE id = ?", (name, category_id))


async def set_category_archived(db: Connection, category_id: int, archived: bool) -> None:
    await execute(db, "UPDATE categories SET archived = ? WHERE id = ?", (int(archived), category_id))


async def set_category_limit(db: Connection, category_id: int, limit: int | None) -> None:
    await execute(db, "UPDATE categories SET month_limit = ? WHERE id = ?", (limit, category_id))


# --- счета ---
async def seed_accounts(db: Connection, user_id: int) -> None:
    """Счета по умолчанию; первый становится основным — на него идут новые операции."""
    if await scalar(db, "SELECT COUNT(*) FROM accounts WHERE user_id = ?", (user_id,)):
        return
    ids = [await add_account(db, user_id, name) for name in DEFAULT_ACCOUNTS]
    await execute(db, "UPDATE settings SET default_account_id = ? WHERE user_id = ?", (ids[0], user_id))


async def accounts(db: Connection, user_id: int, archived: bool = False) -> list[Row]:
    return await fetchall(
        db, "SELECT * FROM accounts WHERE user_id = ? AND archived = ? ORDER BY id", (user_id, int(archived))
    )


async def account(db: Connection, user_id: int, account_id: int) -> Row | None:
    return await fetchone(db, "SELECT * FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id))


async def add_account(db: Connection, user_id: int, name: str) -> int:
    return await execute(db, "INSERT INTO accounts (user_id, name) VALUES (?, ?)", (user_id, name))


async def rename_account(db: Connection, account_id: int, name: str) -> None:
    await execute(db, "UPDATE accounts SET name = ? WHERE id = ?", (name, account_id))


async def set_account_start(db: Connection, account_id: int, start_balance: int) -> None:
    await execute(db, "UPDATE accounts SET start_balance = ? WHERE id = ?", (start_balance, account_id))


async def set_account_archived(db: Connection, account_id: int, archived: bool) -> None:
    await execute(db, "UPDATE accounts SET archived = ? WHERE id = ?", (int(archived), account_id))


async def account_balances(db: Connection, user_id: int) -> dict[int, int]:
    """Баланс каждого счёта: начальный + доходы − расходы + переводы на счёт − переводы со счёта."""
    rows = await fetchall(
        db,
        "SELECT a.id, a.start_balance"
        " + COALESCE((SELECT SUM(CASE WHEN o.kind = 'income' THEN o.amount ELSE -o.amount END)"
        "   FROM transactions o WHERE o.account_id = a.id), 0)"
        " + COALESCE((SELECT SUM(t.amount) FROM transfers t WHERE t.to_id = a.id), 0)"
        " - COALESCE((SELECT SUM(t.amount) FROM transfers t WHERE t.from_id = a.id), 0)"
        " FROM accounts a WHERE a.user_id = ?",
        (user_id,),
    )
    return {row[0]: row[1] for row in rows}


async def add_transfer(
    db: Connection, user_id: int, from_id: int, to_id: int, amount: int, op_date: date, now: datetime
) -> int:
    return await execute(
        db,
        "INSERT INTO transfers (user_id, from_id, to_id, amount, op_date, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, from_id, to_id, amount, op_date.isoformat(), tu.to_db(now)),
    )


_TRANSFER_SELECT = (
    "SELECT t.*, f.name AS from_name, o.name AS to_name FROM transfers t"
    " JOIN accounts f ON f.id = t.from_id JOIN accounts o ON o.id = t.to_id"
)


async def recent_transfers(db: Connection, user_id: int, limit: int = 15) -> list[Row]:
    return await fetchall(db, _TRANSFER_SELECT + " WHERE t.user_id = ? ORDER BY t.id DESC LIMIT ?", (user_id, limit))


async def transfer(db: Connection, user_id: int, transfer_id: int) -> Row | None:
    return await fetchone(db, _TRANSFER_SELECT + " WHERE t.id = ? AND t.user_id = ?", (transfer_id, user_id))


async def delete_transfer(db: Connection, transfer_id: int) -> None:
    await execute(db, "DELETE FROM transfers WHERE id = ?", (transfer_id,))


# --- операции ---
async def add_op(
    db: Connection,
    user_id: int,
    kind: str,
    amount: int,
    category_id: int,
    comment: str | None,
    op_date: date,
    now: datetime,
    account_id: int | None = None,
) -> int:
    return await execute(
        db,
        "INSERT INTO transactions (user_id, kind, amount, category_id, comment, op_date, created_at, account_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, kind, amount, category_id, comment, op_date.isoformat(), tu.to_db(now), account_id),
    )


async def op(db: Connection, user_id: int, op_id: int) -> Row | None:
    return await fetchone(db, _OP_SELECT + " WHERE o.id = ? AND o.user_id = ?", (op_id, user_id))


async def comment_history(db: Connection, user_id: int, limit: int = 500) -> list[Row]:
    """Недавние операции с комментарием в активных категориях, свежие первыми: по ним угадываем категорию."""
    return await fetchall(
        db,
        "SELECT o.kind, o.comment, o.category_id FROM transactions o JOIN categories c ON c.id = o.category_id"
        " WHERE o.user_id = ? AND o.comment IS NOT NULL AND c.archived = 0 ORDER BY o.id DESC LIMIT ?",
        (user_id, limit),
    )


async def all_categories(db: Connection, user_id: int) -> list[Row]:
    return await fetchall(db, "SELECT * FROM categories WHERE user_id = ? AND archived = 0 ORDER BY id", (user_id,))


async def set_op_category(db: Connection, op_id: int, category_id: int) -> None:
    await execute(db, "UPDATE transactions SET category_id = ? WHERE id = ?", (category_id, op_id))


async def set_op_account(db: Connection, op_id: int, account_id: int) -> None:
    await execute(db, "UPDATE transactions SET account_id = ? WHERE id = ?", (account_id, op_id))


async def set_op_date(db: Connection, op_id: int, op_date: date) -> None:
    await execute(db, "UPDATE transactions SET op_date = ? WHERE id = ?", (op_date.isoformat(), op_id))


async def delete_op(db: Connection, op_id: int) -> None:
    await execute(db, "DELETE FROM transactions WHERE id = ?", (op_id,))


async def recent_ops(db: Connection, user_id: int, offset: int, limit: int) -> list[Row]:
    return await fetchall(
        db,
        _OP_SELECT + " WHERE o.user_id = ? ORDER BY o.op_date DESC, o.id DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )


async def count_ops(db: Connection, user_id: int) -> int:
    return await scalar(db, "SELECT COUNT(*) FROM transactions WHERE user_id = ?", (user_id,))


async def ops_range(db: Connection, user_id: int, start: date | None, end: date | None) -> list[Row]:
    where, params = _range(start, end, "o.op_date")
    return await fetchall(
        db, _OP_SELECT + " WHERE o.user_id = ?" + where + " ORDER BY o.op_date, o.id", (user_id, *params)
    )


# --- суммы ---
async def totals(db: Connection, user_id: int, start: date | None, end: date | None) -> tuple[int, int]:
    """(доходы, расходы) за период."""
    where, params = _range(start, end)
    row = await fetchone(
        db,
        "SELECT COALESCE(SUM(CASE WHEN kind = 'income' THEN amount END), 0),"
        " COALESCE(SUM(CASE WHEN kind = 'expense' THEN amount END), 0)"
        " FROM transactions WHERE user_id = ?" + where,
        (user_id, *params),
    )
    return row[0], row[1]


async def by_category(db: Connection, user_id: int, kind: str, start: date | None, end: date | None) -> list[Row]:
    where, params = _range(start, end, "o.op_date")
    return await fetchall(
        db,
        "SELECT c.id, c.name, c.month_limit, SUM(o.amount) AS total"
        " FROM transactions o JOIN categories c ON c.id = o.category_id"
        " WHERE o.user_id = ? AND o.kind = ?" + where + " GROUP BY c.id ORDER BY total DESC",
        (user_id, kind, *params),
    )


async def spent(db: Connection, user_id: int, start: date, end: date, category_id: int | None = None) -> int:
    where, params = _range(start, end)
    if category_id is not None:
        where += " AND category_id = ?"
        params += (category_id,)
    return await scalar(
        db,
        "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE user_id = ? AND kind = 'expense'" + where,
        (user_id, *params),
    )


async def balance(db: Connection, user_id: int) -> int:
    """Все деньги: начальные балансы счетов + доходы − расходы. Переводы общую сумму не меняют."""
    start = await scalar(db, "SELECT COALESCE(SUM(start_balance), 0) FROM accounts WHERE user_id = ?", (user_id,))
    income, expense = await totals(db, user_id, None, None)
    return start + income - expense
