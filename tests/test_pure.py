"""Чистая логика: время, тексты, отсчёты, серии, отчёты, выгрузка, быстрый ввод."""

import io
from datetime import UTC, date, datetime, time, timedelta

import pytest
from openpyxl import load_workbook

from bot.services import export, quick, reports, smart_task, streaks, summary
from bot.services import timeutil as tu
from bot.services.texts import days, fmt_money, parse_amount, plural
from bot.views import habits as habit_views
from bot.views.countdowns import countdown_message
from tests.conftest import NOW, TZ

# --- время ---


def test_local_to_utc_almaty_is_utc_plus_5():
    assert tu.local_to_utc(date(2026, 10, 17), time(9, 0), TZ) == datetime(2026, 10, 17, 4, 0, tzinfo=UTC)


def test_next_daily_run_today_or_tomorrow():
    assert tu.next_daily_run(time(9, 0), TZ, NOW) == datetime(2026, 10, 17, 4, 0, tzinfo=UTC)
    # 07:00 уже прошло — следующий запуск завтра; ровно 08:00 тоже считается прошедшим.
    assert tu.next_daily_run(time(7, 0), TZ, NOW) == datetime(2026, 10, 18, 2, 0, tzinfo=UTC)
    assert tu.next_daily_run(time(8, 0), TZ, NOW) == datetime(2026, 10, 18, 3, 0, tzinfo=UTC)


def test_next_daily_run_respects_weekday_mask():
    # NOW — суббота, по будням ближайший запуск в понедельник 19-го.
    assert tu.next_daily_run(time(9, 0), TZ, NOW, tu.WEEKDAYS) == datetime(2026, 10, 19, 4, 0, tzinfo=UTC)


def test_next_daily_run_in_other_timezone():
    assert tu.next_daily_run(time(9, 0), "Europe/Moscow", NOW) == datetime(2026, 10, 17, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "repeat", "expected"),
    [
        (date(2026, 10, 17), tu.REPEAT_DAILY, date(2026, 10, 18)),
        (date(2026, 10, 16), tu.REPEAT_WEEKDAYS, date(2026, 10, 19)),
        (date(2026, 10, 17), tu.REPEAT_WEEKLY, date(2026, 10, 24)),
        (date(2026, 1, 31), tu.REPEAT_MONTHLY, date(2026, 2, 28)),
        (date(2026, 12, 15), tu.REPEAT_MONTHLY, date(2027, 1, 15)),
    ],
)
def test_next_repeat_date(start, repeat, expected):
    assert tu.next_repeat_date(start, repeat) == expected


def test_monthly_repeat_keeps_anchor_day_after_short_month():
    assert tu.next_repeat_date(date(2026, 2, 28), tu.REPEAT_MONTHLY, anchor_day=31) == date(2026, 3, 31)


def test_advance_repeat_never_lands_in_the_past():
    today = date(2026, 10, 17)
    # Просрочено на три дня — следующий раз сегодня.
    assert tu.advance_repeat(date(2026, 10, 14), tu.REPEAT_DAILY, today) == today
    # Выполнено в свой день — следующий раз завтра.
    assert tu.advance_repeat(today, tu.REPEAT_DAILY, today) == date(2026, 10, 18)


def test_yearly_dates():
    assert tu.add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)
    assert tu.next_yearly(date(2026, 10, 18), date(2026, 10, 19)) == date(2027, 10, 18)
    assert tu.next_yearly(date(2020, 5, 1), date(2026, 10, 18)) == date(2027, 5, 1)
    assert tu.next_yearly(date(2025, 10, 18), date(2026, 10, 18)) == date(2026, 10, 18)


def test_tz_validation_and_label():
    assert tu.is_valid_tz("Asia/Almaty")
    assert not tu.is_valid_tz("Mars/Olympus")
    assert tu.tz_offset_label("Asia/Almaty") == "UTC+5"


# --- тексты ---


