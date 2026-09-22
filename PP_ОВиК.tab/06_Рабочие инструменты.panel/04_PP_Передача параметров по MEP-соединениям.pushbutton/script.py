# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FilteredElementCollector,
    InstanceBinding,
    StorageType,
    Transaction,
    TypeBinding,
)
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

import os
import re
import sys


from pyrevit import forms, script
from pp_settings import load_settings, save_settings

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_mep_transfer_window


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TOOL_TITLE = u"PP_Передача параметров по MEP-соединениям"
DEFAULT_SOURCE_PARAM = u"ADSK_Имя системы сокращенное"
DEFAULT_RECEIVER_PARAM = u"ADSK_Имя системы сокращенное"
SETTINGS_KEY = "mep_param_transfer_settings"
REPORT_SETTING_KEY = "mep_transfer_show_pyrevit_report"
SCOPE_WHOLE_MODEL = "whole_model"
SCOPE_ACTIVE_VIEW = "active_view"
SCOPE_MANUAL_SOURCE = "manual_source"
SCOPE_MANUAL_RECEIVER = "manual_receiver"
LEGACY_SCOPE_MANUAL = "manual_selection"

CATEGORY_OPTIONS = [
    {
        "key": u"pipe_curves",
        "name": u"Трубы",
        "category": BuiltInCategory.OST_PipeCurves,
    },
    {
        "key": u"pipe_fittings",
        "name": u"Соединительные детали трубопроводов",
        "category": BuiltInCategory.OST_PipeFitting,
    },
    {
        "key": u"pipe_accessories",
        "name": u"Арматура трубопроводов",
        "category": BuiltInCategory.OST_PipeAccessory,
    },
    {
        "key": u"equipment",
        "name": u"Оборудование",
        "category": BuiltInCategory.OST_MechanicalEquipment,
    },
]

DEFAULT_SOURCE_KEYS = [u"pipe_curves"]
DEFAULT_RECEIVER_KEYS = [u"pipe_accessories"]

VALID_CATEGORY_KEYS = set(option["key"] for option in CATEGORY_OPTIONS)

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
FILTER_GROUP_COMBINE_AND = "and"
FILTER_GROUP_COMBINE_OR = "or"
FILTER_GROUP_NUMBERS = [1, 2, 3, 4]
DEFAULT_FILTER = {
    "enabled": False,
    "match": FILTER_MATCH_ALL,
    "group_combine": FILTER_GROUP_COMBINE_AND,
    "conditions": [],
}

# Накопитель перехваченных исключений. Голые except по коду больше не
# «глотают» ошибку молча — они пишут сюда, а сводка попадает в отчет.
DEBUG_LOG = []


def log_debug(context, ex):
    try:
        message = u"{0}: {1}".format(context, unicode(ex))
    except:
        message = unicode(context)

    if message not in DEBUG_LOG:
        DEBUG_LOG.append(message)


class CategorySelectionFilter(ISelectionFilter):
    def __init__(self, allowed_category_ids):
        self.allowed_category_ids = allowed_category_ids

    def AllowElement(self, elem):
        return is_allowed_category(elem, self.allowed_category_ids)

    def AllowReference(self, ref, point):
        return False


def load_transfer_settings():
    settings = load_settings()
    state = settings.get(SETTINGS_KEY, {})

    if not isinstance(state, dict):
        return {}

    return state


def save_transfer_settings(config):
    settings = load_settings()
    settings[SETTINGS_KEY] = {
        "source_param": config.get("source_param", DEFAULT_SOURCE_PARAM),
        "receiver_param": config.get("receiver_param", DEFAULT_RECEIVER_PARAM),
        "mode": config.get("mode", SCOPE_WHOLE_MODEL),
        "source_keys": [option["key"] for option in config.get("source_options", [])],
        "receiver_keys": [option["key"] for option in config.get("receiver_options", [])],
        "filter": normalize_filter_config(config.get("filter")),
        "chain_fill": bool(config.get("chain_fill")),
    }
    save_settings(settings)


def normalize_filter_config(filter_config):
    if not isinstance(filter_config, dict):
        return {"enabled": False, "match": FILTER_MATCH_ALL, "conditions": []}

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
        except Exception:
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


def should_show_pyrevit_report():
    settings = load_settings()
    return settings.get(REPORT_SETTING_KEY, True)


def get_saved_text(settings, key, default_value):
    value = unicode(settings.get(key, default_value) or u"").strip()

    if value:
        return value

    return default_value


def get_saved_mode(settings):
    mode = settings.get("mode", SCOPE_WHOLE_MODEL)

    if mode in [SCOPE_WHOLE_MODEL, SCOPE_ACTIVE_VIEW, SCOPE_MANUAL_SOURCE, SCOPE_MANUAL_RECEIVER]:
        return mode

    if settings.get("source_scope") == LEGACY_SCOPE_MANUAL:
        return SCOPE_MANUAL_SOURCE

    receiver_scope = settings.get(
        "receiver_scope",
        settings.get("scope", SCOPE_WHOLE_MODEL)
    )

    if receiver_scope == LEGACY_SCOPE_MANUAL:
        return SCOPE_MANUAL_RECEIVER

    return SCOPE_WHOLE_MODEL


def get_saved_keys(settings, key, default_keys):
    values = settings.get(key)

    if not isinstance(values, list):
        return list(default_keys)

    result = [unicode(value) for value in values if value in VALID_CATEGORY_KEYS]

    if not result:
        return list(default_keys)

    return result


