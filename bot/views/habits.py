import calendar
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from aiogram.types import InlineKeyboardMarkup
from aiosqlite import Connection

from bot.callbacks import CalCb, DaysCb, HabitCb, PartCb
from bot.keyboards.common import btn, chunks, kb, nav_row
from bot.repo import habits as habits_repo
from bot.repo.habits import CHECK, PARTS, QUIT
from bot.services import streaks, summary
from bot.services import timeutil as tu
from bot.services.reports import month_range, shift
from bot.services.streaks import HabitStats, QuitStats
from bot.services.texts import MONTHS, WEEKDAYS_SHORT, clip, days, esc, fmt_date, fmt_date_long, fmt_day
from bot.services.timeutil import ALL_DAYS, WEEKDAYS, day_in_mask, pack_day, unpack_day  # noqa: F401

# Где показан список на день; уходит в HabitCb.f кнопок-номеров, чтобы после отметки перерисовать то же.
MENU, EVENING, MORNING = "", "pm", "am"
CHECKIN_HEADER = "🌙 <b>Как прошёл день?</b> Отметь, что получилось:"
PART_ICON = {"morning": "🌅", "day": "☀️", "evening": "🌙"}
PART_NAME = {"morning": "Утром", "day": "Днём", "evening": "Вечером"}


def days_text(mask: int) -> str:
    if mask == ALL_DAYS:
        return "каждый день"
    if mask == WEEKDAYS:
        return "по будням"
    return ", ".join(name for bit, name in enumerate(WEEKDAYS_SHORT) if mask >> bit & 1)


def quit_counter(current: int) -> str:
    return days(current) if current else "старт сегодня"


def part_title(part: str) -> str:
    return f"{PART_ICON[part]} {PART_NAME[part]}"


def by_part(habits: Sequence[Mapping]) -> list[Mapping]:
    """Обычные привычки сверху вниз: утренние, дневные, вечерние, внутри части — по порядку создания."""
    return sorted((h for h in habits if h["kind"] == CHECK), key=lambda h: PARTS.index(h["part"]))


def numbers(habits: Sequence[Mapping]) -> dict[int, int]:
    """Номера обычных привычек: место среди активных, а не в плане на день — у привычки «по будням»
    номер в выходные пропускается, а не достаётся соседке."""
    return {h["id"]: n for n, h in enumerate(by_part(habits), start=1)}


def _header(view: str, day: date, today: date) -> str:
    if view == MORNING:
        return f"🌅 <b>Привычки · {fmt_day(day)}</b>"
    if view == EVENING:
        return CHECKIN_HEADER
    return f"✅ <b>Привычки — {fmt_date(day, today)}</b>"


def _habit_line(number: int, habit: Mapping, marked: bool, streak: int) -> str:
    line = f"{number}. {'✅' if marked else '⬜'} {esc(habit['title'])}"
    if streak:
        line += f"  🔥{streak}"
    if habit["remind_time"]:
        line += f" · ⏰ {habit['remind_time']}"
    return line


