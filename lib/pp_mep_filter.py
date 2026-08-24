# -*- coding: utf-8 -*-

# Общий модуль фильтра элементов по параметрам.
# Логика перенесена из инструмента «PP_Передача параметров по MEP-соединениям»,
# чтобы стратегия проверки использовала точно такой же фильтр.
# Все функции чтения параметризованы doc (в отличие от исходника, где doc
# был модульной глобальной переменной).

import clr
import re

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInParameter,
    ElementId,
    StorageType,
)


# Операторы дополнительного фильтра (label -> ключ). Числовые операторы
# (gt/lt/ge/le) сравнивают значение в единицах проекта (как в поле ввода Revit).
FILTER_OPERATORS = [
    (u"Равно", "eq"),
    (u"Не равно", "ne"),
    (u"Содержит", "contains"),
    (u"Не содержит", "not_contains"),
    (u"Начинается с", "starts_with"),
    (u"Больше", "gt"),
    (u"Меньше", "lt"),
    (u"Больше или равно", "ge"),
    (u"Меньше или равно", "le"),
    (u"Пусто", "empty"),
    (u"Не пусто", "not_empty"),
]
FILTER_OP_KEYS = [key for _label, key in FILTER_OPERATORS]
FILTER_NO_VALUE_OPS = set(["empty", "not_empty"])
FILTER_NUMERIC_OPS = set(["gt", "lt", "ge", "le"])
FILTER_MATCH_ALL = "all"
FILTER_MATCH_ANY = "any"
# "Внутри группы" = match (все/любое). "Между группами" = group_combine (И/ИЛИ).
FILTER_GROUP_COMBINE_AND = "and"
FILTER_GROUP_COMBINE_OR = "or"
FILTER_GROUP_NUMBERS = [1, 2, 3, 4]
DEFAULT_FILTER = {
    "enabled": False,
    "match": FILTER_MATCH_ALL,
    "group_combine": FILTER_GROUP_COMBINE_AND,
    "apply_source": True,
    "apply_receiver": False,
    "conditions": [],
}


def normalize_filter_config(filter_config):
    if not isinstance(filter_config, dict):
        return {
            "enabled": False,
            "match": FILTER_MATCH_ALL,
            "group_combine": FILTER_GROUP_COMBINE_AND,
            "apply_source": True,
            "apply_receiver": False,
            "conditions": [],
        }

    match = filter_config.get("match")
    if match not in (FILTER_MATCH_ALL, FILTER_MATCH_ANY):
        match = FILTER_MATCH_ALL

    group_combine = filter_config.get("group_combine")
    if group_combine not in (FILTER_GROUP_COMBINE_AND, FILTER_GROUP_COMBINE_OR):
        group_combine = FILTER_GROUP_COMBINE_AND

    conditions = []
    for condition in filter_config.get("conditions") or []:
        if not isinstance(condition, dict):
            continue

        param_name = unicode(condition.get("param") or u"").strip()
        if not param_name:
            continue

        op = condition.get("op")
        if op not in FILTER_OP_KEYS:
            op = "eq"

        try:
            group = int(condition.get("group", 1))
        except:
            group = 1
        if group < 1:
            group = 1

        conditions.append({
            "param": param_name,
            "op": op,
            "value": unicode(condition.get("value") or u"").strip(),
            "group": group,
        })

    return {
        "enabled": bool(filter_config.get("enabled")),
        "match": match,
        "group_combine": group_combine,
        "apply_source": bool(filter_config.get("apply_source", True)),
        "apply_receiver": bool(filter_config.get("apply_receiver", False)),
        "conditions": conditions,
    }


def parse_filter_number(text):
    if text is None:
        return None

    normalized = unicode(text).strip().replace(u",", u".")
    if not normalized:
        return None

    match = re.search(r"[-+]?\d+(?:\.\d+)?", normalized)
    if match is None:
        return None

    try:
        return float(match.group(0))
    except:
        return None


def _get_type_element(element):
    try:
        return element.Document.GetElement(element.GetTypeId())
    except:
        return None


def _get_lookup_parameter(element, name):
    if element is None or not name:
        return None

    try:
        return element.LookupParameter(name)
    except:
        return None