def get_category_ids(options):
    ids = set()

    for option in options:
        ids.add(int(option["category"]))

    return ids


# Режимы обработки: ключ, подпись чипса, пояснение под ним.
# Порядок совпадает с порядком чипсов в разметке.
SCOPE_MODES = [
    (SCOPE_WHOLE_MODEL, u"Вся модель",
     u"Источники и приёмники ищутся по всей модели. На крупном проекте это дольше."),
    (SCOPE_ACTIVE_VIEW, u"Текущий вид",
     u"Обрабатываются только элементы активного вида."),
    (SCOPE_MANUAL_SOURCE, u"Источник вручную",
     u"Источники укажете мышью в модели, приёмники найдутся по соединениям."),
    (SCOPE_MANUAL_RECEIVER, u"Приёмник вручную",
     u"Приёмники укажете мышью в модели, источники найдутся по соединениям."),
]


def collect_project_parameter_options():
    u"""Параметры проекта, применимые к поддерживаемым категориям.

    Окно не обращается к Revit API: здесь заранее собирается компактная карта
    имя -> категории экземпляра/типа. Ручной ввод в окне остаётся доступен для
    встроенных и семейных параметров, которых нет в ParameterBindings.
    """
    category_key_by_id = {}

    for option in CATEGORY_OPTIONS:
        try:
            category_id = ElementId(option["category"]).IntegerValue
            category_key_by_id[category_id] = option["key"]
        except Exception as ex:
            log_debug(u"Подготовка категорий параметров проекта", ex)

    by_name = {}

    try:
        iterator = doc.ParameterBindings.ForwardIterator()
        iterator.Reset()

        while iterator.MoveNext():
            definition = iterator.Key
            binding = iterator.Current

            try:
                name = unicode(definition.Name or u"").strip()
            except Exception:
                name = u""

            if not name:
                continue

            if isinstance(binding, InstanceBinding):
                scope_key = "instance_keys"
            elif isinstance(binding, TypeBinding):
                scope_key = "type_keys"
            else:
                continue

            category_keys = set()

            try:
                for category in binding.Categories:
                    key = category_key_by_id.get(category.Id.IntegerValue)
                    if key:
                        category_keys.add(key)
            except Exception as ex:
                log_debug(u"Категории параметра проекта '{0}'".format(name), ex)

            if not category_keys:
                continue

            item = by_name.get(name)

            if item is None:
                item = {
                    "name": name,
                    "instance_keys": set(),
                    "type_keys": set(),
                }
                by_name[name] = item

            item[scope_key].update(category_keys)
    except Exception as ex:
        log_debug(u"Сбор параметров проекта", ex)

    result = []

    for name, item in by_name.items():
        result.append({
            u"name": name,
            u"instance_keys": sorted(item["instance_keys"]),
            u"type_keys": sorted(item["type_keys"]),
        })

    return sorted(result, key=lambda item: item[u"name"].lower())


def show_setup_dialog():
    u"""Окно настроек. Возврат прежний: словарь параметров запуска или None."""
    settings = load_transfer_settings()

    setup_data = pp_mep_transfer_window.ask(_HERE, {
        u"options": CATEGORY_OPTIONS,
        u"source_keys": get_saved_keys(settings, "source_keys", DEFAULT_SOURCE_KEYS),
        u"receiver_keys": get_saved_keys(settings, "receiver_keys", DEFAULT_RECEIVER_KEYS),
        u"source_param": get_saved_text(settings, "source_param", DEFAULT_SOURCE_PARAM),
        u"receiver_param": get_saved_text(settings, "receiver_param", DEFAULT_RECEIVER_PARAM),
        u"mode": get_saved_mode(settings),
        u"chain_fill": bool(settings.get("chain_fill")),
        u"filter": normalize_filter_config(settings.get("filter")),
        u"modes": SCOPE_MODES,
        u"parameters": collect_project_parameter_options(),
    })

    if setup_data is None:
        return None

    save_transfer_settings(setup_data)

    return setup_data


def get_active_view_id():
    try:
        view = doc.ActiveView
        if view is not None:
            return view.Id
    except Exception as ex:
        log_debug(u"Получение активного вида", ex)

    return None


def collect_elements(category_options, view_id=None):
    result = []

    for option in category_options:
        try:
            if view_id is not None:
                collector = FilteredElementCollector(doc, view_id)
            else:
                collector = FilteredElementCollector(doc)

            elements = collector \
                .OfCategory(option["category"]) \
                .WhereElementIsNotElementType() \
                .ToElements()
            result.extend(list(elements))
        except Exception as ex:
            log_debug(u"Сбор элементов категории", ex)

    return result


def pick_elements(category_options, prompt):
    allowed_category_ids = get_category_ids(category_options)
    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        CategorySelectionFilter(allowed_category_ids),
        prompt
    )
    result = []
    seen = set()

    for ref in refs:
        element = doc.GetElement(ref.ElementId)

        if element is None:
            continue

        element_id = element.Id.IntegerValue

        if element_id in seen:
            continue

        seen.add(element_id)
        result.append(element)

    return result


def collect_source_elements(config):
    mode = config.get("mode")

    if mode == SCOPE_MANUAL_SOURCE:
        return pick_elements(
            config["source_options"],
            u"Выберите элементы-источники и нажмите Готово"
        )

    if mode == SCOPE_ACTIVE_VIEW:
        return collect_elements(config["source_options"], get_active_view_id())

    return collect_elements(config["source_options"])


