"""Задача из обычной фразы: «завтра в 10 позвонить маме», «каждый понедельник спортзал в 19».

Свои правила, без сети и платных вызовов. Понимает: сегодня / завтра / послезавтра, дни недели,
«15 ноября», «15.11», время «в 10», «в 10:30», «в 7 вечера», «через 2 часа / 3 дня / неделю»,
повторы «каждый день», «по будням», «каждую неделю», «каждый месяц», «каждый понедельник».
Если ничего из этого нет — возвращает None, и текст разбирается как раньше (сумма или кнопки).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from bot.services import timeutil as tu

_MONTHS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")
_MONTH_WORD = (
    r"(январ[яь]|феврал[яь]|марта?|апрел[яь]|ма[яй]|июн[яь]|июл[яь]|августа?|сентябр[яь]|октябр[яь]|ноябр[яь]"
    r"|декабр[яь])"
)
_WEEKDAYS = {
    0: r"понедельник\w*|пн",
    1: r"вторник\w*|вт",
    2: r"сред[уаы]|средам|ср",
    3: r"четверг\w*|чт",
    4: r"пятниц[уаы]|пятницам|пт",
    5: r"суббот[уаы]|субботам|сб",
    6: r"воскресень[еяю]|воскресеньям|вс",
}
_WEEKDAY = "|".join(f"(?P<wd{n}>{pattern})" for n, pattern in _WEEKDAYS.items())
_B = r"(?<![\w])"  # начало слова
_E = r"(?![\w])"  # конец слова

_REPEAT_RULES = [
    (re.compile(rf"{_B}(каждый\s+день|ежедневно){_E}", re.I), tu.REPEAT_DAILY),
    (re.compile(rf"{_B}(по\s+будням|каждый\s+будний\s+день){_E}", re.I), tu.REPEAT_WEEKDAYS),
    (re.compile(rf"{_B}(каждую\s+неделю|еженедельно){_E}", re.I), tu.REPEAT_WEEKLY),
    (re.compile(rf"{_B}(каждый\s+месяц|ежемесячно){_E}", re.I), tu.REPEAT_MONTHLY),
]
_EVERY_WEEKDAY = re.compile(rf"{_B}(?:кажд(?:ый|ую|ое)|по)\s+(?:{_WEEKDAY}){_E}", re.I)
_ON_WEEKDAY = re.compile(rf"{_B}(?:на|во?)\s+(?:{_WEEKDAY}){_E}", re.I)
_RELATIVE_DAY = re.compile(rf"{_B}(?:на\s+)?(сегодня|завтра|послезавтра){_E}", re.I)
_DAY_MONTH = re.compile(rf"{_B}(?:на\s+)?(\d{{1,2}})\s+{_MONTH_WORD}{_E}", re.I)
# Месяц — двумя цифрами: «1.5» — это скорее сумма, чем 1 мая.
_DAY_DOT_MONTH = re.compile(rf"{_B}(?:на\s+)?(\d{{1,2}})[./](\d{{2}})(?:[./](\d{{2}}|\d{{4}}))?{_E}")
_IN = re.compile(
    rf"{_B}через\s+(?:(\d+)\s+)?(полчаса|минут[уы]?|час(?:а|ов)?|день|дн(?:я|ей)|недел(?:ю|и|ь))(?![\w])", re.I
)
_TIME_WITH_PREP = re.compile(rf"{_B}(?:в|к)\s+(\d{{1,2}})(?:[:.](\d{{2}}))?(?:\s+(утра|дня|вечера|ночи))?{_E}", re.I)
_TIME_BARE = re.compile(rf"{_B}(\d{{1,2}}):(\d{{2}}){_E}")
_REMIND = re.compile(r"^(?:напомни(?:ть)?|напоминание)(?:\s+мне)?\s+", re.I)


@dataclass(frozen=True)
class TaskGuess:
    title: str
    due_date: date | None
    due_time: time | None
    repeat: str = tu.REPEAT_NONE


def _weekday(match: re.Match) -> int:
    return next(n for n in _WEEKDAYS if match.group(f"wd{n}"))


def _next_weekday(today: date, weekday: int) -> date:
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def _month(word: str) -> int:
    word = word.casefold()
    # «ма» (май) — короткая основа, проверяем последней, чтобы не перехватить «март».
    order = sorted(range(12), key=lambda i: -len(_MONTHS[i]))
    return next(i for i in order if word.startswith(_MONTHS[i])) + 1


def _hour(value: str, part: str | None) -> int | None:
    hour = int(value)
    part = (part or "").casefold()
    if part in ("дня", "вечера") and hour < 12:
        hour += 12
    elif part == "ночи" and hour == 12:
        hour = 0
    return hour if 0 <= hour <= 23 else None


def _future_date(day: int, month: int, year: int | None, today: date) -> date | None:
    try:
        result = date(year or today.year, month, day)
    except ValueError:
        return None
    if year is None and result < today:
        result = date(today.year + 1, month, day)
    return result


def parse(text: str, now: datetime) -> TaskGuess | None:
    """now — местное время владельца. None, если в тексте нет ни даты, ни времени, ни повтора."""
    today = now.date()
    rest = text
    due_date: date | None = None
    due_time: time | None = None
    repeat = tu.REPEAT_NONE
    by_weekday = False  # дата — ближайший день недели: сегодняшний, но уже прошедший по времени, — через неделю

    def cut(match: re.Match) -> None:
        nonlocal rest
        rest = rest[: match.start()] + " " + rest[match.end() :]

    for pattern, value in _REPEAT_RULES:
        if match := pattern.search(rest):
            repeat = value
            cut(match)
            break
    if match := _EVERY_WEEKDAY.search(rest):
        repeat, due_date, by_weekday = tu.REPEAT_WEEKLY, _next_weekday(today, _weekday(match)), True
        cut(match)
    if match := _IN.search(rest):
        count, unit = int(match.group(1) or 1), match.group(2).casefold()
        if unit == "полчаса":
            moment = now + timedelta(minutes=30)
        elif unit.startswith("минут"):
            moment = now + timedelta(minutes=count)
        elif unit.startswith("час"):
            moment = now + timedelta(hours=count)
        else:
            moment = None
            due_date = today + timedelta(days=count * (7 if unit.startswith("недел") else 1))
        if moment is not None:
            due_date, due_time = moment.date(), moment.time().replace(second=0, microsecond=0)
        cut(match)
    if due_date is None and (match := _RELATIVE_DAY.search(rest)):
        due_date = today + timedelta(days=("сегодня", "завтра", "послезавтра").index(match.group(1).casefold()))
        cut(match)
    if due_date is None and (match := _ON_WEEKDAY.search(rest)):
        due_date, by_weekday = _next_weekday(today, _weekday(match)), True
        cut(match)
    if (
        due_date is None
        and (match := _DAY_MONTH.search(rest))
        and (parsed := _future_date(int(match.group(1)), _month(match.group(2)), None, today)) is not None
    ):
        due_date = parsed
        cut(match)
    if due_time is None:
        for pattern in (_TIME_WITH_PREP, _TIME_BARE):
            match = pattern.search(rest)
            if not match:
                continue
            hour = _hour(match.group(1), match.group(3) if pattern is _TIME_WITH_PREP else None)
            minute = int(match.group(2) or 0)
            if hour is not None and minute < 60:
                due_time = time(hour, minute)
                cut(match)
                break

    # После времени: «в 10.30» — это время, а «15.11» без предлога — дата.
    if due_date is None and (match := _DAY_DOT_MONTH.search(rest)):
        year = match.group(3)
        year = int(year) + (2000 if len(year) == 2 else 0) if year else None
        if (parsed := _future_date(int(match.group(1)), int(match.group(2)), year, today)) is not None:
            due_date = parsed
            cut(match)

    if due_date is None and due_time is None and repeat == tu.REPEAT_NONE:
        return None
    if due_date is None:
        # Только время — сегодня, если оно ещё впереди, иначе завтра. Только повтор — с сегодня.
        late = due_time is not None and due_time <= now.time()
        due_date = today + timedelta(days=1) if late else today
    if by_weekday and due_date == today and due_time is not None and due_time <= now.time():
        due_date += timedelta(days=7)
    if repeat == tu.REPEAT_WEEKDAYS:
        while due_date.weekday() >= 5:
            due_date += timedelta(days=1)
    title = _REMIND.sub("", re.sub(r"\s+", " ", rest).strip(" ,.;:-—")).strip(" ,.;:-—")
    if not title:
        return None
    return TaskGuess(title[0].upper() + title[1:], due_date, due_time, repeat)