def day_view(
    habits: Sequence[Mapping],
    marked: set[int],
    counters: Mapping[int, int],
    day: date,
    today: date,
    header: str | None = None,
    view: str = MENU,
) -> tuple[str, InlineKeyboardMarkup]:
    """counters — у обычной привычки текущая серия, у отказа — дни без срыва.

    view: MENU — экран раздела, EVENING — вечерний опрос, MORNING — закреп на день. В закрепе только
    кнопки-номера: любой переход отредактировал бы закреплённое сообщение в другой экран.
    """
    number = numbers(habits)
    planned = [h for h in by_part(habits) if day_in_mask(day, h["days_mask"])]
    parts = [(part, items) for part in PARTS if (items := [h for h in planned if h["part"] == part])]
    quits = [h for h in habits if h["kind"] == QUIT] if day == today else []
    lines = [header or _header(view, day, today)]
    if not habits:
        lines.append("Пока нет ни одной привычки. Добавь первую 👇")
    elif planned:
        done = sum(h["id"] in marked for h in planned)
        hint = "🎉 Всё выполнено!" if done == len(planned) else "Нажимай номер, чтобы отметить."
        lines.append(f"Выполнено {done} из {len(planned)}. {hint}")
        for part, items in parts:
            lines += ["", f"<b>{part_title(part)}</b>"]
            lines += [_habit_line(number[h["id"]], h, h["id"] in marked, counters.get(h["id"], 0)) for h in items]
    elif not quits:
        lines.append("На этот день привычек не назначено.")
    if quits and view == MORNING:
        lines.append("")
        lines += [f"🚫 {esc(h['title'])} · {quit_counter(counters.get(h['id'], 0))}" for h in quits]
    elif quits:
        if planned:
            lines.append("")
        lines.append("🚫 Отказы считаются сами. Сорвался — открой и отметь.")
    text = "\n".join(lines)
    # Ряд кнопок на каждую часть дня, в начале ряда — её значок.
    rows = []
    for part, items in parts:
        grid = [
            btn(
                f"✅ {number[h['id']]}" if h["id"] in marked else str(number[h["id"]]),
                HabitCb(act="toggle", id=h["id"], d=pack_day(day), f=view),
            )
            for h in items
        ]
        rows += [[btn(PART_ICON[part], HabitCb(act="noop")), *chunk] for chunk in chunks(grid, 5)]
    if view == MORNING:
        return text, kb(*rows)
    for habit in quits:
        label = f"{habit['title']} · {quit_counter(counters.get(habit['id'], 0))}"
        rows.append([btn(clip(label, 60), HabitCb(act="open", id=habit["id"]))])
    if day == today:
        rows.append([btn("◀ Отметить за вчера", HabitCb(act="day", d=pack_day(today - timedelta(days=1))))])
    else:
        rows.append([btn("Сегодня ▶", HabitCb(act="day", d=pack_day(today)))])
    rows.append([btn("📈 Статистика", HabitCb(act="stats")), btn("⚙️ Управление", HabitCb(act="manage"))])
    rows.append([btn("➕ Новая привычка", HabitCb(act="new"))])
    return text, kb(*rows)


def quit_line(stats: QuitStats) -> str:
    return f"🚫 Без срывов: {days(stats.current)} · рекорд: {days(stats.best)} · срывов за 30 дней: {stats.relapses_30}"


def stats_view(items: Sequence[tuple[Mapping, HabitStats | QuitStats]]) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["📈 <b>Статистика привычек</b>"]
    if not items:
        lines.append("Пока нечего считать.")
    for habit, stats in items:
        if isinstance(stats, QuitStats):
            lines += ["", f"<b>{esc(habit['title'])}</b> · отказ", quit_line(stats)]
            continue
        week = " ".join(f"{WEEKDAYS_SHORT[day.weekday()]}{status}" for day, status in stats.week)
        lines += [
            "",
            f"<b>{esc(habit['title'])}</b> · {days_text(habit['days_mask'])}",
            f"🔥 Серия: {stats.current} · рекорд: {stats.best} · за 30 дней: {stats.percent_30}%",
            week,
        ]
    return "\n".join(lines), kb([btn("◀ К привычкам", HabitCb(act="day"))])


def manage_view(habits: Sequence[Mapping]) -> tuple[str, InlineKeyboardMarkup]:
    text = "⚙️ <b>Управление привычками</b>\nВыбери привычку, чтобы изменить её."
    rows = [
        [btn(clip(("🗄 " if h["archived"] else "") + h["title"], 60), HabitCb(act="open", id=h["id"]))] for h in habits
    ]
    rows.append([btn("➕ Новая привычка", HabitCb(act="new")), btn("◀ К привычкам", HabitCb(act="day"))])
    return text, kb(*rows)


def kind_choice() -> tuple[str, InlineKeyboardMarkup]:
    return (
        "✅ <b>Какую привычку добавим?</b>\n\n"
        "<b>Делать регулярно</b> — зарядка, чтение, вода. Отмечаешь, когда сделал.\n"
        "<b>Отказаться от чего-то</b> — сладкое, сигареты, соцсети. Отмечать ничего не надо: "
        "бот сам считает дни, а ты жмёшь «Сорвался», если что.",
        kb(
            [btn("✅ Делать регулярно", HabitCb(act="new", f=CHECK))],
            [btn("🚫 Отказаться от чего-то", HabitCb(act="new", f=QUIT))],
            [btn("◀ К привычкам", HabitCb(act="day"))],
        ),
    )