def collect_receiver_elements(config):
    if config.get("mode") == SCOPE_MANUAL_RECEIVER:
        return pick_elements(
            config["receiver_options"],
            u"Выберите элементы-приемники и нажмите Готово"
        )

    return collect_elements(config["receiver_options"])


def collect_connected_receivers_from_sources(source_elements, receiver_options):
    allowed_category_ids = get_category_ids(receiver_options)
    result = []
    seen = set()

    for source in source_elements or []:
        for connector in get_connectors(source):
            try:
                refs = connector.AllRefs
            except Exception as ex:
                log_debug(u"Чтение AllRefs коннектора источника", ex)
                refs = []

            for ref_connector in refs:
                try:
                    owner = ref_connector.Owner
                except Exception as ex:
                    log_debug(u"Владелец коннектора (источник->приемник)", ex)
                    owner = None

                if owner is None:
                    continue

                owner_id = owner.Id.IntegerValue

                if owner_id == source.Id.IntegerValue:
                    continue

                if owner_id in seen:
                    continue

                if not is_allowed_category(owner, allowed_category_ids):
                    continue

                seen.add(owner_id)
                result.append(owner)

    return result


def get_receiver_elements(config, source_elements):
    if config.get("mode") in (SCOPE_MANUAL_SOURCE, SCOPE_ACTIVE_VIEW):
        return collect_connected_receivers_from_sources(
            source_elements,
            config["receiver_options"]
        )

    return collect_receiver_elements(config)


def get_type_element(element):
    try:
        return element.Document.GetElement(element.GetTypeId())
    except Exception as ex:
        log_debug(u"Получение типа элемента", ex)
        return None


def get_lookup_parameter(element, name):
    if element is None or not name:
        return None

    try:
        return element.LookupParameter(name)
    except Exception as ex:
        log_debug(u"Поиск параметра '{0}'".format(name), ex)
        return None


def get_double_edit_text(param, value):
    """Возвращает значение Double так, как оно выглядит в поле ввода Revit —
    без символа единиц (forEditing). Если API недоступен, возвращает None,
    и вызывающий код падает обратно на AsValueString."""
    try:
        from Autodesk.Revit.DB import UnitFormatUtils
        units = doc.GetUnits()
    except Exception as ex:
        log_debug(u"Получение единиц измерения", ex)
        return None

    # Revit 2022+ : Format(Units, ForgeTypeId, double, forEditing)
    try:
        spec = param.Definition.GetDataType()
        return unicode(UnitFormatUtils.Format(units, spec, value, True)).strip()
    except Exception:
        pass

    # Revit <=2021 : Format(Units, UnitType, double, maxAccuracy, forEditing)
    try:
        spec = param.Definition.UnitType
        return unicode(UnitFormatUtils.Format(units, spec, value, False, True)).strip()
    except Exception as ex:
        log_debug(u"Форматирование значения Double", ex)
        return None


def get_parameter_payload_from_param(param):
    if param is None:
        return None

    storage_type = param.StorageType

    try:
        if storage_type == StorageType.String:
            text = param.AsString()
            if text is None:
                text = param.AsValueString()
            text = unicode(text or u"").strip()
            if not text:
                return None
            return {
                "storage": storage_type,
                "value": text,
                "text": text,
            }

        if storage_type == StorageType.Integer:
            value = int(param.AsInteger())
            return {
                "storage": storage_type,
                "value": value,
                "text": unicode(value),
            }

        if storage_type == StorageType.Double:
            value = float(param.AsDouble())
            text = param.AsValueString()
            text = unicode(text or u"").strip()
            if not text:
                text = unicode(value)
            return {
                "storage": storage_type,
                "value": value,
                "text": text,
                "edit_text": get_double_edit_text(param, value),
            }

        if storage_type == StorageType.ElementId:
            value = param.AsElementId()
            if value is None or value == ElementId.InvalidElementId:
                return None
            return {
                "storage": storage_type,
                "value": value,
                "text": unicode(value.IntegerValue),
            }
    except Exception as ex:
        log_debug(u"Чтение значения параметра", ex)
        return None

    return None


def get_source_parameter_payload(element, param_name):
    param = get_lookup_parameter(element, param_name)
    if param is not None:
        payload = get_parameter_payload_from_param(param)
        if payload is not None:
            return payload

    type_element = get_type_element(element)
    if type_element is not None:
        param = get_lookup_parameter(type_element, param_name)
        if param is not None:
            return get_parameter_payload_from_param(param)

    return None


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
    except Exception as ex:
        log_debug(u"Разбор числа фильтра", ex)
        return None


def get_filter_param(element, name):
    param = get_lookup_parameter(element, name)
    if param is not None:
        return param

    type_element = get_type_element(element)
    if type_element is not None:
        return get_lookup_parameter(type_element, name)

    return None


def get_filter_text_value(param):
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
            edit_text = get_double_edit_text(param, float(param.AsDouble()))
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
    except Exception as ex:
        log_debug(u"Чтение текста параметра для фильтра", ex)

    return u""


