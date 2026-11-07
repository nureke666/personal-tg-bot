from collections.abc import Mapping, Sequence
from datetime import date

from aiogram.types import InlineKeyboardMarkup

from bot.callbacks import CountdownCb, NavCb
from bot.keyboards.common import btn, kb, nav_row
from bot.repo.countdowns import ACTIVE, DONE, PAUSED
from bot.services.texts import clip, days, esc, fmt_date_long

PLACEHOLDER = "{дни}"
STATUS_ICONS = {ACTIVE: "", PAUSED: "⏸ ", DONE: "✔ "}
REPEAT_LABELS = {
    "once": "Нет, один раз",
    "yearly": "Каждый год · пишу каждый день",
    "yearly30": "Каждый год · пишу за 30 дней",
    "yearly7": "Каждый год · пишу за 7 дней",
}


def days_left(countdown: Mapping, today: date) -> int:
    return (date.fromisoformat(countdown["target_date"]) - today).days


def countdown_message(title: str, text: str | None, left: int) -> str:
    """Ежедневное сообщение. Если в тексте есть {дни}, он используется как готовая фраза."""
    text = esc(text) if text else ""
    if left <= 0:
        message = f"🎉 <b>{esc(title)}</b> — сегодня!"
        if text and PLACEHOLDER not in text:
            message += f"\n\n{text}"
        return message
    if PLACEHOLDER in text:
        return f"⏳ <b>{esc(title)}</b>\n\n{text.replace(PLACEHOLDER, days(left))}"
    message = f"⏳ <b>{esc(title)}</b>\nОсталось: <b>{days(left)}</b>"
    if text:
        message += f"\n\n{text}"
    return message


def _left_text(countdown: Mapping, today: date) -> str:
    left = days_left(countdown, today)
    if countdown["status"] == DONE or left < 0:
        return "завершён"
    if left == 0:
        return "сегодня!"
    return f"через {days(left)}"


def schedule_text(countdown: Mapping) -> str:
    """«каждый день в 09:00» или «в 09:00 за 7 дней до события»."""
    lead = countdown.get("lead_days")
    when = f"каждый день в {countdown['send_time']}" if lead is None else f"в {countdown['send_time']} за {days(lead)}"
    return when + (" · 🔁 каждый год" if countdown.get("yearly") else "")


def countdown_list(items: Sequence[Mapping], today: date) -> tuple[str, InlineKeyboardMarkup]:
    if items:
        text = "⏳ <b>Обратные отсчёты</b>\nКаждый день в своё время бот пишет, сколько осталось."
    else:
        text = (
            "⏳ <b>Обратные отсчёты</b>\nПока ни одного. Создай отсчёт до отпуска, экзамена или дня рождения, "
            "и бот будет каждый день писать, сколько дней осталось."
        )
    rows = [
        [
            btn(
                clip(
                    f"{STATUS_ICONS[c['status']]}{c['title']} — {_left_text(c, today)}{' 🔁' if c['yearly'] else ''}",
                    60,
                ),
                CountdownCb(act="open", id=c["id"]),
            )
        ]
        for c in items
    ]
    rows.append([btn("➕ Новый отсчёт", CountdownCb(act="new"))])
    return text, kb(*rows)


def countdown_card(countdown: Mapping, today: date) -> tuple[str, InlineKeyboardMarkup]:
    status = {ACTIVE: "активен", PAUSED: "на паузе", DONE: "завершён"}[countdown["status"]]
    lines = [
        f"⏳ <b>{esc(countdown['title'])}</b>",
        f"📅 {fmt_date_long(date.fromisoformat(countdown['target_date']))} — {_left_text(countdown, today)}",
        f"🕐 Пишу {schedule_text(dict(countdown))}",
        f"Статус: {status}",
    ]
    if countdown["text"]:
        lines.append(f"\n💬 {esc(countdown['text'])}")
    cid = countdown["id"]
    pause = (
        btn("▶ Возобновить", CountdownCb(act="resume", id=cid))
        if countdown["status"] == PAUSED
        else btn("⏸ Пауза", CountdownCb(act="pause", id=cid))
    )
    rows = [
        [
            btn("✏️ Тема", CountdownCb(act="edit", id=cid, f="title")),
            btn("📅 Дата", CountdownCb(act="edit", id=cid, f="target_date")),
        ],
        [
            btn("🕐 Время", CountdownCb(act="edit", id=cid, f="send_time")),
            btn("💬 Текст", CountdownCb(act="edit", id=cid, f="text")),
        ],
        [pause, btn("📨 Прислать сейчас", CountdownCb(act="now", id=cid))] if countdown["status"] != DONE else [],
        [
            btn("🔁 Повтор", CountdownCb(act="edit", id=cid, f="repeat")),
            btn("🗑 Удалить", CountdownCb(act="del", id=cid)),
        ],
        [btn("◀ К списку", CountdownCb(act="list"))],
    ]
    return "\n".join(lines), kb(*rows)


def repeat_kb() -> InlineKeyboardMarkup:
    rows = [[btn(label, CountdownCb(act="rep", f=key))] for key, label in REPEAT_LABELS.items()]
    return kb(*rows, nav_row())


def confirm_kb() -> InlineKeyboardMarkup:
    return kb([btn("✅ Сохранить", CountdownCb(act="save"))], [btn("✖ Отмена", NavCb(action="cancel"))])
