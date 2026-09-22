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
import pp_sheet_naming
import pp_sheetname_views_window


doc = __revit__.ActiveUIDocument.Document

TOOL_TITLE = u"Заполнить имя листа по видам"


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта."""
    pass


def fail(message):
    raise Stop(message)


# ── Разбор систем в именах видов — общий модуль lib/pp_sheet_naming ──────────
# Порядок систем в имени у этой кнопки прежний: В, Д, П, Х, К.

extract_systems_from_view_name = pp_sheet_naming.extract_systems
_system_sort_key = pp_sheet_naming.system_sort_key
_system_to_text = pp_sheet_naming.system_to_text
compress_system_ranges = pp_sheet_naming.compress_system_ranges
build_sheet_name = pp_sheet_naming.build_views_name


def get_prefix_options():
    return pp_sheet_naming.get_prefix_options()


def get_sheet_viewports(sheet):
    viewport_ids = sheet.GetAllViewports()
    viewports = []

    for viewport_id in viewport_ids:
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


def _get_selected_systems(checked_list, ordered_systems, changed_index=None, changed_state=None):
    selected = []

    for index in range(checked_list.Items.Count):
        is_checked = checked_list.GetItemChecked(index)

        if changed_index is not None and index == changed_index:
            is_checked = changed_state

        if is_checked:
            selected.append(ordered_systems[index])

    return selected


def _collect_systems_from_view_names(view_names):
    systems = []
    matched_view_names = []
    seen_view_names = set()

    for view_name in view_names:
        found_systems = extract_systems_from_view_name(view_name)
        if not found_systems:
            continue

        systems.extend(found_systems)

        if view_name not in seen_view_names:
            matched_view_names.append(view_name)
            seen_view_names.add(view_name)

    return matched_view_names, sorted(set(systems), key=_system_sort_key)


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


def show_review_dialog(view_names, systems):
    u"""Окно проверки. Возврат прежний: словарь с префиксом, системами и именем."""
    prefix_options = get_prefix_options()

    if not systems:
        return None

    result = pp_sheetname_views_window.ask(_HERE, {
        u"view_names": view_names,
        u"prefixes": prefix_options,
        u"systems": sorted(set(systems), key=_system_sort_key),
        u"system_label": _system_to_text,
        u"build_name": build_sheet_name,
    })

    if result is None:
        return None

    prefix, chosen_systems, name = result

    return {
        "confirmed": True,
        "prefix": prefix,
        "systems": chosen_systems,
        "name": name,
    }


def _show_result_report(old_name, new_name):
    report_lines = [
        u"Лист переименован.",
        u"",
        u"Старое имя:",
        old_name,
        u"",
        u"Новое имя:",
        new_name
    ]

    pp_wpf.show_report(
        u"\n".join(report_lines),
        title=u"Готово",
        subtitle=TOOL_TITLE
    )


try:
    active_view = doc.ActiveView

    if active_view is None or not isinstance(active_view, ViewSheet):
        fail(
            u"Инструмент нужно запускать только с активного листа."
        )

    sheet = active_view
    viewports = get_sheet_viewports(sheet)

    if not viewports:
        fail(
            u"На активном листе нет размещенных видов."
        )

    view_names = get_view_names_from_sheet(sheet)
    matched_view_names, systems = _collect_systems_from_view_names(view_names)

    if not systems:
        fail(
            u"В именах размещенных на листе видов не найдены обозначения"
            u" систем В, Д, П, Х или К."
        )

    dialog_result = show_review_dialog(matched_view_names, systems)

    if dialog_result is None:
        raise SystemExit

    old_sheet_name = sheet.Name
    requested_name = build_sheet_name(
        dialog_result["prefix"],
        dialog_result["systems"]
    )

    transaction = Transaction(doc, u"Заполнить имя листа по видам")
    transaction.Start()
    try:
        new_sheet_name = set_unique_sheet_name(sheet, requested_name)
        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    _show_result_report(old_sheet_name, new_sheet_name)

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
