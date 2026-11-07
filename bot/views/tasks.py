from collections.abc import Mapping, Sequence
from datetime import date, datetime

from aiogram.types import InlineKeyboardMarkup
from aiosqlite import Connection

from bot.callbacks import (
    DateQuickCb,
    NavCb,
    QuickCb,
    RemindCb,
    RepeatCb,
    SnoozeCb,
    TaskCb,
    TaskFieldCb,
    TaskListCb,
)
from bot.keyboards.common import btn, kb, nav_row
from bot.repo import tasks as tasks_repo
from bot.repo.tasks import DONE
from bot.services import timeutil as tu
from bot.services.texts import clip, esc, fmt_date

REPEAT_LABELS = {
    tu.REPEAT_NONE: "не повторять",
    tu.REPEAT_DAILY: "каждый день",
    tu.REPEAT_WEEKDAYS: "по будням",
    tu.REPEAT_WEEKLY: "каждую неделю",
    tu.REPEAT_MONTHLY: "каждый месяц",
}
OFFSET_LABELS = {0: "в момент", 15: "за 15 мин", 60: "за 1 ч", 1440: "за 1 день"}
VIEW_TITLES = {
    "today": "Сегодня",
    "tomorrow": "Завтра",
    "week": "Ближайшие 7 дней",
    "overdue": "Просроченные",
    "nodate": "Без даты",
    "done": "Выполненные",
}


def when_text(due_date: str | None, due_time: str | None, today: date) -> str:
    if not due_date:
        return "без даты"
    return f"{fmt_date(date.fromisoformat(due_date), today)}, {due_time or 'весь день'}"


def offsets_text(offsets: Sequence[int]) -> str:
    return ", ".join(OFFSET_LABELS.get(o, f"за {o} мин") for o in sorted(offsets)) or "без напоминаний"


def task_text(task: Mapping, offsets: Sequence[int], today: date) -> str:
    lines = [f"📌 <b>{esc(task['title'])}</b>"]
    if task.get("details"):
        lines.append(esc(task["details"]))
    lines.append("")
    lines.append(f"📅 {when_text(task.get('due_date'), task.get('due_time'), today)}")
    if task.get("due_date"):
        lines.append(f"🔔 {offsets_text(offsets)}")
        if task.get("repeat", tu.REPEAT_NONE) != tu.REPEAT_NONE:
            lines.append(f"🔁 {REPEAT_LABELS[task['repeat']]} · без отметки сам перейдёт на следующий раз")
    if task.get("status") == DONE:
        lines.append("✅ выполнено")
    return "\n".join(lines)


def task_card_kb(task: Mapping) -> InlineKeyboardMarkup:
    task_id = task["id"]
    back = [btn("◀ К задачам", TaskListCb(view="menu"))]
    if task["status"] == DONE:
        return kb(
            [
                btn("↩️ Вернуть в работу", TaskCb(act="undo", id=task_id)),
                btn("🗑 Удалить", TaskCb(act="del", id=task_id)),
            ],
            back,
        )
    return kb(
        [btn("✅ Выполнено", TaskCb(act="done", id=task_id)), btn("⏰ Перенести", TaskCb(act="move", id=task_id))],
        [btn("✏️ Изменить", TaskCb(act="edit", id=task_id)), btn("🗑 Удалить", TaskCb(act="del", id=task_id))],
        back,
    )


def tasks_menu(counts: Mapping[str, int]) -> tuple[str, InlineKeyboardMarkup]:
    def label(title: str, key: str) -> str:
        return f"{title} ({counts[key]})" if counts.get(key) else title

    text = "📋 <b>Задачи</b>"
    summary = []
    if counts["today"]:
        summary.append(f"сегодня: {counts['today']}")
    if counts["overdue"]:
        summary.append(f"просрочено: {counts['overdue']}")
    text += "\n" + (", ".join(summary).capitalize() if summary else "На сегодня ничего не запланировано.")
    markup = kb(
        [btn("➕ Новая задача", TaskListCb(view="new"))],
        [
            btn(label("Сегодня", "today"), TaskListCb(view="today")),
            btn(label("Завтра", "tomorrow"), TaskListCb(view="tomorrow")),
        ],
        [btn("7 дней", TaskListCb(view="week")), btn(label("Просроченные", "overdue"), TaskListCb(view="overdue"))],
        [btn(label("Без даты", "nodate"), TaskListCb(view="nodate")), btn("Выполненные", TaskListCb(view="done"))],
    )
    return text, markup


def _list_label(task: Mapping, view: str, today: date) -> str:
    if view in ("today", "tomorrow"):
        prefix = task["due_time"] or "весь день"
    elif task["due_date"]:
        prefix = when_text(task["due_date"], task["due_time"], today)
    else:
        prefix = ""
    repeat = " 🔁" if task["repeat"] != tu.REPEAT_NONE else ""
    return f"{prefix} · {task['title']}{repeat}" if prefix else f"{task['title']}{repeat}"


def task_list(view: str, tasks: Sequence[Mapping], today: date) -> tuple[str, InlineKeyboardMarkup]:
    title = f"📋 <b>{VIEW_TITLES[view]}</b>"
    if not tasks:
        text = f"{title}\nЗдесь пусто 🎉"
    else:
        text = f"{title}\nВсего: {len(tasks)}. Нажми на задачу, чтобы открыть."
    rows = [[btn(clip(_list_label(task, view, today), 60), TaskCb(act="open", id=task["id"]))] for task in tasks]
    rows.append([btn("➕ Новая", TaskListCb(view="new")), btn("◀ К задачам", TaskListCb(view="menu"))])
    return text, kb(*rows)


