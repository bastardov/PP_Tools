# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr
import re

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import BuiltInParameter, Transaction, ViewSheet
from pp_settings import load_settings, DEFAULT_SETTINGS

import os
import sys

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_sheetname_views_window


doc = __revit__.ActiveUIDocument.Document

TOOL_TITLE = u"Заполнить имя листа по видам"


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта."""
    pass


def fail(message):
    raise Stop(message)
SYSTEM_PATTERN = re.compile(
    ur"(?<![A-Za-zА-Яа-я0-9])([A-Za-zА-Яа-я]{1,3})\s*(\d+)(?:\.(\d+))?"
    ur"(?![A-Za-zА-Яа-я0-9.])"
)
PREFIX_SORT_ORDER = {
    u"В": 0,
    u"Д": 1,
    u"П": 2,
    u"Х": 3,
    u"К": 4
}
_settings = load_settings()


def get_sheet_viewports(sheet):
    viewport_ids = sheet.GetAllViewports()
    viewports = []

    for viewport_id in viewport_ids:
        viewport = doc.GetElement(viewport_id)
        if viewport is not None:
            viewports.append(viewport)

    return viewports


def get_prefix_options():
    prefixes = _settings.get(
        "sheet_name_prefixes",
        DEFAULT_SETTINGS.get("sheet_name_prefixes", [])
    )

    result = []

    for prefix in prefixes:
        prefix_text = unicode(prefix).strip()
        if prefix_text and prefix_text not in result:
            result.append(prefix_text)

    if not result:
        for prefix in DEFAULT_SETTINGS.get("sheet_name_prefixes", []):
            prefix_text = unicode(prefix).strip()
            if prefix_text and prefix_text not in result:
                result.append(prefix_text)

    return result


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


def extract_systems_from_view_name(view_name):
    systems = []
    seen = set()

    if not view_name:
        return systems

    for match in SYSTEM_PATTERN.finditer(view_name):
        prefix = _normalize_system_prefix(match.group(1))
        if not _is_supported_system_prefix(prefix):
            continue

        number = int(match.group(2))
        sub_text = match.group(3)
        sub_number = int(sub_text) if sub_text else None
        key = (prefix, number, sub_number)

        if key in seen:
            continue

        seen.add(key)
        systems.append(key)

    return systems


def _normalize_system_prefix(raw_prefix):
    prefix_text = unicode(raw_prefix).strip().upper()
    prefix_text = prefix_text.replace(u" ", u"")
    prefix_text = prefix_text.replace(u"Ё", u"Е")
    return prefix_text


def _is_supported_system_prefix(prefix):
    if not prefix:
        return False

    first_char = prefix[0]
    return first_char in PREFIX_SORT_ORDER


def _system_sort_key(system_item):
    prefix, number, sub_number = system_item
    prefix_rank = PREFIX_SORT_ORDER.get(prefix, 999)
    has_sub = 1 if sub_number is not None else 0
    return (prefix_rank, prefix, number, has_sub, sub_number or 0)


def compress_system_ranges(systems):
    unique_systems = sorted(set(systems), key=_system_sort_key)
    if not unique_systems:
        return u""

    grouped_items = {}
    grouped_order = []

    for prefix, number, sub_number in unique_systems:
        if prefix not in grouped_items:
            grouped_items[prefix] = []
            grouped_order.append(prefix)

        grouped_items[prefix].append((number, sub_number))

    parts = []

    for prefix in grouped_order:
        items = sorted(set(grouped_items[prefix]), key=_item_sort_key)
        parts.extend(_compress_prefix_items(prefix, items))

    return u", ".join(parts)


def _item_sort_key(item):
    number, sub_number = item
    has_sub = 1 if sub_number is not None else 0
    return (number, has_sub, sub_number or 0)


def _compress_prefix_items(prefix, items):
    parts = []
    index = 0
    item_count = len(items)

    while index < item_count:
        range_start = items[index]
        range_end = range_start
        next_index = index + 1

        while next_index < item_count and _is_next_in_sequence(range_end, items[next_index]):
            range_end = items[next_index]
            next_index += 1

        parts.append(_format_item_range(prefix, range_start, range_end))
        index = next_index

    return parts


def _is_next_in_sequence(previous_item, current_item):
    previous_number, previous_sub = previous_item
    current_number, current_sub = current_item

    if previous_sub is None and current_sub is None:
        return current_number == previous_number + 1

    if previous_sub is not None and current_sub is not None:
        return (
            current_number == previous_number and
            current_sub == previous_sub + 1
        )

    return False


def _format_item_range(prefix, range_start, range_end):
    if range_start == range_end:
        return u"{0}{1}".format(prefix, _format_system_number(range_start))

    return u"{0}{1}-{0}{2}".format(
        prefix,
        _format_system_number(range_start),
        _format_system_number(range_end)
    )


def _format_system_number(item):
    number, sub_number = item
    if sub_number is None:
        return u"{0}".format(number)

    return u"{0}.{1}".format(number, sub_number)


def build_sheet_name(prefix, systems):
    prefix_text = (prefix or u"").strip()
    compressed_systems = compress_system_ranges(systems)

    if prefix_text and compressed_systems:
        return u"{0} {1}".format(prefix_text, compressed_systems)

    if prefix_text:
        return prefix_text

    return compressed_systems


def _system_to_text(system_item):
    prefix, number, sub_number = system_item
    return u"{0}{1}".format(prefix, _format_system_number((number, sub_number)))


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
