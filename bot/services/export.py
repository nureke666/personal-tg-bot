"""Выгрузка операций в CSV (для русского Excel: UTF-8 с BOM и «;») и в XLSX."""

import csv
import io
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from aiosqlite import Row
from openpyxl import Workbook
from openpyxl.styles import Font

HEADERS = ["Дата", "Тип", "Категория", "Сумма", "Комментарий"]
KIND_LABELS = {"expense": "Расход", "income": "Доход"}


def _amount(minor: int) -> Decimal:
    return Decimal(minor) / 100


def to_csv(ops: Sequence[Row]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(HEADERS)
    for op in ops:
        writer.writerow(
            [
                date.fromisoformat(op["op_date"]).strftime("%d.%m.%Y"),
                KIND_LABELS[op["kind"]],
                op["category"],
                f"{_amount(op['amount']):.2f}".replace(".", ","),
                op["comment"] or "",
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


def to_xlsx(ops: Sequence[Row], currency: str) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Операции"
    sheet.append(HEADERS)
    totals: dict[tuple[str, str], int] = {}
    for op in ops:
        sheet.append(
            [
                date.fromisoformat(op["op_date"]),
                KIND_LABELS[op["kind"]],
                op["category"],
                _amount(op["amount"]),
                op["comment"] or "",
            ]
        )
        key = (KIND_LABELS[op["kind"]], op["category"])
        totals[key] = totals.get(key, 0) + op["amount"]
    for row in sheet.iter_rows(min_row=2):
        row[0].number_format = "DD.MM.YYYY"
        row[3].number_format = "#,##0.00"

    summary = book.create_sheet("Итоги по категориям")
    summary.append(["Тип", "Категория", f"Сумма, {currency}"])
    for (kind, category), total in sorted(totals.items(), key=lambda item: (item[0][0], -item[1])):
        summary.append([kind, category, _amount(total)])
    for row in summary.iter_rows(min_row=2):
        row[2].number_format = "#,##0.00"

    for ws, widths in ((sheet, [12, 10, 22, 14, 40]), (summary, [10, 22, 16])):
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for index, width in enumerate(widths):
            ws.column_dimensions[chr(ord("A") + index)].width = width
        ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