def get_filter_number_value(param):
    if param is None:
        return None

    try:
        storage_type = param.StorageType

        if storage_type == StorageType.Integer:
            return float(param.AsInteger())

        if storage_type == StorageType.Double:
            edit_text = get_double_edit_text(param, float(param.AsDouble()))
            number = parse_filter_number(edit_text)
            if number is not None:
                return number
            return float(param.AsDouble())

        if storage_type == StorageType.String:
            return parse_filter_number(param.AsString())
    except Exception as ex:
        log_debug(u"Чтение числа параметра для фильтра", ex)

    return None


def get_special_field_text(element, name):
    """Значения, которые не читаются через LookupParameter: имя типа
    (имя типоразмера), имя семейства, категория. Возвращает None, если имя
    условия не относится к спец-полю."""
    key = unicode(name or u"").strip().lower()

    if key in (u"имя типа", u"type name", u"тип"):
        return get_type_name(element)

    if key in (u"имя семейства", u"family name", u"семейство", u"family"):
        return get_family_name(element)

    if key in (u"семейство и тип", u"family and type"):
        family_name = get_family_name(element)
        type_name = get_type_name(element)
        return u"{0}: {1}".format(family_name, type_name).strip()

    if key in (u"категория", u"category"):
        return get_category_name(element)

    return None


def get_condition_element_text(element, name):
    param = get_filter_param(element, name)
    text = get_filter_text_value(param) if param is not None else u""

    if text:
        return text

    special = get_special_field_text(element, name)
    if special is not None:
        return special

    return text


def get_condition_element_number(element, name):
    param = get_filter_param(element, name)
    number = get_filter_number_value(param) if param is not None else None

    if number is not None:
        return number

    special = get_special_field_text(element, name)
    if special is not None:
        return parse_filter_number(special)

    return None


# Латиница и кириллица с одинаковым начертанием (с, о, р ...) сворачиваются
# к латинице — иначе фильтр не находит элемент при визуально совпадающих
# именах. Та же карта, что в lib/pp_mep_filter.py — править надо в обоих местах.
_CONFUSABLE_MAP = {
    u"а": u"a", u"в": u"b", u"е": u"e", u"ё": u"e", u"к": u"k", u"м": u"m",
    u"н": u"h", u"о": u"o", u"р": u"p", u"с": u"c", u"т": u"t", u"у": u"y",
    u"х": u"x",
}


def _fold_confusables(text):
    if not text:
        return text
    return u"".join(_CONFUSABLE_MAP.get(ch, ch) for ch in text)


def evaluate_filter_condition(element, condition):
    op = condition.get("op", "eq")
    name = condition.get("param", u"")

    if op in FILTER_NO_VALUE_OPS:
        text = get_condition_element_text(element, name).strip()
        if op == "empty":
            return text == u""
        return text != u""

    if op in FILTER_NUMERIC_OPS:
        element_number = get_condition_element_number(element, name)
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
        get_condition_element_text(element, name).strip().lower()
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


def element_passes_filter(element, filter_config):
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
            evaluate_filter_condition(element, condition)
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


def get_parameter_key(payload):
    if payload is None:
        return u""

    storage_type = payload["storage"]
    value = payload["value"]

    if storage_type == StorageType.Double:
        return u"double:{0:.12f}".format(value)

    if storage_type == StorageType.ElementId:
        return u"elementid:{0}".format(value.IntegerValue)

    return u"{0}:{1}".format(unicode(storage_type), unicode(value))


def get_parameter_display_value(payload):
    if payload is None:
        return u""
    return unicode(payload.get("text", u"")).strip()


def get_write_text(payload):
    """Текст для записи в строковый приемник. Для Double берем значение без
    символа единиц (edit_text), иначе — обычное отображаемое значение."""
    if payload is None:
        return u""

    edit_text = payload.get("edit_text")
    if edit_text:
        return unicode(edit_text).strip()

    return get_parameter_display_value(payload)


def set_parameter_from_payload(param, payload):
    if param is None:
        return False, u"Параметр приемника не найден."

    if param.IsReadOnly:
        return False, u"Параметр приемника доступен только для чтения."

    storage_type = param.StorageType

    try:
        if storage_type == StorageType.String:
            new_text = get_write_text(payload)
            current_text = param.AsString()
            if current_text is None:
                current_text = param.AsValueString()
            current_text = unicode(current_text or u"").strip()
            if current_text == new_text:
                return True, u"unchanged"
            param.Set(new_text)
            return True, u"updated"

        if storage_type == StorageType.Integer:
            if payload["storage"] != StorageType.Integer:
                return False, u"Тип параметра приемника не совпадает с источником."
            new_value = int(payload["value"])
            if param.AsInteger() == new_value:
                return True, u"unchanged"
            param.Set(new_value)
            return True, u"updated"

        if storage_type == StorageType.Double:
            if payload["storage"] != StorageType.Double:
                return False, u"Тип параметра приемника не совпадает с источником."
            new_value = float(payload["value"])
            if abs(param.AsDouble() - new_value) < 1e-9:
                return True, u"unchanged"
            param.Set(new_value)
            return True, u"updated"

        if storage_type == StorageType.ElementId:
            if payload["storage"] != StorageType.ElementId:
                return False, u"Тип параметра приемника не совпадает с источником."
            new_value = payload["value"]
            current_value = param.AsElementId()
            if current_value is not None and current_value.IntegerValue == new_value.IntegerValue:
                return True, u"unchanged"
            param.Set(new_value)
            return True, u"updated"
    except Exception as ex:
        log_debug(u"Запись значения в параметр приемника", ex)
        return False, unicode(ex)

    return False, u"Неподдерживаемый тип параметра приемника."


