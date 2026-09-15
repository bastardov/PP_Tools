# -*- coding: utf-8 -*-
"""Проверка «4. Пустая или нулевая толщина металла» (ключ duct_metal_thickness).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
)

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    _get_element_label,
    _get_length_param_mm,
    _get_parameter_text,
    _is_zero_number,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "metal_thickness_param": u"ADSK_Толщина металла",
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


DUCT_ONLY_CATEGORIES = [
    BuiltInCategory.OST_DuctCurves,
]


def _run_duct_metal_thickness_check(doc, config, cache):
    elements = cache.get_by_categories(DUCT_ONLY_CATEGORIES)
    param_name = config.get("metal_thickness_param", DEFAULT_SPEC_CONFIG["metal_thickness_param"])
    issues = []

    for element in elements:
        text_value = _get_parameter_text(element, [param_name])
        numeric_value = _get_length_param_mm(element, [param_name])

        if not text_value:
            issues.append(CheckIssue(
                u"{0} | толщина металла не заполнена".format(_get_element_label(doc, element)),
                [element.Id.IntegerValue]
            ))
            continue

        if _is_zero_number(numeric_value):
            issues.append(CheckIssue(
                u"{0} | толщина металла равна 0".format(_get_element_label(doc, element)),
                [element.Id.IntegerValue]
            ))

    return CheckResult(
        "duct_metal_thickness",
        u"4. Пустая или нулевая толщина металла",
        len(elements),
        issues
    )


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "metal_thickness_param",
            u"Параметр для проверки",
            ["duct_metal_thickness"],
            option_type=u"param"
        ),
    ]


def get_definition():
    return CheckDefinition(
        "duct_metal_thickness",
        u"4. Пустая или нулевая толщина металла",
        u"Ищет воздуховоды с пустым или нулевым параметром ADSK_Толщина металла.",
        _run_duct_metal_thickness_check,
        option_keys=["metal_thickness_param"]
    )
