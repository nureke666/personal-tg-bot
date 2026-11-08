from collections.abc import Mapping

from aiogram.types import InlineKeyboardMarkup

from bot.callbacks import CountdownCb, FinCb, SetCb
from bot.keyboards.common import btn, chunks, kb
from bot.repo.settings import SCHEDULE_DAYS
from bot.services.texts import fmt_money
from bot.services.timeutil import tz_offset_label

TIMEZONES = {
    "Asia/Almaty": "Алматы, Астана",
    "Asia/Tashkent": "Ташкент",
    "Asia/Bishkek": "Бишкек",
    "Europe/Moscow": "Москва",
    "Asia/Dubai": "Дубай",
    "Europe/Istanbul": "Стамбул",
    "Europe/Berlin": "Берлин",
    "Asia/Seoul": "Сеул",
}
CURRENCIES = ["₸", "₽", "$", "€", "сом", "сўм"]
SCHEDULES = {
    "morning": (
        "🌅 Привычки на день",
        "Утром присылаю список привычек на сегодня и закрепляю его в чате, вчерашний открепляю. "
        "Отмечай прямо в нём, кнопками с номерами. Если на день привычек нет, не присылаю.",
    ),
    "digest": ("☀️ Утренняя сводка", "Утром присылаю задачи на день и просроченные."),
    "checkin": ("🌙 Вечерний опрос привычек", "Вечером спрашиваю, какие привычки ты сегодня выполнил."),
    "weekly": (
        "📊 Итоги недели",
        "В воскресенье вечером присылаю итоги недели: задачи, привычки, деньги. Пустую неделю не присылаю.",
    ),
    "backup": (
        "💾 Бэкап базы",
        "Каждую ночь без звука присылаю файл базы. Если с сервером что-то случится, из него всё восстановится.",
    ),
}
SEND_NOW = {"backup": "backupnow", "morning": "morningnow"}


def tz_title(tz: str) -> str:
    return f"{TIMEZONES.get(tz, tz)} ({tz_offset_label(tz)})"


def _when(settings: Mapping, key: str) -> str:
    return ("вс " if key in SCHEDULE_DAYS else "") + settings[f"{key}_time"]


def _schedule_label(settings: Mapping, key: str) -> str:
    title = SCHEDULES[key][0]
    if settings[f"{key}_on"]:
        return f"{title}: {_when(settings, key)}"
    return f"{title}: выкл"


def settings_view(settings: Mapping) -> tuple[str, InlineKeyboardMarkup]:
    currency = settings["currency"]
    text = "\n".join(
        [
            "⚙️ <b>Настройки</b>",
            f"🌍 Часовой пояс: {tz_title(settings['tz'])}",
            f"💱 Валюта: {currency}",
            f"💼 Начальный баланс: {fmt_money(settings['start_balance'], currency)}",
            *[_schedule_label(settings, key) for key in SCHEDULES],
        ]
    )
    markup = kb(
        [btn("🌍 Часовой пояс", SetCb(act="tz")), btn("💱 Валюта", SetCb(act="cur"))],
        [btn("💼 Начальный баланс", SetCb(act="balance"))],
        *[[btn(_schedule_label(settings, key), SetCb(act="sched", v=key))] for key in SCHEDULES],
        [btn("⏳ Обратные отсчёты", CountdownCb(act="list"))],
        [btn("🎯 Лимиты", FinCb(act="limits")), btn("🗂 Категории", FinCb(act="cats", v="expense"))],
    )
    return text, markup


def tz_kb() -> InlineKeyboardMarkup:
    rows = chunks([btn(tz_title(tz), SetCb(act="tzset", v=tz)) for tz in TIMEZONES], 2)
    rows.append([btn("⌨️ Ввести вручную", SetCb(act="tzinput"))])
    rows.append([btn("◀ Настройки", SetCb(act="menu"))])
    return kb(*rows)


def currency_kb() -> InlineKeyboardMarkup:
    rows = chunks([btn(c, SetCb(act="curset", v=c)) for c in CURRENCIES], 3)
    rows.append([btn("◀ Настройки", SetCb(act="menu"))])
    return kb(*rows)


def schedule_view(settings: Mapping, key: str) -> tuple[str, InlineKeyboardMarkup]:
    title, description = SCHEDULES[key]
    on = bool(settings[f"{key}_on"])
    text = f"<b>{title}</b>\n{description}\n\nСейчас: {'включено, ' + _when(settings, key) if on else 'выключено'}"
    return text, kb(
        [
            btn("🔕 Выключить" if on else "🔔 Включить", SetCb(act="toggle", v=key)),
            btn("🕐 Изменить время", SetCb(act="time", v=key)),
        ],
        [btn("📤 Прислать сейчас", SetCb(act=SEND_NOW[key]))] if key in SEND_NOW else [],
        [btn("◀ Настройки", SetCb(act="menu"))],
    )