def quit_card(habit: Mapping, stats: QuitStats, today: date) -> tuple[str, InlineKeyboardMarkup]:
    hid = habit["id"]
    lines = [
        f"<b>{esc(habit['title'])}</b>",
        f"🚫 Держусь: <b>{quit_counter(stats.current)}</b> · рекорд: {days(stats.best)}",
        f"📅 С {fmt_date_long(date.fromisoformat(habit['since']))} · срывов за 30 дней: {stats.relapses_30}",
    ]
    if habit["archived"]:
        lines.append("🗄 В архиве: не показывается в списке.")
    undo = []
    if stats.last and stats.last >= today - timedelta(days=1):
        undo = [btn(f"↩️ Отменить срыв ({fmt_date(stats.last, today)})", HabitCb(act="unrelapse", id=hid))]
    archive = (
        btn("👁 Вернуть из архива", HabitCb(act="restore", id=hid))
        if habit["archived"]
        else btn("🗄 В архив", HabitCb(act="archive", id=hid))
    )
    return "\n".join(lines), kb(
        [btn("💥 Сорвался", HabitCb(act="relapse", id=hid)), btn("📅 Календарь", HabitCb(act="cal", id=hid))],
        undo,
        [
            btn("✏️ Название", HabitCb(act="edit", id=hid, f="title")),
            btn("📅 С какого дня", HabitCb(act="edit", id=hid, f="since")),
        ],
        [archive, btn("🗑 Удалить", HabitCb(act="del", id=hid))],
        [btn("◀ К привычкам", HabitCb(act="day")), btn("⚙️ Управление", HabitCb(act="manage"))],
    )


def relapse_confirm(habit: Mapping, today: date) -> tuple[str, InlineKeyboardMarkup]:
    hid, yesterday = habit["id"], today - timedelta(days=1)
    when = [btn("Да, сегодня", HabitCb(act="relapseok", id=hid, d=pack_day(today)))]
    if date.fromisoformat(habit["since"]) <= yesterday:
        when.append(btn("Это было вчера", HabitCb(act="relapseok", id=hid, d=pack_day(yesterday))))
    return (
        f"💥 Записать срыв: <b>{esc(habit['title'])}</b>?\nСчётчик начнётся заново, рекорд и история останутся.",
        kb(when, [btn("Нет, держусь 💪", HabitCb(act="open", id=hid))]),
    )


def habit_card(habit: Mapping) -> tuple[str, InlineKeyboardMarkup]:
    archived = bool(habit["archived"])
    lines = [
        f"<b>{esc(habit['title'])}</b>",
        f"📅 {days_text(habit['days_mask'])}",
        part_title(habit["part"]),
        f"⏰ {'напоминание в ' + habit['remind_time'] if habit['remind_time'] else 'без напоминания'}",
    ]
    if archived:
        lines.append("🗄 В архиве: не показывается и не напоминает.")
    hid = habit["id"]
    archive = (
        btn("👁 Вернуть из архива", HabitCb(act="restore", id=hid))
        if archived
        else btn("🗄 В архив", HabitCb(act="archive", id=hid))
    )
    return "\n".join(lines), kb(
        [
            btn("✏️ Название", HabitCb(act="edit", id=hid, f="title")),
            btn("📅 Дни", HabitCb(act="edit", id=hid, f="days")),
        ],
        [
            btn("🕐 Часть дня", HabitCb(act="edit", id=hid, f="part")),
            btn("⏰ Напоминание", HabitCb(act="edit", id=hid, f="time")),
        ],
        [btn("📅 Календарь", HabitCb(act="cal", id=hid))],
        [archive, btn("🗑 Удалить", HabitCb(act="del", id=hid))],
        [btn("◀ Назад", HabitCb(act="manage"))],
    )


def part_kb(current: str | None) -> InlineKeyboardMarkup:
    return kb(
        [btn(("✅ " if part == current else "") + part_title(part), PartCb(v=part)) for part in PARTS],
        nav_row(),
    )


def days_kb(mask: int, custom: bool) -> InlineKeyboardMarkup:
    if not custom:
        return kb(
            [btn("Каждый день", DaysCb(v="all")), btn("По будням", DaysCb(v="weekdays"))],
            [btn("Выбрать дни недели", DaysCb(v="custom"))],
            nav_row(),
        )
    toggles = [
        btn(("✅" if mask >> bit & 1 else "") + name, DaysCb(v="custom", bit=bit))
        for bit, name in enumerate(WEEKDAYS_SHORT)
    ]
    return kb(toggles[:4], toggles[4:], [btn("Готово ▶", DaysCb(v="done"))], nav_row())


