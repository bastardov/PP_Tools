# -*- coding: utf-8 -*-
"""Проверка «6. Имя системы и ADSK_Группирование» (ключ system_grouping).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr
import re

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
)

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    _get_element_label,
    _get_parameter_text,
    _get_parameter_text_from_param,
    _normalize_space,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "grouping_param": u"ADSK_Группирование",
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


SYSTEM_GROUP_CATEGORIES = [
    BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_DuctAccessory,
    BuiltInCategory.OST_DuctInsulations,
    BuiltInCategory.OST_DuctFitting,
]


def _get_system_names(element):
    result = []

    direct_texts = [
        _get_parameter_text(element, u"Имя системы"),
        _get_parameter_text(element, u"Система"),
    ]

    for text in direct_texts:
        if text:
            result.append(text)

    try:
        param = element.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
        text = _get_parameter_text_from_param(param)
        if text:
            result.append(text)
    except:
        pass

    try:
        mep_system = element.MEPSystem
        if mep_system is not None and mep_system.Name:
            result.append(unicode(mep_system.Name))
    except:
        pass

    try:
        connector_manager = element.ConnectorManager
    except:
        connector_manager = None

    if connector_manager is not None:
        try:
            connectors = connector_manager.Connectors
        except:
            connectors = []

        try:
            for connector in connectors:
                try:
                    mep_system = connector.MEPSystem
                    if mep_system is not None and mep_system.Name:
                        result.append(unicode(mep_system.Name))
                except:
                    pass
        except:
            pass

    normalized = []
    seen = set()

    for raw_text in result:
        if not raw_text:
            continue
        for token in re.split(r"[;,/]+", unicode(raw_text)):
            cleaned = _normalize_space(token)
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)

    return normalized


def _run_system_grouping_check(doc, config, cache):
    elements = cache.get_by_categories(SYSTEM_GROUP_CATEGORIES)
    grouping_param = config.get("grouping_param", DEFAULT_SPEC_CONFIG["grouping_param"])
    issues = []

    for element in elements:
        grouping_value = _normalize_space(_get_parameter_text(element, [grouping_param]))
        system_names = _get_system_names(element)

        if not system_names and not grouping_value:
            continue

        if not system_names:
            issues.append(CheckIssue(
                u"{0} | имя системы пустое, ADSK_Группирование = '{1}'".format(
                    _get_element_label(doc, element),
                    grouping_value
                ),
                [element.Id.IntegerValue]
            ))
            continue

        if not grouping_value:
            issues.append(CheckIssue(
                u"{0} | ADSK_Группирование пустое, имя системы = '{1}'".format(
                    _get_element_label(doc, element),
                    u", ".join(system_names)
                ),
                [element.Id.IntegerValue]
            ))
            continue

        grouping_key = grouping_value.lower()
        system_keys = [system_name.lower() for system_name in system_names]

        if grouping_key not in system_keys:
            issues.append(CheckIssue(
                u"{0} | имя системы: '{1}' | ADSK_Группирование: '{2}'".format(
                    _get_element_label(doc, element),
                    u", ".join(system_names),
                    grouping_value
                ),
                [element.Id.IntegerValue]
            ))

    return CheckResult(
        "system_grouping",
        u"6. Имя системы и ADSK_Группирование",
        len(elements),
        issues
    )


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "grouping_param",
            u"Параметр группирования",
            ["system_grouping"],
            option_type=u"param"
        ),
    ]


def get_definition():
    return CheckDefinition(
        "system_grouping",
        u"6. Имя системы и ADSK_Группирование",
        u"Сравнивает имя системы и ADSK_Группирование у воздуховодов и связанных категорий.",
        _run_system_grouping_check,
        option_keys=["grouping_param"]
    )