def get_connectors(element):
    connectors = []

    try:
        connector_manager = element.ConnectorManager
        if connector_manager is not None:
            for connector in connector_manager.Connectors:
                connectors.append(connector)
    except Exception:
        # Ожидаемо: у экземпляров (оборудование/фитинги) нет ConnectorManager,
        # коннекторы берутся ниже через MEPModel. Это не ошибка.
        pass

    try:
        mep_model = element.MEPModel
        if mep_model is not None and mep_model.ConnectorManager is not None:
            for connector in mep_model.ConnectorManager.Connectors:
                connectors.append(connector)
    except Exception:
        # Ожидаемо: у труб нет MEPModel, коннекторы уже получены выше.
        pass

    return connectors


def is_allowed_category(element, allowed_category_ids):
    try:
        category = element.Category
        if category is None:
            return False
        return category.Id.IntegerValue in allowed_category_ids
    except Exception as ex:
        log_debug(u"Проверка категории элемента", ex)
        return False


def get_connected_source_elements(receiver, allowed_category_ids, allowed_source_ids=None):
    result = []
    seen = set()

    for connector in get_connectors(receiver):
        try:
            refs = connector.AllRefs
        except Exception as ex:
            log_debug(u"Чтение AllRefs коннектора приемника", ex)
            refs = []

        for ref_connector in refs:
            try:
                owner = ref_connector.Owner
            except Exception as ex:
                log_debug(u"Владелец коннектора (приемник->источник)", ex)
                owner = None

            if owner is None:
                continue

            owner_id = owner.Id.IntegerValue

            if owner_id == receiver.Id.IntegerValue:
                continue

            if owner_id in seen:
                continue

            if allowed_source_ids is not None and owner_id not in allowed_source_ids:
                continue

            if not is_allowed_category(owner, allowed_category_ids):
                continue

            seen.add(owner_id)
            result.append(owner)

    return result


def get_family_name(element):
    try:
        if element.Symbol is not None and element.Symbol.Family is not None:
            return unicode(element.Symbol.Family.Name or u"").strip()
    except:
        pass

    type_element = get_type_element(element)
    if type_element is not None:
        try:
            return unicode(type_element.FamilyName or u"").strip()
        except:
            pass

    return u""


def get_type_name(element):
    # Надежнее всего — через параметр экземпляра "Тип" (ELEM_TYPE_PARAM):
    # его AsValueString() возвращает имя типоразмера. Прямой .Name у
    # системных типов (трубы, воздуховоды и т.п.) может бросать исключение.
    try:
        type_param = element.get_Parameter(BuiltInParameter.ELEM_TYPE_PARAM)
        if type_param is not None:
            text = type_param.AsValueString()
            if text:
                return unicode(text).strip()
    except Exception as ex:
        log_debug(u"Имя типа через ELEM_TYPE_PARAM", ex)

    type_element = get_type_element(element)
    if type_element is None:
        return u""

    for bip in (BuiltInParameter.ALL_MODEL_TYPE_NAME, BuiltInParameter.SYMBOL_NAME_PARAM):
        try:
            type_param = type_element.get_Parameter(bip)
            if type_param is not None:
                text = type_param.AsString()
                if text:
                    return unicode(text).strip()
        except Exception as ex:
            log_debug(u"Имя типа через параметр типа", ex)

    try:
        return unicode(type_element.Name or u"").strip()
    except Exception as ex:
        log_debug(u"Имя типа через .Name", ex)
        return u""


def get_category_name(element):
    try:
        return unicode(element.Category.Name or u"").strip()
    except:
        return u"?"


def get_element_label(element):
    parts = [
        u"ID {0}".format(element.Id.IntegerValue),
        get_category_name(element),
    ]

    family_name = get_family_name(element)
    type_name = get_type_name(element)

    if family_name:
        parts.append(family_name)

    if type_name:
        parts.append(type_name)

    return u" | ".join(parts)


def build_source_value_map(sources, source_param_name):
    value_map = {}

    for source in sources:
        payload = get_source_parameter_payload(source, source_param_name)
        if payload is None:
            continue

        key = get_parameter_key(payload)
        bucket = value_map.get(key)

        if bucket is None:
            bucket = {
                "payload": payload,
                "sources": [],
            }
            value_map[key] = bucket

        bucket["sources"].append(source)

    return value_map


def build_conflict_text(value_map):
    parts = []

    for bucket in value_map.values():
        payload = bucket["payload"]
        parts.append(u"'{0}'".format(get_parameter_display_value(payload)))

    return u", ".join(sorted(parts))


def write_report(report, config):
    output = script.get_output()
    output.print_md(u"### PP Передача параметров по MEP-соединениям — отчет")
    print(u"Источник: {0}".format(u", ".join([option["name"] for option in config["source_options"]])))
    print(u"Приемник: {0}".format(u", ".join([option["name"] for option in config["receiver_options"]])))
    print(u"Режим: {0}".format(get_mode_label(config.get("mode"))))
    print(u"Параметр источника: {0}".format(config["source_param"]))
    print(u"Параметр приемника: {0}".format(config["receiver_param"]))
    print(u"Фильтр: {0}".format(get_filter_label(config.get("filter"))))
    if report.get("filtered_receivers"):
        print(u"Отсеяно фильтром приемников: {0}".format(report["filtered_receivers"]))
    print(u"")

    sections = [
        (u"Заполнено", report["copied"]),
        (u"Без изменений", report["unchanged"]),
        (u"Конфликт", report["conflicts"]),
        (u"Источник не найден", report["no_source"]),
        (u"Нет параметра приемника", report["missing_receiver_param"]),
        (u"Ошибки записи", report["errors"]),
    ]

    for title, lines in sections:
        print(u"{0}: {1}".format(title, len(lines)))
        for line in lines:
            print(u" - {0}".format(line))
        print(u"")

    if DEBUG_LOG:
        print(u"Внутренние предупреждения (перехваченные исключения): {0}".format(len(DEBUG_LOG)))
        for line in DEBUG_LOG:
            print(u" - {0}".format(line))
        print(u"")


