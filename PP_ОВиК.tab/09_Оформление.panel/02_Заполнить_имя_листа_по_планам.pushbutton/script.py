# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import BuiltInParameter, Transaction, ViewSheet

import os
import sys

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_sheet_picker
import pp_sheet_naming
import pp_sheetname_plans_window


doc = __revit__.ActiveUIDocument.Document

TOOL_TITLE = u"Заполнить имя листа по планам"


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта."""
    pass


def fail(message):
    raise Stop(message)


# ── Алгоритм формирования имени листа — общий модуль lib/pp_sheet_naming ─────

def analyze_sheet(sheet):
    """Возвращает (old_text, proposed_name, warning) для строки таблицы."""
    view_names = get_view_names_from_sheet(sheet)
    proposed, warning = pp_sheet_naming.propose(view_names)
    old_text = u"; ".join(view_names)

    return old_text, proposed, warning


# ── Работа с листом ────────────────────────────────────────────────────────────

def get_sheet_viewports(sheet):
    viewports = []

    for viewport_id in sheet.GetAllViewports():
        viewport = doc.GetElement(viewport_id)
        if viewport is not None:
            viewports.append(viewport)

    return viewports


def get_view_names_from_sheet(sheet):
    view_names = []

    for viewport in get_sheet_viewports(sheet):
        view = doc.GetElement(viewport.ViewId)
        if view is None:
            continue

        try:
            view_name = view.Name
        except Exception:
            view_name = u""

        if view_name:
            view_names.append(view_name)

    return view_names


# ── Запись имени листа ─────────────────────────────────────────────────────────

def _try_set_sheet_name(sheet, candidate_name):
    sheet_name_param = sheet.get_Parameter(BuiltInParameter.SHEET_NAME)
    if sheet_name_param is None or sheet_name_param.IsReadOnly:
        raise Exception(u"Параметр \"Имя листа\" недоступен для записи.")

    try:
        sheet_name_param.Set(candidate_name)
        return True
    except Exception:
        return False


def set_unique_sheet_name(sheet, name):
    base_name = (name or u"").strip()
    if not base_name:
        raise Exception(u"Не удалось сформировать новое имя листа.")

    if _try_set_sheet_name(sheet, base_name):
        return base_name

    suffix_index = 2

    while suffix_index < 1000:
        candidate_name = u"{0} ({1})".format(base_name, suffix_index)
        if _try_set_sheet_name(sheet, candidate_name):
            return candidate_name

        suffix_index += 1

    raise Exception(u"Не удалось подобрать уникальное имя листа.")


# ── Окно проверки (таблица «было / станет») ────────────────────────────────────

def show_review_dialog(rows_data):
    u"""Окно проверки. Возврат прежний: [(лист, имя), ...] или None."""
    return pp_sheetname_plans_window.ask(_HERE, rows_data)


try:
    sheets = pp_sheet_picker.ask(
        doc,
        title=TOOL_TITLE,
        subtitle=u"Отберите листы по разделу проекта и отметьте те, "
                 u"для которых нужно собрать имя по планам.",
        button=u"Выбрать листы"
    )

    if not sheets:
        raise SystemExit

    rows_data = []

    for sheet in sheets:
        old_text, proposed, warning = analyze_sheet(sheet)

        include = bool(proposed) and u"нет видов" not in warning

        try:
            sheet_number = sheet.SheetNumber
        except Exception:
            sheet_number = u""

        sheet_label = u"{0}  {1}".format(sheet_number, sheet.Name)

        rows_data.append({
            "sheet": sheet,
            "sheet_label": sheet_label,
            "old": old_text,
            "new": proposed,
            "warn": warning,
            "include": include
        })

    items = show_review_dialog(rows_data)

    if not items:
        raise SystemExit

    applied = []

    transaction = Transaction(doc, TOOL_TITLE)
    transaction.Start()
    try:
        for sheet, new_name in items:
            final_name = set_unique_sheet_name(sheet, new_name)
            applied.append((sheet, final_name))
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    pp_wpf.show_report(
        u"Готово. Переименовано листов: {0}".format(len(applied)),
        title=u"Готово",
        subtitle=TOOL_TITLE
    )

except SystemExit:
    pass

except Stop as ex:
    pp_wpf.show_report(
        unicode(ex),
        title=u"Не выполнено",
        subtitle=TOOL_TITLE,
        is_error=True
    )

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )
