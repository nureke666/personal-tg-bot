from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.callbacks import NavCb

MENU_TASKS = "📋 Задачи"
MENU_COUNTDOWNS = "⏳ Отсчёты"
MENU_FINANCE = "💰 Финансы"
MENU_HABITS = "✅ Привычки"
MENU_SUMMARY = "📊 Итоги"
MENU_JOURNAL = "📜 Журнал"
MENU_SETTINGS = "⚙️ Настройки"
MENU_BUTTONS = {MENU_TASKS, MENU_COUNTDOWNS, MENU_FINANCE, MENU_HABITS, MENU_SUMMARY, MENU_JOURNAL, MENU_SETTINGS}


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_TASKS), KeyboardButton(text=MENU_COUNTDOWNS)],
            [KeyboardButton(text=MENU_FINANCE), KeyboardButton(text=MENU_HABITS)],
            [
                KeyboardButton(text=MENU_SUMMARY),
                KeyboardButton(text=MENU_JOURNAL),
                KeyboardButton(text=MENU_SETTINGS),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def btn(text: str, data: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data.pack())


def kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])


def chunks(buttons: list[InlineKeyboardButton], size: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i : i + size] for i in range(0, len(buttons), size)]


def nav_row(back: bool = True, cancel: bool = True, skip: bool = False) -> list[InlineKeyboardButton]:
    row = []
    if back:
        row.append(btn("◀ Назад", NavCb(action="back")))
    if skip:
        row.append(btn("Пропустить ▶", NavCb(action="skip")))
    if cancel:
        row.append(btn("✖ Отмена", NavCb(action="cancel")))
    return row


def confirm_kb(yes: CallbackData, no: CallbackData, yes_text: str = "Да, удалить") -> InlineKeyboardMarkup:
    return kb([btn(yes_text, yes), btn("Нет", no)])
