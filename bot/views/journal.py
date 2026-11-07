from collections.abc import Mapping, Sequence
from datetime import date

from aiogram.types import InlineKeyboardMarkup

from bot.callbacks import JournalCb
from bot.keyboards.common import btn, kb
from bot.services import timeutil as tu
from bot.services.texts import esc, fmt_date, plural

LIMITS = (10, 25, 50)
# Telegram не принимает сообщения длиннее 4096 символов.
MAX_TEXT = 3900


def journal_view(entries: Sequence[Mapping], limit: int, tz: str, today: date) -> tuple[str, InlineKeyboardMarkup]:
    markup = kb([btn(("• " if n == limit else "") + str(n), JournalCb(n=n)) for n in LIMITS])
    if not entries:
        text = (
            "📜 <b>Журнал</b>\n\nПока пусто. Здесь будут твои действия: новые задачи, траты, отметки привычек"
            " и всё остальное, что ты меняешь в боте."
        )
        return text, markup
    body, shown, day = "", 0, None
    for entry in entries:
        at = tu.local_now(tz, tu.from_db(entry["at"]))
        chunk = f"\n\n<b>{fmt_date(at.date(), today).capitalize()}</b>" if at.date() != day else ""
        chunk += f"\n{at:%H:%M} {esc(entry['text'])}"
        if len(body) + len(chunk) > MAX_TEXT:
            break
        body, shown, day = body + chunk, shown + 1, at.date()
    text = f"📜 <b>Журнал</b>: последние {shown} {plural(shown, ('действие', 'действия', 'действий'))}" + body
    if shown < len(entries):
        text += f"\n\n…и ещё {len(entries) - shown}: не влезли в одно сообщение."
    return text, markup