def get_mode_label(mode):
    if mode == SCOPE_ACTIVE_VIEW:
        return u"искать на текущем виде"

    if mode == SCOPE_MANUAL_SOURCE:
        return u"выбрать вручную источник"

    if mode == SCOPE_MANUAL_RECEIVER:
        return u"выбрать вручную приемник"

    return u"искать по всей модели"


def get_element_id_set(elements):
    result = set()

    for element in elements or []:
        try:
            result.add(element.Id.IntegerValue)
        except Exception as ex:
            log_debug(u"Чтение Id элемента", ex)

    return result


def read_chain_payload(element, param_name):
    param = get_lookup_parameter(element, param_name)
    if param is None:
        return None
    return get_parameter_payload_from_param(param)


def get_chain_options(config):
    result = []
    seen = set()

    for option in list(config.get("source_options", [])) + list(config.get("receiver_options", [])):
        if option["key"] not in seen:
            seen.add(option["key"])
            result.append(option)

    return result


def write_chain_report(report, param_name):
    output = script.get_output()
    output.print_md(u"### PP Заполнить разрывы по цепочке — отчет")
    print(u"Параметр: {0}".format(param_name))
    print(u"Всего элементов цепочки: {0}".format(report["elements"]))
    print(u"Пустых: {0}".format(report["empty"]))
    print(u"Разрывов (цепочек пустых): {0}".format(report["components"]))
    print(u"Заполнено: {0}".format(len(report["filled"])))
    print(u"Без изменений: {0}".format(report["unchanged"]))
    print(u"")

    sections = [
        (u"Конфликты", report["conflicts"]),
        (u"Без источника", report["no_source"]),
        (u"Концы веток (пропущено)", report["ends"]),
        (u"Ошибки записи", report["errors"]),
    ]

    for title, lines in sections:
        print(u"{0}: {1}".format(title, len(lines)))
        for line in lines:
            print(u" - {0}".format(line))
        print(u"")

    if DEBUG_LOG:
        print(u"Внутренние предупреждения: {0}".format(len(DEBUG_LOG)))
        for line in DEBUG_LOG:
            print(u" - {0}".format(line))
        print(u"")