@pytest.mark.parametrize(
    ("n", "word"),
    [
        (1, "день"),
        (2, "дня"),
        (4, "дня"),
        (5, "дней"),
        (11, "дней"),
        (12, "дней"),
        (21, "день"),
        (22, "дня"),
        (111, "дней"),
    ],
)
def test_plural(n, word):
    assert plural(n, ("день", "дня", "дней")) == word


def test_money_format():
    assert fmt_money(150000, "₸") == "1 500 ₸"
    assert fmt_money(150050, "₸") == "1 500,50 ₸"
    assert fmt_money(-2500000, "₸") == "-25 000 ₸"
    assert fmt_money(100, "$", sign=True) == "+1 $"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1500", 150000), ("1 500", 150000), ("1500,50", 150050), ("1.5", 150), (" 2 350,5 ", 235050)],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "0", "-5", "1.234", "1e5", "10000000000"])
def test_parse_amount_rejects(text):
    assert parse_amount(text) is None


def test_parse_amount_options():
    assert parse_amount("-5", allow_negative=True) == -500
    assert parse_amount("0", allow_zero=True) == 0


# --- отсчёты ---


def test_countdown_message_counts_days():
    text = countdown_message("🏖 Отпуск", "Пора бронировать!", 42)
    assert "42 дня" in text and "Пора бронировать!" in text


def test_countdown_message_placeholder():
    assert "До отпуска 21 день!" in countdown_message("Отпуск", "До отпуска {дни}!", 21)


def test_countdown_message_on_the_day_and_escaping():
    text = countdown_message("<b>Свадьба</b>", None, 0)
    assert "сегодня" in text
    assert "&lt;b&gt;Свадьба" in text


def test_days_helper():
    assert days(3) == "3 дня"


# --- серии привычек ---

TODAY = date(2026, 10, 17)  # суббота


def _d(day: int) -> date:
    return date(2026, 10, day)


def test_streak_today_unmarked_does_not_break():
    marks = {_d(14), _d(15), _d(16)}
    assert streaks.current_streak(marks, tu.ALL_DAYS, _d(1), TODAY) == 3


def test_streak_breaks_on_missed_day():
    marks = {_d(12), _d(13), _d(15), _d(16), _d(17)}
    assert streaks.current_streak(marks, tu.ALL_DAYS, _d(1), TODAY) == 3
    assert streaks.best_streak(marks, tu.ALL_DAYS, _d(1), TODAY) == 3


def test_streak_skips_unscheduled_days():
    # По будням: пт 16, чт 15, ср 14 отмечены; суббота (сегодня) не назначена.
    marks = {_d(14), _d(15), _d(16)}
    assert streaks.current_streak(marks, tu.WEEKDAYS, _d(1), TODAY) == 3


def test_percent_and_week_grid():
    stats = streaks.stats({_d(15), _d(16)}, tu.ALL_DAYS, _d(14), TODAY)
    # Назначено 14, 15, 16 (сегодня не отмечено и не считается) → 2 из 3.
    assert stats.percent_30 == 67
    week = [status for _, status in stats.week]
    assert week == [streaks.OFF] * 3 + [streaks.MISSED, streaks.DONE, streaks.DONE, streaks.PENDING]


# --- отчёты и лимиты ---


def test_quit_stats():
    since = date(2026, 10, 1)
    clean = streaks.quit_stats(set(), since, date(2026, 10, 17))
    assert (clean.current, clean.best, clean.relapses_30, clean.last) == (16, 16, 0, None)

    relapses = {date(2026, 10, 6), date(2026, 10, 14), date(2026, 9, 1)}  # 1 сентября — до старта, не считаем
    stats = streaks.quit_stats(relapses, since, date(2026, 10, 17))
    # 1→6 октября: дошёл до 4; 6→14: до 7; с 14-го — 3.
    assert (stats.current, stats.best, stats.relapses_30, stats.last) == (3, 7, 2, date(2026, 10, 14))

    on_start = streaks.quit_stats({since}, since, since)
    assert (on_start.current, on_start.best) == (0, 0)


def test_period_ranges():
    assert reports.period_range("month", TODAY)[:2] == (date(2026, 10, 1), date(2026, 10, 31))
    assert reports.period_range("prev", TODAY)[:2] == (date(2026, 9, 1), date(2026, 9, 30))
    assert reports.period_range("week", TODAY)[:2] == (date(2026, 10, 12), date(2026, 10, 18))
    assert reports.period_range("all", TODAY)[:2] == (None, None)


def test_period_ranges_with_anchor_and_shift():
    year_ago = reports.shift("year", TODAY, -1)
    assert reports.period_range("year", TODAY, year_ago) == (date(2025, 1, 1), date(2025, 12, 31), "2025 год")
    assert reports.period_range("month", TODAY, reports.shift("month", TODAY, -10))[2] == "Декабрь 2025"
    assert reports.period_range("week", TODAY, date(2025, 12, 30))[2] == "Неделя 29.12–04.01.2026"
    assert reports.period_range("today", TODAY, date(2026, 10, 1))[2] == "1 октября 2026"
    assert reports.shift("month", date(2026, 1, 31), -1) == date(2025, 12, 1)
    assert not reports.has_next("month", TODAY, TODAY)
    assert reports.has_next("week", TODAY - timedelta(days=7), TODAY)


def _habit(**fields):
    base = {"title": "💧 Вода", "kind": "check", "days_mask": tu.ALL_DAYS, "archived": 0, "since": None}
    return base | {"created_at": "2026-10-01 00:00:00"} | fields


def test_summary_habit_line():
    week = (date(2026, 10, 12), date(2026, 10, 18))
    marks = {date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 16)}
    # Сегодня (17-е) не отмечено — ещё не провал, а 18-е в будущем.
    line = summary.habit_line(_habit(), marks, *week, TODAY, TZ)
    assert (line.done, line.planned) == (3, 5)
    weekdays = summary.habit_line(_habit(days_mask=tu.WEEKDAYS), marks, *week, TODAY, TZ)
    assert (weekdays.done, weekdays.planned) == (3, 5)

    assert summary.habit_line(_habit(created_at="2026-10-20 00:00:00"), marks, *week, TODAY, TZ) is None
    assert summary.habit_line(_habit(archived=1), set(), *week, TODAY, TZ) is None
    assert summary.habit_line(_habit(archived=1), marks, *week, TODAY, TZ).done == 3

    quit = summary.habit_line(_habit(kind="quit", since="2026-10-14"), marks, *week, TODAY, TZ)
    assert quit.quit and quit.relapses == 1  # срыв 12-го и 13-го был до старта


def test_limit_alerts_only_when_crossing():
    assert reports.limit_alert(0, 50_000, 100_000, "Еда", "₸") is None
    assert "⚠️" in reports.limit_alert(70_000, 85_000, 100_000, "Еда", "₸")
    assert reports.limit_alert(85_000, 90_000, 100_000, "Еда", "₸") is None
    assert "🚨" in reports.limit_alert(90_000, 100_000, 100_000, "Еда", "₸")
    assert "🚨" in reports.limit_alert(10_000, 150_000, 100_000, "Еда", "₸")
    assert reports.limit_alert(0, 150_000, None, "Еда", "₸") is None


# --- выгрузка ---

OPS = [
    {
        "op_date": "2026-10-01",
        "kind": "income",
        "category": "💼 Зарплата",
        "amount": 50_000_000,
        "comment": None,
        "account": "💳 Карта",
    },
    {
        "op_date": "2026-10-02",
        "kind": "expense",
        "category": "🍔 Еда",
        "amount": 150_050,
        "comment": "кофе; булка",
        "account": None,
    },
]


def test_csv_is_excel_friendly():
    content = export.to_csv(OPS)
    assert content.startswith(b"\xef\xbb\xbf")
    text = content.decode("utf-8-sig")
    assert text.splitlines()[0] == "Дата;Тип;Категория;Сумма;Комментарий;Счёт"
    assert '02.10.2026;Расход;🍔 Еда;1500,50;"кофе; булка";' in text
    assert "01.10.2026;Доход;💼 Зарплата;500000,00;;💳 Карта" in text


