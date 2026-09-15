# -*- coding: utf-8 -*-
"""Общие хелперы «Проверки Модели» — то, чем пользуются 2 и больше проверок.

Имена перенесены из pp_model_checks.py как есть, даже если исторически
названы по одной проверке (_duplicate_label, _smoke_label, _pipe_label…).
Хелпер, нужный одной проверке, живёт в её файле check_*.py.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    Element,
    StorageType,
)

from model_checks.core import (
    FEET_TO_MM,
)


# Категории с осевой геометрией (LocationCurve) — трубы/воздуховоды, гибкие.
CURVE_CATEGORY_OPTIONS = [
    (u"OST_PipeCurves", u"Трубы", BuiltInCategory.OST_PipeCurves),
    (u"OST_DuctCurves", u"Воздуховоды", BuiltInCategory.OST_DuctCurves),
    (u"OST_FlexPipeCurves", u"Гибкие трубы", BuiltInCategory.OST_FlexPipeCurves),
    (u"OST_FlexDuctCurves", u"Гибкие воздуховоды", BuiltInCategory.OST_FlexDuctCurves),
]


def _parse_masks(raw_text):
    result = []
    seen = set()
    cleaned = unicode(raw_text or u"")
    for part in cleaned.replace(u",", u";").split(u";"):
        token = part.strip().lower()
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _matches_any(name, masks):
    low = (name or u"").lower()
    for mask in masks:
        if mask in low:
            return True
    return False


def _pipe_system_name(pipe):
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
        if p is not None and p.HasValue:
            return (p.AsString() or u"").strip()
    except:
        pass
    return u""


def _pipe_size_text(pipe):
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
        if p is not None and p.HasValue:
            text = p.AsValueString()
            if text:
                return text
    except:
        pass
    return u""


def _pipe_label(pipe, system_name):
    parts = []
    if system_name:
        parts.append(u"система «{0}»".format(system_name))
    size = _pipe_size_text(pipe)
    if size:
        parts.append(size)
    try:
        parts.append(u"id {0}".format(pipe.Id.IntegerValue))
    except:
        pass
    return u", ".join(parts) if parts else u"труба"


def _parse_float(raw_text, default_value):
    try:
        text = unicode(raw_text or u"").strip().replace(u",", u".")
        if not text:
            return default_value
        return float(text)
    except:
        return default_value


def _element_size_text(element):
    try:
        p = element.get_Parameter(BuiltInParameter.RBS_CALCULATED_SIZE)
        if p is not None and p.HasValue:
            return (p.AsString() or u"").strip()
    except:
        pass
    return u""


def _element_label(element, system_name):
    parts = []
    try:
        if element.Category is not None:
            parts.append(element.Category.Name)
    except:
        pass
    if system_name:
        parts.append(u"система «{0}»".format(system_name))
    size = _element_size_text(element)
    if size:
        parts.append(size)
    try:
        parts.append(u"id {0}".format(element.Id.IntegerValue))
    except:
        pass
    return u", ".join(parts) if parts else u"элемент"


def _parse_category_keys(raw_text):
    result = []
    seen = set()
    cleaned = unicode(raw_text or u"").replace(u";", u",").replace(u"/", u",")
    for part in cleaned.split(u","):
        token = part.strip()
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _is_yes(raw_text):
    return unicode(raw_text or u"").strip().lower() in (
        u"да", u"yes", u"y", u"1", u"true", u"+")


def _location_curve(element):
    try:
        loc = element.Location
    except:
        return None
    if loc is None:
        return None
    try:
        return loc.Curve
    except:
        return None


def _element_length_mm(element):
    """Длина осевой геометрии в мм или None, если геометрии нет."""
    curve = _location_curve(element)
    if curve is None:
        return None
    try:
        return curve.Length * FEET_TO_MM
    except:
        return None


def _location_point(element):
    try:
        loc = element.Location
    except:
        return None
    if loc is None:
        return None
    try:
        point = loc.Point
        if point is not None:
            return point
    except:
        pass
    return None


def _cell(value, size):
    """Индекс ячейки сетки. Усечение int безопасно при переборе соседей ±1."""
    try:
        return int(value / size)
    except:
        return 0


def _safe_name(element):
    try:
        return Element.Name.GetValue(element)
    except:
        pass
    try:
        return element.Name or u""
    except:
        return u""


def _type_name(doc, element):
    try:
        type_element = doc.GetElement(element.GetTypeId())
        if type_element is not None:
            return _safe_name(type_element)
    except:
        pass
    return u""


def _parse_category_map(raw_text, category_map, default_keys):
    keys = _parse_category_keys(raw_text)
    if not keys:
        keys = list(default_keys)
    categories = []
    for key in keys:
        built_in = category_map.get(key)
        if built_in is not None:
            categories.append(built_in)
    return categories


def _category_id(element):
    try:
        if element.Category is not None:
            return int(element.Category.Id.IntegerValue)
    except:
        pass
    return None


def _duplicate_label(doc, element):
    parts = []
    try:
        if element.Category is not None:
            parts.append(element.Category.Name)
    except:
        pass
    type_name = _type_name(doc, element)
    if type_name:
        parts.append(u"тип «{0}»".format(type_name))
    return u", ".join(parts) if parts else u"элемент"


def _param_text_from(param):
    """Текст значения параметра или u"" (пусто/нет значения)."""
    if param is None:
        return u""
    try:
        if not param.HasValue:
            return u""
    except:
        pass
    try:
        if param.StorageType == StorageType.String:
            return (param.AsString() or u"").strip()
    except:
        pass
    try:
        s = param.AsValueString()
        if s:
            return s.strip()
    except:
        pass
    return u""


def _smoke_search_text(doc, element, use_name_fallback):
    """(текст для поиска масок, имя системы). Пустой текст = элемент пропускаем."""
    system_name = _pipe_system_name(element)
    if system_name:
        return system_name, system_name
    if not use_name_fallback:
        return u"", u""

    parts = []
    type_name = _type_name(doc, element)
    if type_name:
        parts.append(type_name)
    own_name = _safe_name(element)
    if own_name and own_name not in parts:
        parts.append(own_name)
    return u" ".join(parts), u""


def _smoke_label(element, display_name):
    """Подпись элемента: категория, имя системы (или типа) и id."""
    parts = []
    try:
        if element.Category is not None:
            parts.append(element.Category.Name)
    except:
        pass
    if display_name:
        parts.append(u"«{0}»".format(display_name))
    try:
        parts.append(u"id {0}".format(element.Id.IntegerValue))
    except:
        pass
    return u", ".join(parts) if parts else u"элемент"