def chain_fill(config):
    param_name = config["receiver_param"]
    chain_options = get_chain_options(config)
    chain_category_ids = get_category_ids(chain_options)
    elements = collect_elements(chain_options)

    if not elements:
        pp_wpf.show_report(
            u"Не найдено элементов выбранных категорий.",
            title=u"Готово",
            subtitle=TOOL_TITLE
        )
        return

    element_by_id = {}
    for element in elements:
        try:
            element_by_id[element.Id.IntegerValue] = element
        except Exception as ex:
            log_debug(u"Цепочка: Id элемента", ex)

    payloads = {}
    empty_ids = []
    element_items = list(element_by_id.items())
    total = len(element_items)

    # Быстрый проход: читаем только параметр, без запросов коннекторов.
    # Коннекторы (дорогая операция) запросим потом только у пустых элементов.
    with forms.ProgressBar(
        title=u"Чтение параметра ({value} из {max_value})",
        cancellable=True,
        step=1
    ) as pb:
        for index, pair in enumerate(element_items):
            eid = pair[0]
            payload = read_chain_payload(pair[1], param_name)
            payloads[eid] = payload
            if payload is None:
                empty_ids.append(eid)

            if index % 250 == 0:
                if pb.cancelled:
                    return
                pb.update_progress(index + 1, total)

    empty_set = set(empty_ids)

    report = {
        "elements": len(element_by_id),
        "empty": len(empty_ids),
        "components": 0,
        "filled": [],
        "unchanged": 0,
        "conflicts": [],
        "no_source": [],
        "ends": [],
        "errors": [],
    }

    visited = set()

    transaction = Transaction(doc, u"PP: Заполнить разрывы по цепочке")
    transaction.Start()

    try:
        for start in empty_ids:
            if start in visited:
                continue

            # Обходим цепочку пустых по коннекторам, попутно собирая границы.
            # Коннекторы запрашиваются только у пустых элементов — это быстро.
            component = []
            border = {}
            border_ids = set()
            stack = [start]
            visited.add(start)
            while stack:
                current = stack.pop()
                component.append(current)

                for neighbor in get_connected_source_elements(
                    element_by_id[current], chain_category_ids, None
                ):
                    neighbor_id = neighbor.Id.IntegerValue
                    if neighbor_id not in element_by_id:
                        continue

                    if neighbor_id in empty_set:
                        if neighbor_id not in visited:
                            visited.add(neighbor_id)
                            stack.append(neighbor_id)
                    else:
                        neighbor_payload = payloads.get(neighbor_id)
                        if neighbor_payload is not None:
                            border[get_parameter_key(neighbor_payload)] = neighbor_payload
                            border_ids.add(neighbor_id)

            report["components"] += 1

            if not border:
                report["no_source"].append(
                    u"Цепочка из {0} эл. (напр. ID {1}) — нет заполненного соседа".format(
                        len(component),
                        component[0]
                    )
                )
                continue

            # Заливаем только цепочки, зажатые между двумя (и более) разными
            # заполненными элементами. Концы веток (граница с одним элементом)
            # не трогаем.
            if len(border_ids) < 2:
                report["ends"].append(
                    u"Цепочка из {0} эл. (напр. ID {1}) — конец ветки (граница с 1 элементом), пропущено".format(
                        len(component),
                        component[0]
                    )
                )
                continue

            if len(border) > 1:
                values_text = u", ".join(sorted(
                    u"'{0}'".format(get_parameter_display_value(payload))
                    for payload in border.values()
                ))
                report["conflicts"].append(
                    u"Цепочка из {0} эл. (напр. ID {1}) — разные значения: {2}".format(
                        len(component),
                        component[0],
                        values_text
                    )
                )
                continue

            payload = list(border.values())[0]
            for cid in component:
                element = element_by_id[cid]
                param = get_lookup_parameter(element, param_name)
                success, status = set_parameter_from_payload(param, payload)

                if not success:
                    report["errors"].append(u"ID {0}: {1}".format(cid, status))
                elif status == u"unchanged":
                    report["unchanged"] += 1
                else:
                    report["filled"].append(cid)

        transaction.Commit()

    except Exception:
        if transaction.HasStarted():
            transaction.RollBack()
        raise

    show_pyrevit_report = should_show_pyrevit_report()
    if show_pyrevit_report:
        write_chain_report(report, param_name)

    summary_lines = [
        u"Заполнение разрывов по цепочке",
        u"Параметр: {0}".format(param_name),
        u"",
        u"Всего элементов цепочки: {0}".format(report["elements"]),
        u"Пустых: {0}".format(report["empty"]),
        u"Разрывов (цепочек пустых): {0}".format(report["components"]),
        u"Заполнено: {0}".format(len(report["filled"])),
        u"Конфликтов: {0}".format(len(report["conflicts"])),
        u"Без источника: {0}".format(len(report["no_source"])),
        u"Концов веток (пропущено): {0}".format(len(report["ends"])),
        u"Ошибок записи: {0}".format(len(report["errors"])),
    ]

    if show_pyrevit_report:
        summary_lines.append(u"")
        summary_lines.append(u"Подробный отчет выведен в окно pyRevit.")

    pp_wpf.show_report(
        u"\n".join(summary_lines),
        title=u"Готово",
        subtitle=TOOL_TITLE
    )