def test_xlsx_has_operations_and_summary():
    book = load_workbook(io.BytesIO(export.to_xlsx(OPS, "₸")))
    assert book.sheetnames == ["Операции", "Итоги по категориям"]
    rows = list(book["Операции"].iter_rows(min_row=2, values_only=True))
    assert len(rows) == 2 and rows[1][2] == "🍔 Еда" and float(rows[1][3]) == 1500.5
    summary = list(book["Итоги по категориям"].iter_rows(min_row=2, values_only=True))
    assert ("Доход", "💼 Зарплата", 500000) in [(k, c, float(s)) for k, c, s in summary]


# --- быстрый ввод ---


@pytest.mark.parametrize(
    ("text", "amount", "comment", "sign"),
    [
        ("1500 такси", 150_000, "такси", ""),
        ("такси 1500", 150_000, "такси", ""),
        ("1 500 такси домой", 150_000, "такси домой", ""),
        ("+250000 зарплата", 25_000_000, "зарплата", "+"),
        ("- 99,90 кофе", 9_990, "кофе", "-"),
        ("кофе с булкой 2 350,50", 235_050, "кофе с булкой", ""),
        ("700", 70_000, None, ""),
    ],
)
def test_quick_parse_op(text, amount, comment, sign):
    assert quick.parse_op(text) == quick.QuickOp(amount, comment, sign)


@pytest.mark.parametrize("text", ["купить хлеб", "купить 2 хлеба", "15:30 встреча", "1500такси", "0 такси"])
def test_quick_parse_op_rejects(text):
    assert quick.parse_op(text) is None


CATEGORIES = [
    {"id": 1, "kind": "expense", "name": "🍔 Еда"},
    {"id": 2, "kind": "expense", "name": "🚕 Транспорт"},
    {"id": 3, "kind": "income", "name": "💼 Зарплата"},
    {"id": 4, "kind": "income", "name": "🎁 Подарок"},
]
HISTORY = [  # свежие первыми
    {"kind": "expense", "comment": "Такси", "category_id": 2},
    {"kind": "income", "comment": "аванс", "category_id": 3},
    {"kind": "expense", "comment": "такси", "category_id": 1},
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1500 такси", ("expense", 2)),  # самая свежая операция с тем же комментарием
        ("1500 такси до работы", ("expense", 2)),  # по первому слову
        ("100000 аванс", ("income", 3)),  # прошлая операция была доходом
        ("-100000 аванс", None),  # явный минус: доходы не смотрим
        ("+300000 зарплата", ("income", 3)),  # по названию категории
        ("300000 зарплата", ("income", 3)),
        ("2000 еда шаурма", ("expense", 1)),
        ("5000 подарок маме", None),  # не доход «🎁 Подарок»: из другого вида берём только точное название
        ("5000 подарок", ("income", 4)),
        ("700 что-то новое", None),
        ("700", None),
    ],
)
def test_quick_guess_category(text, expected):
    assert quick.guess_category(quick.parse_op(text), HISTORY, CATEGORIES) == expected


def test_day_view_splits_habits_by_part_of_day():
    def habit(habit_id, title, part, mask=tu.ALL_DAYS):
        return {"id": habit_id, "title": title, "kind": "check", "days_mask": mask, "remind_time": None, "part": part}

    habits = [
        habit(1, "📚 Чтение", "evening"),
        habit(2, "💧 Вода", "morning"),
        habit(3, "🇬🇧 Английский", "day", tu.WEEKDAYS),  # TODAY — суббота, не по плану
        habit(4, "🏃 Зарядка", "morning"),
        *(habit(10 + n, f"Утро {n}", "morning") for n in range(4)),
    ]
    text, markup = habit_views.day_view(habits, {2}, {}, TODAY, TODAY, view=habit_views.MORNING)
    assert "Днём" not in text  # единственная дневная сегодня не по плану — и части нет
    assert text.index("<b>🌅 Утром</b>") < text.index("<b>🌙 Вечером</b>")
    assert "1. ✅ 💧 Вода" in text and "2. ⬜ 🏃 Зарядка" in text and "8. ⬜ 📚 Чтение" in text
    rows = [[button.text for button in row] for row in markup.inline_keyboard]
    # Номер 7 у пропущенной дневной; больше 5 номеров в ряд не ставим.
    assert rows == [["🌅", "✅ 1", "2", "3", "4", "5"], ["🌅", "6"], ["🌙", "8"]]


# Воскресенье, 8 ноября 2026, 14:30 по местному времени.
SUNDAY = datetime(2026, 11, 8, 14, 30)


def _guess(text: str):
    guess = smart_task.parse(text, SUNDAY)
    return guess and (guess.title, guess.due_date, guess.due_time, guess.repeat)


def test_smart_task_dates_and_times():
    assert _guess("завтра в 10 позвонить маме") == ("Позвонить маме", date(2026, 11, 9), time(10, 0), "none")
    assert _guess("в пятницу оплатить интернет") == ("Оплатить интернет", date(2026, 11, 13), None, "none")
    assert _guess("через 2 часа выпить таблетку") == ("Выпить таблетку", date(2026, 11, 8), time(16, 30), "none")
    assert _guess("на 15 ноября записаться к врачу") == ("Записаться к врачу", date(2026, 11, 15), None, "none")
    assert _guess("15.11 в 10.30 встреча") == ("Встреча", date(2026, 11, 15), time(10, 30), "none")
    assert _guess("встреча 25 декабря в 9 утра") == ("Встреча", date(2026, 12, 25), time(9, 0), "none")
    assert _guess("в 7 вечера ужин") == ("Ужин", date(2026, 11, 8), time(19, 0), "none")
    assert _guess("10:00 созвон") == ("Созвон", date(2026, 11, 9), time(10, 0), "none")  # 10:00 уже прошло
    assert _guess("напомни через 30 минут проверить духовку") == (
        "Проверить духовку",
        date(2026, 11, 8),
        time(15, 0),
        "none",
    )
    assert _guess("1 марта сдать отчёт")[1] == date(2027, 3, 1)  # прошедшая дата — в следующем году
    assert _guess("купить 3 мая цветы")[1] == date(2027, 5, 3)


def test_smart_task_repeats():
    assert _guess("каждый понедельник спортзал в 19") == ("Спортзал", date(2026, 11, 9), time(19, 0), "weekly")
    assert _guess("по средам английский") == ("Английский", date(2026, 11, 11), None, "weekly")
    assert _guess("каждый день зарядка") == ("Зарядка", date(2026, 11, 8), None, "daily")
    assert _guess("по будням в 20 спортзал") == ("Спортзал", date(2026, 11, 9), time(20, 0), "weekdays")  # не в вс
    assert _guess("каждое воскресенье в 10 уборка")[1] == date(2026, 11, 15)  # сегодняшние 10:00 прошли


def test_smart_task_leaves_money_and_plain_text_alone():
    for text in (
        "купить хлеб",
        "1500 такси",
        "такси 1500",
        "кофе 1.5",
        "позвонить 15",
        "завтра",
        "+250000 зарплата",
        "кофе 12.10",
        "обед 25.10",
        "сегодня такси 1500",
        "продукты на завтра 5000",
        "кино в пт 4000",
        "обед 12:30 2500",
        "подарок к 8 марта 5000",
        "такси до 10:00 1500",
        "ежемесячно интернет 5000",
        "1500 по пт",
    ):
        assert smart_task.parse(text, SUNDAY) is None, text


def test_smart_task_past_and_impossible_dates():
    assert smart_task.parse("сегодня в 9 позвонить", SUNDAY) is None  # 9:00 уже прошло
    assert smart_task.parse("оплатить 01.01.25", SUNDAY) is None
    assert smart_task.parse("29 февраля поздравить", datetime(2028, 3, 1, 10, 0)) is None  # 2029 не високосный
    assert smart_task.parse("29.02 поздравить", datetime(2028, 3, 1, 10, 0)) is None
    assert _guess("каждый день в 9 зарядка") == ("Зарядка", date(2026, 11, 9), time(9, 0), "daily")
    assert _guess("на 12.11 записаться к врачу") == ("Записаться к врачу", date(2026, 11, 12), None, "none")
