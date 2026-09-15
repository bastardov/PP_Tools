# -*- coding: utf-8 -*-
"""Общие хелперы «Проверки спецификации» — то, чем пользуются 2 и больше проверок.

Имена перенесены из pp_spec_checks.py как есть. Хелпер, нужный одной проверке,
живёт в её файле check_*.py. Узкие группы вынесены отдельно:
common_size.py (размер в наименовании) и common_nested.py (вложенные семейства).
"""

import clr
import re

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    StorageType,
)

from spec_checks.core import (
    CheckIssue,
    CheckResult,
)


MM_PER_FOOT = 304.8


PIPE_AND_DUCT_CATEGORIES = [
    BuiltInCategory.OST_PipeCurves,
    BuiltInCategory.OST_FlexPipeCurves,
    BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_FlexDuctCurves,
]


# Категории, доступные для выбора в стратегиях с мультивыбором категорий.
# (ключ, подпись в интерфейсе, BuiltInCategory)
SORT_ORDER_CATEGORY_OPTIONS = [
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_PipeCurves", u"Трубы", BuiltInCategory.OST_PipeCurves),
    (u"OST_DuctCurves", u"Воздуховоды", BuiltInCategory.OST_DuctCurves),
    (u"OST_PipeAccessory", u"Арматура трубопроводов", BuiltInCategory.OST_PipeAccessory),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_PipeFitting", u"Соединительные детали трубопроводов", BuiltInCategory.OST_PipeFitting),
    (u"OST_FlexPipeCurves", u"Гибкие трубы", BuiltInCategory.OST_FlexPipeCurves),
    (u"OST_PipeInsulations", u"Материалы изоляции труб", BuiltInCategory.OST_PipeInsulations),
    (u"OST_DuctFitting", u"Соединительные детали воздуховодов", BuiltInCategory.OST_DuctFitting),
    (u"OST_FlexDuctCurves", u"Гибкие воздуховоды", BuiltInCategory.OST_FlexDuctCurves),
    (u"OST_DuctInsulations", u"Материалы изоляции воздуховодов", BuiltInCategory.OST_DuctInsulations),
    (u"OST_GenericModel", u"Обобщенные модели", BuiltInCategory.OST_GenericModel),
]


# ключ категории -> BuiltInCategory
SORT_ORDER_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in SORT_ORDER_CATEGORY_OPTIONS
}


# Список ключей всех категорий (используется как значение по умолчанию).
SORT_ORDER_ALL_CATEGORY_KEYS = [
    key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
]


def _get_type_element(doc, element):
    try:
        return doc.GetElement(element.GetTypeId())
    except:
        return None


def _get_family_name(element):
    try:
        symbol = element.Symbol
        if symbol and symbol.Family:
            return unicode(symbol.Family.Name or u"").strip()
    except:
        pass

    try:
        type_element = element.Document.GetElement(element.GetTypeId())
        family_name = type_element.FamilyName
        if family_name:
            return unicode(family_name).strip()
    except:
        pass

    try:
        param = element.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
        value = _get_parameter_text_from_param(param)
        if value:
            return value
    except:
        pass

    try:
        type_element = element.Document.GetElement(element.GetTypeId())
        param = type_element.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
        value = _get_parameter_text_from_param(param)
        if value:
            return value
    except:
        pass

    return u""


def _get_type_name(doc, element):
    # Инстансный параметр "Тип" (ELEM_TYPE_PARAM) надежнее всего: его
    # AsValueString возвращает имя типоразмера. Прямой .Name у системных типов
    # (трубы, воздуховоды) может бросать исключение или быть пустым.
    try:
        type_param = element.get_Parameter(BuiltInParameter.ELEM_TYPE_PARAM)
        if type_param is not None:
            text = type_param.AsValueString()
            if text:
                return unicode(text).strip()
    except:
        pass

    type_element = _get_type_element(doc, element)
    if type_element is None:
        return u""

    for bip in (BuiltInParameter.ALL_MODEL_TYPE_NAME, BuiltInParameter.SYMBOL_NAME_PARAM):
        try:
            type_param = type_element.get_Parameter(bip)
            if type_param is not None:
                text = type_param.AsString()
                if text:
                    return unicode(text).strip()
        except:
            pass

    try:
        return unicode(type_element.Name or u"").strip()
    except:
        return u""


def _get_category_name(element):
    try:
        return unicode(element.Category.Name or u"").strip()
    except:
        return u"?"


def _get_element_label(doc, element):
    type_name = _get_type_name(doc, element)
    category_name = _get_category_name(element)

    if type_name:
        return u"ID {0} | {1} | {2}".format(
            element.Id.IntegerValue,
            category_name,
            type_name
        )

    return u"ID {0} | {1}".format(
        element.Id.IntegerValue,
        category_name
    )


def _get_lookup_parameter(element, names):
    if not isinstance(names, list):
        names = [names]

    for name in names:
        if not name:
            continue
        try:
            param = element.LookupParameter(name)
            if param is not None:
                return param
        except:
            pass

    return None


def _get_parameter_text_from_param(param):
    if param is None:
        return u""

    try:
        value = param.AsString()
        if value:
            return unicode(value).strip()
    except:
        pass

    try:
        value = param.AsValueString()
        if value:
            return unicode(value).strip()
    except:
        pass

    try:
        if param.StorageType == StorageType.Integer:
            return unicode(param.AsInteger()).strip()
    except:
        pass

    try:
        if param.StorageType == StorageType.Double:
            return unicode(param.AsDouble()).strip()
    except:
        pass

    return u""


def _get_parameter_text(element, names):
    return _get_parameter_text_from_param(_get_lookup_parameter(element, names))


def _get_parameter_text_from_element_or_type(doc, element, names):
    value = _get_parameter_text(element, names)
    if value:
        return value

    type_element = _get_type_element(doc, element)
    if type_element is None:
        return u""

    return _get_parameter_text(type_element, names)


def _has_nonempty_text(element, names):
    return bool(_get_parameter_text(element, names))


def _normalize_space(text):
    text = unicode(text or u"").strip()
    if not text:
        return u""
    return u" ".join(text.split())


def _parse_category_keys(raw_text):
    result = []
    seen = set()
    parts = re.split(r"[;,/\n]+", unicode(raw_text or u""))

    for part in parts:
        token = _normalize_space(part)
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)

    return result


def _parse_float(raw_text):
    text = unicode(raw_text or u"").strip()
    if not text:
        return None

    text = text.replace(u",", u".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None

    try:
        return float(match.group(0))
    except:
        return None


def _get_length_param_mm(element, param_names):
    param = _get_lookup_parameter(element, param_names)

    if param is None:
        return None

    try:
        if param.StorageType == StorageType.Double:
            return float(param.AsDouble()) * MM_PER_FOOT
    except:
        pass

    return _parse_float(_get_parameter_text_from_param(param))


def _get_number_param(element, param_names):
    """Числовое значение параметра без пересчета единиц (для счетных полей)."""
    param = _get_lookup_parameter(element, param_names)

    if param is None:
        return None

    try:
        if param.StorageType == StorageType.Double:
            return float(param.AsDouble())
    except:
        pass

    try:
        if param.StorageType == StorageType.Integer:
            return float(param.AsInteger())
    except:
        pass

    return _parse_float(_get_parameter_text_from_param(param))


def _is_zero_number(value):
    if value is None:
        return False
    return abs(float(value)) < 0.0001


def _make_simple_result(key, title, elements, param_names, message_template):
    issues = []
    checked_count = len(elements)

    for element in elements:
        if _has_nonempty_text(element, param_names):
            continue
        issues.append(CheckIssue(
            message_template.format(_get_element_label(element.Document, element)),
            [element.Id.IntegerValue]
        ))

    return CheckResult(key, title, checked_count, issues)


def _resolve_categories(category_keys):
    categories = []

    for category_key in category_keys:
        built_in = SORT_ORDER_CATEGORY_MAP.get(category_key)
        if built_in is not None:
            categories.append(built_in)

    return categories