def run_transfer(config):
    source_options = config["source_options"]
    source_param = config["source_param"]
    receiver_param = config["receiver_param"]

    save_transfer_settings(config)

    if config.get("chain_fill"):
        chain_fill(config)
        return

    mode = config.get("mode")
    # Режимы, где мы отталкиваемся от источников: ручной выбор источника и
    # «на текущем виде» (берем трубы с вида и идем к их приемникам).
    is_source_driven = mode in (SCOPE_MANUAL_SOURCE, SCOPE_ACTIVE_VIEW)

    if is_source_driven:
        source_elements = collect_source_elements(config)

        if not source_elements:
            if mode == SCOPE_ACTIVE_VIEW:
                message = u"На текущем виде не найдено элементов-источников выбранных категорий."
            else:
                message = u"Элементы-источники не выбраны."
            pp_wpf.show_report(
                message,
                title=u"Готово",
                subtitle=TOOL_TITLE
            )
            return
    else:
        # В режимах «вся модель» и «вручную приемник» источники берутся
        # пофакту через коннекторы приемника, поэтому всю категорию не собираем.
        source_elements = []

    filter_config = config.get("filter")

    receiver_elements = get_receiver_elements(config, source_elements)

    receivers_before_filter = len(receiver_elements)
    if filter_targets_side(filter_config, "receiver"):
        receiver_elements = [
            receiver for receiver in receiver_elements
            if element_passes_filter(receiver, filter_config)
        ]
    filtered_receivers = receivers_before_filter - len(receiver_elements)

    if not receiver_elements:
        if filtered_receivers > 0:
            message = u"После применения фильтра не осталось подходящих приемников (отсеяно: {0}).".format(
                filtered_receivers
            )
        else:
            message = u"В выбранных категориях приемника не найдено элементов."

            if config.get("mode") == SCOPE_ACTIVE_VIEW:
                message = u"Для труб на текущем виде не найдено подключенных приемников выбранных категорий."
            elif config.get("mode") == SCOPE_MANUAL_SOURCE:
                message = u"Для выбранных источников не найдено подключенных приемников выбранных категорий."
            elif config.get("mode") == SCOPE_MANUAL_RECEIVER:
                message = u"Элементы-приемники не выбраны."

        pp_wpf.show_report(
            message,
            title=u"Готово",
            subtitle=TOOL_TITLE
        )
        return

    source_category_ids = get_category_ids(source_options)
    allowed_source_ids = get_element_id_set(source_elements) if is_source_driven else None

    # Реально подключенные источники, учтенные при обработке приемников.
    connected_source_ids = set()

    report = {
        "checked_count": len(receiver_elements),
        "filtered_receivers": filtered_receivers,
        "source_count": 0,
        "copied": [],
        "unchanged": [],
        "conflicts": [],
        "no_source": [],
        "missing_receiver_param": [],
        "errors": [],
    }

    transaction = Transaction(doc, u"PP: Передача параметров по MEP-соединениям")
    transaction.Start()

    try:
        with forms.ProgressBar(
            title=u"Передача параметров ({value} из {max_value})",
            cancellable=True,
            step=1
        ) as pb:
            for index, receiver in enumerate(receiver_elements):
                if pb.cancelled:
                    raise OperationCanceledException()

                connected_sources = get_connected_source_elements(
                    receiver,
                    source_category_ids,
                    allowed_source_ids
                )

                if filter_targets_side(filter_config, "source"):
                    connected_sources = [
                        source for source in connected_sources
                        if element_passes_filter(source, filter_config)
                    ]

                for connected_source in connected_sources:
                    connected_source_ids.add(connected_source.Id.IntegerValue)

                value_map = build_source_value_map(connected_sources, source_param)
                receiver_label = get_element_label(receiver)

                if not value_map:
                    report["no_source"].append(
                        u"{0} | нет подключенного источника с заполненным параметром '{1}'".format(
                            receiver_label,
                            source_param
                        )
                    )
                    pb.update_progress(index + 1, len(receiver_elements))
                    continue

                if len(value_map) > 1:
                    report["conflicts"].append(
                        u"{0} | найдено несколько значений: {1}".format(
                            receiver_label,
                            build_conflict_text(value_map)
                        )
                    )
                    pb.update_progress(index + 1, len(receiver_elements))
                    continue

                bucket = list(value_map.values())[0]
                payload = bucket["payload"]
                receiver_param_obj = get_lookup_parameter(receiver, receiver_param)

                if receiver_param_obj is None:
                    report["missing_receiver_param"].append(
                        u"{0} | параметр '{1}' не найден у экземпляра приемника".format(
                            receiver_label,
                            receiver_param
                        )
                    )
                    pb.update_progress(index + 1, len(receiver_elements))
                    continue

                success, status = set_parameter_from_payload(receiver_param_obj, payload)

                if not success:
                    report["errors"].append(
                        u"{0} | значение '{1}' не записано: {2}".format(
                            receiver_label,
                            get_parameter_display_value(payload),
                            status
                        )
                    )
                    pb.update_progress(index + 1, len(receiver_elements))
                    continue

                source_count = len(bucket["sources"])
                source_text = get_parameter_display_value(payload)

                if status == u"unchanged":
                    report["unchanged"].append(
                        u"{0} | значение '{1}' уже было заполнено | источников: {2}".format(
                            receiver_label,
                            source_text,
                            source_count
                        )
                    )
                else:
                    report["copied"].append(
                        u"{0} | записано '{1}' | источников: {2}".format(
                            receiver_label,
                            source_text,
                            source_count
                        )
                    )

                pb.update_progress(index + 1, len(receiver_elements))

        report["source_count"] = len(connected_source_ids)

        transaction.Commit()

    except OperationCanceledException:
        if transaction.HasStarted():
            transaction.RollBack()
        pp_wpf.show_report(
            u"Операция отменена. Изменения не сохранены.",
            title=u"Готово",
            subtitle=TOOL_TITLE
        )
        return

    except Exception:
        if transaction.HasStarted():
            transaction.RollBack()
        raise

    show_pyrevit_report = should_show_pyrevit_report()
    if show_pyrevit_report:
        write_report(report, config)

    summary_lines = [
        u"Проверено приемников: {0}".format(report["checked_count"]),
        u"Задействовано подключенных источников: {0}".format(report["source_count"]),
        u"Заполнено: {0}".format(len(report["copied"])),
        u"Без изменений: {0}".format(len(report["unchanged"])),
        u"Конфликтов: {0}".format(len(report["conflicts"])),
        u"Без источника: {0}".format(len(report["no_source"])),
        u"Нет параметра приемника: {0}".format(len(report["missing_receiver_param"])),
        u"Ошибок записи: {0}".format(len(report["errors"])),
    ]

    if config.get("mode") == SCOPE_ACTIVE_VIEW:
        try:
            active_view = doc.ActiveView
            view_name = unicode(active_view.Name)
            view_type = unicode(active_view.ViewType)
        except Exception as ex:
            log_debug(u"Имя активного вида", ex)
            view_name = u"?"
            view_type = u"?"
        summary_lines.append(u"")
        summary_lines.append(u"Активный вид: {0} ({1})".format(view_name, view_type))

    if filter_targets_side(config.get("filter"), "receiver"):
        summary_lines.insert(
            1,
            u"Отсеяно фильтром приемников: {0}".format(report["filtered_receivers"])
        )

    if DEBUG_LOG:
        summary_lines.append(
            u"Внутренних предупреждений: {0}".format(len(DEBUG_LOG))
        )

    if show_pyrevit_report:
        summary_lines.extend([
            u"",
            u"Подробный отчет выведен в окно pyRevit.",
        ])

    pp_wpf.show_report(
        u"\n".join(summary_lines),
        title=u"Готово",
        subtitle=TOOL_TITLE
    )


try:
    setup_data = show_setup_dialog()

    if setup_data is None:
        raise SystemExit

    run_transfer(setup_data)

except SystemExit:
    pass

except OperationCanceledException:
    pass

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )
