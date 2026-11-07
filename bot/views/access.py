from collections.abc import Mapping, Sequence

from aiogram.types import InlineKeyboardMarkup

from bot.callbacks import AccessCb
from bot.keyboards.common import btn, kb
from bot.services.texts import clip, esc

USAGE = (
    "<code>/allow id</code> — открыть доступ\n"
    "<code>/deny id</code> — закрыть доступ\n"
    "<code>/users</code> — кто пользуется ботом"
)


def person(name: str | None, username: str | None, user_id: int) -> str:
    """«Имя @ник (id 123)» для HTML-сообщений."""
    parts = [f"<b>{esc(name)}</b>" if name else "без имени"]
    if username:
        parts.append(f"@{esc(username)}")
    parts.append(f"(id <code>{user_id}</code>)")
    return " ".join(parts)


def _label(row: Mapping) -> str:
    return row["name"] or (f"@{row['username']}" if row["username"] else str(row["user_id"]))


def request_message(user_id: int, name: str, username: str | None) -> tuple[str, InlineKeyboardMarkup]:
    text = f"👤 {person(name, username, user_id)} просит доступ к боту."
    allow = btn("✅ Разрешить", AccessCb(act="allow", id=user_id))
    return text, kb([allow, btn("🚫 Отклонить", AccessCb(act="reject", id=user_id))])


def users_view(allowed: Sequence[Mapping], requests: Sequence[Mapping]) -> tuple[str, InlineKeyboardMarkup]:
    lines = ["👥 <b>Доступ к боту</b>", "У каждого свои задачи, отсчёты, финансы и привычки.", ""]
    if allowed:
        lines.append(f"<b>Есть доступ ({len(allowed)}):</b>")
        lines += [f"• {person(r['name'], r['username'], r['user_id'])}" for r in allowed]
    else:
        lines.append("Кроме тебя, доступа ни у кого нет.")
    if requests:
        lines += ["", f"<b>Ждут ответа ({len(requests)}):</b>"]
        lines += [f"• {person(r['name'], r['username'], r['user_id'])}" for r in requests]
    lines += ["", USAGE]

    rows = [[btn(clip(f"🚫 Закрыть: {_label(r)}", 60), AccessCb(act="deny", id=r["user_id"]))] for r in allowed]
    for r in requests:
        rows.append(
            [
                btn(clip(f"✅ {_label(r)}", 40), AccessCb(act="allow", id=r["user_id"])),
                btn("🚫 Отклонить", AccessCb(act="reject", id=r["user_id"])),
            ]
        )
    return "\n".join(lines), kb(*rows)
