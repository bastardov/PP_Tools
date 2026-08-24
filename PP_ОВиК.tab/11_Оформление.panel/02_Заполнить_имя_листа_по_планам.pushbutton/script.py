# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr
import re

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import BuiltInParameter, Transaction, ViewSheet
from pyrevit import forms
from pp_settings import load_settings, DEFAULT_SETTINGS

import os
import sys

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_sheetname_plans_window


doc = __revit__.ActiveUIDocument.Document

TOOL_TITLE = u"Заполнить имя листа по планам"


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта."""
    pass


def fail(message):
    raise Stop(message)
FLOOR_PATTERN = re.compile(ur"Этаж\s*(\d+)", re.IGNORECASE)
_settings = load_settings()


# ── Настройки: словари локаций и разделов ──────────────────────────────────────

def _parse_map(items):
    """Преобразует список строк вида 'ключ = значение' в список пар (ключ, значение)."""
    pairs = []

    for item in items:
        text = unicode(item)
        if u"=" not in text:
            continue

        key, _sep, value = text.partition(u"=")
        key = key.strip()
        value = value.strip()

        if key:
            pairs.append((key, value))

    return pairs


def _get_location_map():
    items = _settings.get(
        "sheet_plan_location_map",
        DEFAULT_SETTINGS.get("sheet_plan_location_map", [])
    )
    return _parse_map(items)


def _get_section_map():
    items = _settings.get(
        "sheet_plan_section_map",
        DEFAULT_SETTINGS.get("sheet_plan_section_map", [])
    )
    return _parse_map(items)


def _lookup_map(pairs, raw):
    raw_norm = unicode(raw).strip().lower()

    for key, value in pairs:
        if unicode(key).strip().lower() == raw_norm:
            return value

    return None


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


# ── Алгоритм формирования имени листа ──────────────────────────────────────────

def parse_view_name(view_name):
    """Разбирает имя вида 'X_<локация>_<раздел>'.

    Возвращает (floor_number, location_override, section_text):
    - location_override — значение из словаря локаций, если сработал ключ.
                          Ключ словаря ПРИОРИТЕТНЕЕ числового этажа
                          (напр. ключ '99' = 'План кровли' перекрывает
                          «Этаж 99»). Иначе None;
    - floor_number      — int числового этажа, если словарь НЕ сработал;
                          иначе None;
    - section_text      — раздел (по словарю, иначе как есть).
    """
    parts = [p.strip() for p in unicode(view_name).split(u"_") if p.strip()]

    if not parts:
        return None, None, None

    section_raw = parts[-1]
    section_text = _lookup_map(_get_section_map(), section_raw) or section_raw

    location_parts = parts[:-1] if len(parts) > 1 else parts

    floor_raw = None
    floor_number = None
    for part in location_parts:
        match = FLOOR_PATTERN.search(part)
        if match:
            floor_raw = match.group(1)
            floor_number = int(floor_raw)
            break

    # Приоритет словаря локаций: ключ важнее числового этажа.
    # Кандидаты для сопоставления с ключами — части имени вида, а также
    # цифры этажа как в имени ('00', '99') и в нормализованном виде ('0').
    candidates = list(location_parts)
    if floor_raw is not None:
        candidates.append(floor_raw)
        candidates.append(unicode(floor_number))

    location_map = _get_location_map()
    for candidate in candidates:
        mapped = _lookup_map(location_map, candidate)
        if mapped:
            return None, mapped, section_text

    return floor_number, None, section_text


def format_location(floor_numbers):
    unique_floors = sorted(set(floor_numbers))

    if not unique_floors:
        return None

    if len(unique_floors) == 1:
        return u"План {0} этажа".format(unique_floors[0])

    joined = u", ".join(unicode(number) for number in unique_floors)
    return u"План {0} этажей".format(joined)


def build_sheet_name(location, section):
    location_text = (location or u"").strip()
    section_text = (section or u"").strip()

    if location_text and section_text:
        return u"{0}. {1}.".format(location_text, section_text)

    if location_text:
        return u"{0}.".format(location_text)

    if section_text:
        return u"{0}.".format(section_text)

    return u""


def analyze_sheet(sheet):
    """Возвращает (old_text, proposed_name, warning) для строки таблицы."""
    view_names = get_view_names_from_sheet(sheet)

    floor_numbers = []
    location_overrides = []
    section_texts = []

    for view_name in view_names:
        floor_number, location_override, section_text = parse_view_name(view_name)

        if location_override:
            location_overrides.append(location_override)
        elif floor_number is not None:
            floor_numbers.append(floor_number)
        if section_text:
            section_texts.append(section_text)

    warnings = []

    if not view_names:
        warnings.append(u"нет видов на листе")
    elif len(view_names) > 1:
        warnings.append(u"на листе несколько видов ({0})".format(len(view_names)))

    if len(set(section_texts)) > 1:
        warnings.append(u"разные разделы в видах")

    if location_overrides:
        location = location_overrides[0]
        if len(set(location_overrides)) > 1:
            warnings.append(u"разные локации в видах")
    else:
        location = format_location(floor_numbers)

    section = section_texts[0] if section_texts else None

    if view_names and location is None:
        warnings.append(u"локация не распознана")

    proposed = build_sheet_name(location, section)
    old_text = u"; ".join(view_names)

    return old_text, proposed, u"; ".join(warnings)


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
    sheets = forms.select_sheets(
        title=TOOL_TITLE,
        button_name=u"Выбрать листы"
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