def reminder_message(habit: Mapping, today: date) -> tuple[str, InlineKeyboardMarkup]:
    return (
        f"⏰ Время для привычки: <b>{esc(habit['title'])}</b>",
        kb([btn("✅ Сделано", HabitCb(act="mark", id=habit["id"], d=pack_day(today)))]),
    )


def created_day(habit: Mapping, tz: str) -> date:
    return tu.local_today(tz, tu.from_db(habit["created_at"]))


def habit_start(habit: Mapping, tz: str) -> date:
    """С какого дня у привычки есть история: у отказа — день старта, у обычной — день создания."""
    return date.fromisoformat(habit["since"]) if habit["kind"] == QUIT else created_day(habit, tz)


def _calendar_label(habit: Mapping, day: date, marks: set[date], today: date) -> str:
    if habit["kind"] == QUIT:
        return "💥" if day in marks else "✅"
    if day in marks:
        return "✅"
    if not day_in_mask(day, habit["days_mask"]):
        return "·"
    return "⬜" if day == today else "❌"


def calendar_view(
    habit: Mapping, marks: set[date], month: date, today: date, tz: str
) -> tuple[str, InlineKeyboardMarkup]:
    """Месяц привычки. Нажатие на день ставит или снимает отметку, у отказа — срыв."""
    hid, quit = habit["id"], habit["kind"] == QUIT
    start, (first, last) = habit_start(habit, tz), month_range(month)
    lines = [f"📅 <b>{esc(habit['title'])}</b> — {MONTHS[month.month - 1]} {month.year}"]
    line = summary.habit_line(dict(habit) | {"archived": 0}, marks, first, last, today, tz)
    if line and quit:
        lines.append(f"Срывов за месяц: {line.relapses}" if line.relapses else "За месяц без срывов 💪")
    elif line and line.planned:
        lines.append(f"Выполнено {line.done} из {line.planned} ({round(line.done * 100 / line.planned)}%)")
    lines.append(
        "✅ держался · 💥 срыв. Нажми на день, чтобы отметить или убрать срыв."
        if quit
        else "✅ сделано · ❌ пропуск · · не по плану. Нажми на день, чтобы поставить или снять отметку."
    )

    noop = CalCb(action="noop", mode="a", y=month.year, m=month.month)
    earlier, later = shift("month", month, -1), shift("month", month, 1)
    nav = [
        btn("◀", HabitCb(act="cal", id=hid, d=tu.pack_day(earlier))) if first > start else btn(" ", noop),
        btn(f"{MONTHS[month.month - 1]} {month.year}", noop),
        btn("▶", HabitCb(act="cal", id=hid, d=tu.pack_day(later))) if later <= today else btn(" ", noop),
    ]
    rows = [nav, [btn(name, noop) for name in WEEKDAYS_SHORT]]
    for week in calendar.monthcalendar(month.year, month.month):
        row = []
        for number in week:
            day = date(month.year, month.month, number) if number else None
            if day is None:
                row.append(btn(" ", noop))
            elif day < start or day > today:
                row.append(btn(str(number), noop))
            else:
                label = _calendar_label(habit, day, marks, today)
                row.append(btn(label, HabitCb(act="calset", id=hid, d=tu.pack_day(day))))
        rows.append(row)
    rows.append([btn("◀ К привычке", HabitCb(act="open", id=hid))])
    return "\n".join(lines), kb(*rows)


async def habit_stats(db: Connection, habit: Mapping, tz: str, today: date) -> HabitStats | QuitStats:
    marks = await habits_repo.marks(db, habit["id"])
    if habit["kind"] == QUIT:
        return streaks.quit_stats(marks, date.fromisoformat(habit["since"]), today)
    return streaks.stats(marks, habit["days_mask"], created_day(habit, tz), today)


async def day_screen(
    db: Connection, user_id: int, tz: str, day: date, today: date, header: str | None = None, view: str = MENU
) -> tuple[str, InlineKeyboardMarkup]:
    habits = await habits_repo.list_active(db, user_id)
    marked = await habits_repo.marked_ids(db, user_id, day)
    counters = {h["id"]: (await habit_stats(db, h, tz, today)).current for h in habits}
    return day_view(habits, marked, counters, day, today, header, view)
