# -*- coding: utf-8 -*-
"""Проверка «8. Совпадение имени по MEP-соединениям» (ключ mep_name_match).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import pp_mep_filter

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    _get_element_label,
    _get_parameter_text,
    _get_parameter_text_from_element_or_type,
    _normalize_space,
    _parse_category_keys,
    _resolve_categories,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "mep_source_categories": u"OST_PipeCurves",
    "mep_source_param": u"ADSK_Имя системы сокращенное",
    "mep_receiver_categories": u"OST_PipeAccessory",
    "mep_receiver_param": u"ADSK_Имя системы сокращенное",
    "mep_filter": {
        "enabled": False,
        "match": "all",
        "group_combine": "and",
        "apply_source": True,
        "apply_receiver": False,
        "conditions": [],
    },
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


# Категории для стратегии сверки по MEP (как в инструменте «Передача по MEP»).
# Ключи совпадают с SORT_ORDER_CATEGORY_MAP, поэтому маппинг общий.
MEP_TRANSFER_CATEGORY_OPTIONS = [
    (u"OST_PipeCurves", u"Трубы"),
    (u"OST_PipeFitting", u"Соединительные детали трубопроводов"),
    (u"OST_PipeAccessory", u"Арматура трубопроводов"),
    (u"OST_MechanicalEquipment", u"Оборудование"),
]


def _get_connectors(element):
    connectors = []

    try:
        manager = element.ConnectorManager
        if manager is not None:
            for connector in manager.Connectors:
                connectors.append(connector)
    except:
        # У экземпляров (оборудование/фитинги) нет ConnectorManager напрямую.
        pass

    try:
        mep_model = element.MEPModel
        if mep_model is not None and mep_model.ConnectorManager is not None:
            for connector in mep_model.ConnectorManager.Connectors:
                connectors.append(connector)
    except:
        # У труб/воздуховодов нет MEPModel.
        pass

    return connectors


def _element_category_id(element):
    try:
        category = element.Category
        if category is not None:
            return category.Id.IntegerValue
    except:
        pass

    return None


def _get_connected_elements(element, allowed_category_ids):
    result = []
    seen = set()

    try:
        self_id = element.Id.IntegerValue
    except:
        return result

    for connector in _get_connectors(element):
        try:
            refs = connector.AllRefs
        except:
            refs = []

        for ref_connector in refs:
            try:
                owner = ref_connector.Owner
            except:
                owner = None

            if owner is None:
                continue

            try:
                owner_id = owner.Id.IntegerValue
            except:
                continue

            if owner_id == self_id or owner_id in seen:
                continue

            if _element_category_id(owner) not in allowed_category_ids:
                continue

            seen.add(owner_id)
            result.append(owner)

    return result


def _run_mep_name_match_check(doc, config, cache):
    title = u"8. Совпадение имени по MEP-соединениям"

    source_keys = _parse_category_keys(
        config.get("mep_source_categories", DEFAULT_SPEC_CONFIG["mep_source_categories"])
    )
    receiver_keys = _parse_category_keys(
        config.get("mep_receiver_categories", DEFAULT_SPEC_CONFIG["mep_receiver_categories"])
    )
    source_param = config.get("mep_source_param", DEFAULT_SPEC_CONFIG["mep_source_param"])
    receiver_param = config.get("mep_receiver_param", DEFAULT_SPEC_CONFIG["mep_receiver_param"])

    source_categories = _resolve_categories(source_keys)
    receiver_categories = _resolve_categories(receiver_keys)

    if not source_categories or not receiver_categories:
        return CheckResult("mep_name_match", title, 0, [])

    filter_config = pp_mep_filter.normalize_filter_config(config.get("mep_filter"))
    apply_source_filter = pp_mep_filter.filter_targets_side(filter_config, "source")
    apply_receiver_filter = pp_mep_filter.filter_targets_side(filter_config, "receiver")

    allowed_source_ids = set()
    for built_in in source_categories:
        try:
            allowed_source_ids.add(int(built_in))
        except:
            pass

    receivers = cache.get_by_categories(receiver_categories)

    if apply_receiver_filter:
        receivers = [
            receiver for receiver in receivers
            if pp_mep_filter.element_passes_filter(doc, receiver, filter_config)
        ]

    issues = []
    checked_count = 0

    for receiver in receivers:
        connected_sources = _get_connected_elements(receiver, allowed_source_ids)

        if apply_source_filter:
            connected_sources = [
                source for source in connected_sources
                if pp_mep_filter.element_passes_filter(doc, source, filter_config)
            ]

        if not connected_sources:
            continue

        source_values = []
        for source in connected_sources:
            value = _normalize_space(
                _get_parameter_text_from_element_or_type(doc, source, [source_param])
            )
            if value and value not in source_values:
                source_values.append(value)

        if not source_values:
            # Подключенные источники есть, но параметр у них пустой -
            # сверять нечего, это задача других проверок.
            continue

        checked_count += 1
        receiver_value = _normalize_space(_get_parameter_text(receiver, [receiver_param]))
        source_text = u", ".join(sorted(source_values))

        if not receiver_value:
            issues.append(CheckIssue(
                u"{0} | значение потеряно, у источника: {1}".format(
                    _get_element_label(doc, receiver),
                    source_text
                ),
                [receiver.Id.IntegerValue]
            ))
            continue

        if receiver_value not in source_values:
            issues.append(CheckIssue(
                u"{0} | приемник '{1}' не совпадает с источником: {2}".format(
                    _get_element_label(doc, receiver),
                    receiver_value,
                    source_text
                ),
                [receiver.Id.IntegerValue]
            ))

    return CheckResult("mep_name_match", title, checked_count, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "mep_source_categories",
            u"Категории источника",
            ["mep_name_match"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label in MEP_TRANSFER_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "mep_source_param",
            u"Параметр источника",
            ["mep_name_match"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "mep_receiver_categories",
            u"Категории приемника",
            ["mep_name_match"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label in MEP_TRANSFER_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "mep_receiver_param",
            u"Параметр приемника",
            ["mep_name_match"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "mep_filter",
            u"Фильтр элементов",
            ["mep_name_match"],
            option_type=u"mep_filter"
        ),
    ]


def get_definition():
    return CheckDefinition(
        "mep_name_match",
        u"8. Совпадение имени по MEP-соединениям",
        u"Сверяет параметр приемника со значением подключенных источников "
        u"(проверка после «Передача по MEP»): ищет потерянные или несовпадающие значения.",
        _run_mep_name_match_check,
        option_keys=[
            "mep_source_categories",
            "mep_source_param",
            "mep_receiver_categories",
            "mep_receiver_param",
            "mep_filter"
        ]
    )
