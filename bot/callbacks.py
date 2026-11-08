"""Все callback_data бота в одном месте: префиксы не должны пересекаться, длина ≤ 64 байт."""

from aiogram.filters.callback_data import CallbackData


class NavCb(CallbackData, prefix="nv"):
    action: str  # back / cancel / skip / home


class CalCb(CallbackData, prefix="cl"):
    action: str  # nav / pick / noop
    mode: str  # f — сегодня и позже, p — сегодня и раньше, a — любые
    y: int
    m: int
    d: int = 0


class TimeCb(CallbackData, prefix="tp"):
    action: str  # pick / none / exact / hour / presets
    kind: str  # t — задачи, h — привычки, p — без доп. кнопки
    h: int = 0
    m: int = 0


# --- задачи ---
class TaskListCb(CallbackData, prefix="tl"):
    view: str  # menu / today / tomorrow / week / overdue / nodate / done


class TaskCb(CallbackData, prefix="ta"):
    act: str  # open / done / undo / move / edit / del / delok
    id: int


class DateQuickCb(CallbackData, prefix="dq"):
    v: str  # today / tomorrow / after / cal / none


class RemindCb(CallbackData, prefix="rm"):
    off: int  # ≥0 — переключить смещение, -1 — без напоминаний, -2 — готово


class RepeatCb(CallbackData, prefix="rp"):
    v: str


class TaskFieldCb(CallbackData, prefix="tf"):
    f: str  # title / details / date / reminders / repeat / back
    id: int = 0  # 0 — черновик, иначе сохранённая задача


class SnoozeCb(CallbackData, prefix="sz"):
    act: str  # done / m10 / h1 / d1
    id: int
    d: str = ""  # дата повторения, к которому относится напоминание


# --- отсчёты ---
class CountdownCb(CallbackData, prefix="cd"):
    act: str  # list / new / open / edit / pause / resume / now / del / delok / save
    id: int = 0
    f: str = ""  # поле для edit: title / date / time / text


# --- финансы ---
class FinCb(CallbackData, prefix="fn"):
    act: str  # menu / add / report / history / limits / cats / export
    v: str = ""
    id: int = 0


class OpCb(CallbackData, prefix="op"):
    act: str  # open / date / setdate / cat / setcat / del / delok
    id: int = 0
    v: str = ""


class CatCb(CallbackData, prefix="ct"):
    act: str  # pick / list / open / rename / archive / restore / add / limit
    id: int = 0
    kind: str = ""


# --- быстрый ввод текстом ---
class QuickCb(CallbackData, prefix="qk"):
    act: str  # today / tomorrow / nodate / wizard — задача из текста; kind — доход⇄расход; task — это задача
    mid: int  # id сообщения с текстом: кнопки под старым текстом не должны сработать на новый


# --- привычки ---
class HabitCb(CallbackData, prefix="hb"):
    act: str  # day / toggle / mark / stats / manage / new / open / edit / archive / restore / del / delok
    # relapse / relapseok / unrelapse — срывы отказа; cal / calset — календарь; noop — подпись в ряду кнопок
    id: int = 0
    d: int = 0  # дата YYYYMMDD
    f: str = ""


class DaysCb(CallbackData, prefix="dy"):
    v: str  # all / weekdays / custom / done
    bit: int = -1


class PartCb(CallbackData, prefix="pt"):
    v: str  # morning / day / evening


# --- итоги ---
class SumCb(CallbackData, prefix="sm"):
    p: str  # week / month / year
    d: int = 0  # любой день периода, YYYYMMDD; 0 — текущий


# --- журнал ---
class JournalCb(CallbackData, prefix="jr"):
    n: int  # сколько последних действий показать: 10 / 25 / 50


# --- настройки ---
class SetCb(CallbackData, prefix="st"):
    act: str  # menu / tz / tzset / tzinput / cur / curset / balance / digest / checkin / toggle / time
    v: str = ""