def quick_offer(text: str, mid: int) -> tuple[str, InlineKeyboardMarkup]:
    """Ответ на текст без суммы: сохранить его задачей."""
    return (
        f"📋 Сохранить как задачу?\n<b>{esc(clip(text, 200))}</b>\n\n"
        "<i>Если это трата — добавь сумму: «1500 такси».</i>",
        kb(
            [btn("На сегодня", QuickCb(act="today", mid=mid)), btn("На завтра", QuickCb(act="tomorrow", mid=mid))],
            [btn("Без даты", QuickCb(act="nodate", mid=mid)), btn("✏️ Дата и время…", QuickCb(act="wizard", mid=mid))],
            [btn("✖ Не надо", NavCb(action="cancel"))],
        ),
    )


# --- шаги мастера ---
def date_quick_kb() -> InlineKeyboardMarkup:
    return kb(
        [
            btn("Сегодня", DateQuickCb(v="today")),
            btn("Завтра", DateQuickCb(v="tomorrow")),
            btn("Послезавтра", DateQuickCb(v="after")),
        ],
        [btn("📅 Выбрать в календаре", DateQuickCb(v="cal"))],
        [btn("Без даты", DateQuickCb(v="none"))],
        nav_row(),
    )


def reminders_kb(selected: Sequence[int]) -> InlineKeyboardMarkup:
    toggles = [
        btn(("✅ " if off in selected else "") + label, RemindCb(off=off)) for off, label in OFFSET_LABELS.items()
    ]
    return kb(
        toggles[:2],
        toggles[2:],
        [btn("🔕 Без напоминаний", RemindCb(off=-1)), btn("Готово ▶", RemindCb(off=-2))],
        nav_row(),
    )


def repeat_kb() -> InlineKeyboardMarkup:
    items = [btn(label.capitalize(), RepeatCb(v=key)) for key, label in REPEAT_LABELS.items()]
    return kb(items[:1], items[1:3], items[3:], nav_row())


def fields_kb(task_id: int, has_date: bool) -> InlineKeyboardMarkup:
    rows = [
        [btn("Название", TaskFieldCb(f="title", id=task_id)), btn("Подробности", TaskFieldCb(f="details", id=task_id))],
        [btn("Дата и время", TaskFieldCb(f="date", id=task_id))],
    ]
    if has_date:
        rows.append(
            [
                btn("Напоминания", TaskFieldCb(f="reminders", id=task_id)),
                btn("Повтор", TaskFieldCb(f="repeat", id=task_id)),
            ]
        )
    rows.append([btn("◀ Назад", TaskFieldCb(f="back", id=task_id))])
    return kb(*rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return kb(
        [btn("✅ Сохранить", NavCb(action="save"))],
        [btn("✏️ Изменить", TaskFieldCb(f="menu")), btn("✖ Отмена", NavCb(action="cancel"))],
    )


# --- сообщения планировщика ---
def reminder_message(task: Mapping, today: date, late: bool) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"🔔 <b>{esc(task['title'])}</b>"]
    if task["details"]:
        lines.append(esc(task["details"]))
    lines.append(f"📅 {when_text(task['due_date'], task['due_time'], today)}")
    if late:
        lines.append("<i>С опозданием: бот был выключен.</i>")
    task_id, day = task["id"], task["due_date"]
    markup = kb(
        [btn("✅ Сделано", SnoozeCb(act="done", id=task_id, d=day))],
        [
            btn("⏰ +10 мин", SnoozeCb(act="m10", id=task_id, d=day)),
            btn("⏰ +1 час", SnoozeCb(act="h1", id=task_id, d=day)),
            btn("⏰ Завтра", SnoozeCb(act="d1", id=task_id, d=day)),
        ],
    )
    return "\n".join(lines), markup


def digest_message(today_tasks: Sequence[Mapping], overdue: Sequence[Mapping], today: date) -> str | None:
    if not today_tasks and not overdue:
        return None
    lines = ["☀️ <b>Доброе утро! План на сегодня:</b>"]
    for task in today_tasks:
        lines.append(f"• {task['due_time'] or 'весь день'} — {esc(task['title'])}")
    if not today_tasks:
        lines.append("На сегодня задач нет.")
    if overdue:
        lines.append("")
        lines.append(f"⚠️ <b>Просрочено: {len(overdue)}</b>")
        for task in overdue[:10]:
            lines.append(f"• {when_text(task['due_date'], task['due_time'], today)} — {esc(task['title'])}")
    return "\n".join(lines)


async def digest_screen(db: Connection, user_id: int, tz: str, now: datetime) -> str | None:
    local = tu.local_now(tz, now)
    today, now_hm = local.date(), local.strftime("%H:%M")
    today_tasks = await tasks_repo.list_view(db, user_id, "today", today, now_hm)
    overdue = await tasks_repo.list_view(db, user_id, "overdue", today, now_hm)
    earlier = [t for t in overdue if t["due_date"] < today.isoformat()]
    return digest_message(today_tasks, earlier, today)
