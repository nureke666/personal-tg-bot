"""Быстрый ввод текстом: «1500 такси» — расход, «+250000 зарплата» — доход, текст без суммы — задача."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bot.services.texts import parse_amount

EXPENSE = "expense"
INCOME = "income"

_AMOUNT = r"\d[\d  ]*(?:[.,]\d{1,2})?"
_LEADING = re.compile(rf"([+-]?)\s*({_AMOUNT})(?:\s+(.+))?", re.S)
_TRAILING = re.compile(rf"(.+?)\s+([+-]?)\s*({_AMOUNT})", re.S)


@dataclass(frozen=True)
class QuickOp:
    amount: int
    comment: str | None
    sign: str  # «+» — доход, «-» — расход, пусто — решим по прошлым операциям


def parse_op(text: str) -> QuickOp | None:
    """Сумма в начале или в конце текста, остальное — комментарий. None, если суммы нет."""
    text = text.strip()
    if match := _LEADING.fullmatch(text):
        sign, amount, comment = match.groups()
    elif match := _TRAILING.fullmatch(text):
        comment, sign, amount = match.groups()
    else:
        return None
    value = parse_amount(amount)
    if value is None:
        return None
    comment = (comment or "").strip()[:300] or None
    return QuickOp(value, comment, sign)


def _first_word(text: str) -> str:
    words = text.split()
    return words[0] if words else ""


def bare(name: str) -> str:
    """Без эмодзи и регистра: «🚕 Транспорт» → «транспорт»."""
    return re.sub(r"[^\w\s-]", "", name).strip().casefold()


def guess_category(op: QuickOp, history: Sequence[Mapping], categories: Sequence[Mapping]) -> tuple[str, int] | None:
    """(kind, id категории) для операции или None, если надо спросить.

    history — прошлые операции с комментарием, свежие первыми; categories — активные категории.
    Сначала ищем операцию с тем же комментарием, потом с тем же первым словом, потом категорию по названию.
    """
    if not op.comment:
        return None
    default = INCOME if op.sign == "+" else EXPENSE
    kinds = {"+": {INCOME}, "-": {EXPENSE}}.get(op.sign, {INCOME, EXPENSE})
    key = op.comment.casefold()
    for row in history:
        if row["kind"] in kinds and row["comment"].casefold() == key:
            return row["kind"], row["category_id"]
    # По первому слову — только в ожидаемом виде, чтобы «подарок маме» не стал доходом.
    first = _first_word(key)
    for row in history:
        if row["kind"] == default and _first_word(row["comment"].casefold()) == first:
            return row["kind"], row["category_id"]
    # По названию категории: слово из текста — только в ожидаемом виде, весь текст целиком — в любом
    # («зарплата» — доход, а «подарок маме» — не доход «🎁 Подарок»).
    words = set(re.findall(r"\w+", key))
    for category in categories:
        name = bare(category["name"])
        if category["kind"] == default and name and name in words:
            return default, category["id"]
    for category in categories:
        if category["kind"] in kinds and bare(category["name"]) == key:
            return category["kind"], category["id"]
    return None
