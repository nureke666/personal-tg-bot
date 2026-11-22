import html
from datetime import date

from bot.services.news import Item
from bot.services.texts import MONTHS_GENITIVE, clip, esc

EMOJI = {"top": "🔥", "show": "🚀", "repo": "🛠", "model": "🧠", "release": "📦"}
EMPTY = "📰 Новых IT-ссылок нет: источники не ответили или всё свежее я уже присылал."


def _link(url: str, text: str) -> str:
    return f'<a href="{html.escape(url)}">{esc(text)}</a>'


def digest_text(items: list[Item], day: date) -> str:
    lines = [f"📰 <b>IT за {day.day} {MONTHS_GENITIVE[day.month - 1]}</b>"]
    for item in items:
        line = f"{EMOJI[item.kind]} {_link(item.url, clip(item.title, 120))}"
        if item.note:
            line += " · " + (_link(item.note_url, item.note) if item.note_url else esc(item.note))
        lines.append(line)
    return "\n\n".join(lines)