def _get_double_edit_text(doc, param, value):
    """Значение Double так, как оно выглядит в поле ввода Revit — без символа
    единиц (forEditing). Если API недоступен, возвращает None."""
    try:
        from Autodesk.Revit.DB import UnitFormatUtils
        units = doc.GetUnits()
    except:
        return None

    # Revit 2022+ : Format(Units, ForgeTypeId, double, forEditing)
    try:
        spec = param.Definition.GetDataType()
        return unicode(UnitFormatUtils.Format(units, spec, value, True)).strip()
    except:
        pass

    # Revit <=2021 : Format(Units, UnitType, double, maxAccuracy, forEditing)
    try:
        spec = param.Definition.UnitType
        return unicode(UnitFormatUtils.Format(units, spec, value, False, True)).strip()
    except:
        return None


def _get_filter_param(element, name):
    param = _get_lookup_parameter(element, name)
    if param is not None:
        return param

    type_element = _get_type_element(element)
    if type_element is not None:
        return _get_lookup_parameter(type_element, name)

    return None


def _get_filter_text_value(doc, param):
    if param is None:
        return u""

    try:
        storage_type = param.StorageType

        if storage_type == StorageType.String:
            text = param.AsString()
            if text is None:
                text = param.AsValueString()
            return unicode(text or u"")

        if storage_type == StorageType.Integer:
            return unicode(param.AsInteger())

        if storage_type == StorageType.Double:
            edit_text = _get_double_edit_text(doc, param, float(param.AsDouble()))
            if edit_text:
                return edit_text
            return unicode(param.AsValueString() or u"")

        if storage_type == StorageType.ElementId:
            value_string = param.AsValueString()
            if value_string:
                return unicode(value_string)
            element_id = param.AsElementId()
            if element_id is not None:
                return unicode(element_id.IntegerValue)
    except:
        pass

    return u""


def _get_filter_number_value(doc, param):
    if param is None:
        return None

    try:
        storage_type = param.StorageType

        if storage_type == StorageType.Integer:
            return float(param.AsInteger())

        if storage_type == StorageType.Double:
            edit_text = _get_double_edit_text(doc, param, float(param.AsDouble()))
            number = parse_filter_number(edit_text)
            if number is not None:
                return number
            return float(param.AsDouble())

        if storage_type == StorageType.String:
            return parse_filter_number(param.AsString())
    except:
        pass

    return None


def _get_type_name(element):
    # Надежнее всего — через параметр экземпляра "Тип" (ELEM_TYPE_PARAM):
    # его AsValueString() возвращает имя типоразмера.
    try:
        type_param = element.get_Parameter(BuiltInParameter.ELEM_TYPE_PARAM)
        if type_param is not None:
            text = type_param.AsValueString()
            if text:
                return unicode(text).strip()
    except:
        pass

    type_element = _get_type_element(element)
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


def _get_family_name(element):
    try:
        if element.Symbol is not None and element.Symbol.Family is not None:
            return unicode(element.Symbol.Family.Name or u"").strip()
    except:
        pass

    type_element = _get_type_element(element)
    if type_element is not None:
        try:
            return unicode(type_element.FamilyName or u"").strip()
        except:
            pass

    return u""


def _get_category_name(element):
    try:
        return unicode(element.Category.Name or u"").strip()
    except:
        return u"?"


def _get_system_name(element):
    # Имя системы у труб/воздуховодов/оборудования: сначала встроенный
    # RBS_SYSTEM_NAME_PARAM, затем MEPSystem.Name.
    try:
        param = element.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
        if param is not None:
            text = param.AsString()
            if not text:
                text = param.AsValueString()
            if text:
                return unicode(text).strip()
    except:
        pass

    try:
        mep_system = element.MEPSystem
        if mep_system is not None and mep_system.Name:
            return unicode(mep_system.Name).strip()
    except:
        pass

    return u""


def _get_special_field_text(element, name):
    """Значения, которые не читаются через LookupParameter: имя типа,
    имя семейства, категория. Возвращает None, если имя условия не относится
    к спец-полю."""
    key = unicode(name or u"").strip().lower()

    if key in (u"имя типа", u"type name", u"тип"):
        return _get_type_name(element)

    if key in (u"имя семейства", u"family name", u"семейство", u"family"):
        return _get_family_name(element)

    if key in (u"семейство и тип", u"family and type"):
        family_name = _get_family_name(element)
        type_name = _get_type_name(element)
        return u"{0}: {1}".format(family_name, type_name).strip()

    if key in (u"категория", u"category"):
        return _get_category_name(element)

    if key in (u"имя системы", u"имясистемы", u"система", u"system name", u"system"):
        return _get_system_name(element)

    return None


def _get_condition_element_text(doc, element, name):
    param = _get_filter_param(element, name)
    text = _get_filter_text_value(doc, param) if param is not None else u""

    if text:
        return text

    special = _get_special_field_text(element, name)
    if special is not None:
        return special

    return text


def _get_condition_element_number(doc, element, name):
    param = _get_filter_param(element, name)
    number = _get_filter_number_value(doc, param) if param is not None else None

    if number is not None:
        return number

    special = _get_special_field_text(element, name)
    if special is not None:
        return parse_filter_number(special)

    return None


# Похожие по начертанию кириллические буквы -> латиница (нижний регистр).
# Нужно, чтобы условие "содержит T11" (латиница) срабатывало на "Т11"
# (кириллица) и наоборот — частый источник несовпадений в именах систем.
_CONFUSABLE_MAP = {
    u"а": u"a", u"в": u"b", u"е": u"e", u"ё": u"e", u"к": u"k", u"м": u"m",
    u"н": u"h", u"о": u"o", u"р": u"p", u"с": u"c", u"т": u"t", u"у": u"y",
    u"х": u"x",
}


def _fold_confusables(text):
    if not text:
        return text
    return u"".join(_CONFUSABLE_MAP.get(ch, ch) for ch in text)


def evaluate_filter_condition(doc, element, condition):
    op = condition.get("op", "eq")
    name = condition.get("param", u"")

    if op in FILTER_NO_VALUE_OPS:
        text = _get_condition_element_text(doc, element, name).strip()
        if op == "empty":
            return text == u""
        return text != u""

    if op in FILTER_NUMERIC_OPS:
        element_number = _get_condition_element_number(doc, element, name)
        condition_number = parse_filter_number(condition.get("value"))
        if element_number is None or condition_number is None:
            return False
        if op == "gt":
            return element_number > condition_number
        if op == "lt":
            return element_number < condition_number
        if op == "ge":
            return element_number >= condition_number
        return element_number <= condition_number

    element_text = _fold_confusables(
        _get_condition_element_text(doc, element, name).strip().lower()
    )
    condition_text = _fold_confusables(
        unicode(condition.get("value") or u"").strip().lower()
    )

    if op == "eq":
        return element_text == condition_text
    if op == "ne":
        return element_text != condition_text
    if op == "contains":
        return condition_text in element_text
    if op == "not_contains":
        return condition_text not in element_text
    if op == "starts_with":
        return element_text.startswith(condition_text)

    return True


def get_active_filter_conditions(filter_config):
    if not filter_config or not filter_config.get("enabled"):
        return []

    conditions = filter_config.get("conditions") or []
    return [
        condition for condition in conditions
        if unicode(condition.get("param") or u"").strip()
    ]


def element_passes_filter(doc, element, filter_config):
    conditions = get_active_filter_conditions(filter_config)

    if not conditions:
        return True

    within_any = filter_config.get("match") == FILTER_MATCH_ANY

    # Раскладываем условия по группам с сохранением порядка появления групп.
    groups = {}
    group_order = []
    for condition in conditions:
        group = condition.get("group", 1)
        if group not in groups:
            groups[group] = []
            group_order.append(group)
        groups[group].append(condition)

    group_results = []
    for group in group_order:
        results = [
            evaluate_filter_condition(doc, element, condition)
            for condition in groups[group]
        ]
        if within_any:
            group_results.append(any(results))
        else:
            group_results.append(all(results))

    if filter_config.get("group_combine") == FILTER_GROUP_COMBINE_OR:
        return any(group_results)

    return all(group_results)


def filter_targets_side(filter_config, side):
    """Применяется ли фильтр к указанной стороне ("source" / "receiver")."""
    if not get_active_filter_conditions(filter_config):
        return False

    if side == "source":
        return bool(filter_config.get("apply_source", True))

    return bool(filter_config.get("apply_receiver", False))


def get_filter_label(filter_config):
    conditions = get_active_filter_conditions(filter_config)

    if not conditions:
        return u"выключен"

    if filter_config.get("match") == FILTER_MATCH_ANY:
        within_label = u"любое"
    else:
        within_label = u"все"

    if filter_config.get("group_combine") == FILTER_GROUP_COMBINE_OR:
        between_label = u"ИЛИ"
    else:
        between_label = u"И"

    group_count = len(set(condition.get("group", 1) for condition in conditions))

    sides = []
    if filter_config.get("apply_source", True):
        sides.append(u"источник")
    if filter_config.get("apply_receiver", False):
        sides.append(u"приемник")
    sides_label = u", ".join(sides) if sides else u"нет сторон"

    return u"включен ({0} усл. в {1} гр.; внутри: {2}, между: {3}; к: {4})".format(
        len(conditions),
        group_count,
        within_label,
        between_label,
        sides_label
    )
