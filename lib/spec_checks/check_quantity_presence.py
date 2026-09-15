# -*- coding: utf-8 -*-
"""Проверка «2. Пустое или нулевое ADSK_количество» (ключ quantity_presence).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    PIPE_AND_DUCT_CATEGORIES,
    _get_element_label,
    _get_number_param,
    _get_parameter_text,
    _is_zero_number,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "quantity_param": u"ADSK_количество",
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _run_quantity_presence_check(doc, config, cache):
    elements = cache.get_by_categories(PIPE_AND_DUCT_CATEGORIES)
    param_name = config.get("quantity_param", DEFAULT_SPEC_CONFIG["quantity_param"])
    param_names = [param_name, u"ADSK_Количество"]
    issues = []

    for element in elements:
        text_value = _get_parameter_text(element, param_names)

        if not text_value:
            issues.append(CheckIssue(
                u"{0} | параметр количества пустой".format(_get_element_label(doc, element)),
                [element.Id.IntegerValue]
            ))
            continue

        numeric_value = _get_number_param(element, param_names)

        if _is_zero_number(numeric_value):
            issues.append(CheckIssue(
                u"{0} | количество равно 0".format(_get_element_label(doc, element)),
                [element.Id.IntegerValue]
            ))

    return CheckResult(
        "quantity_presence",
        u"2. Пустое или нулевое ADSK_количество",
        len(elements),
        issues
    )


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "quantity_param",
            u"Параметр для проверки",
            ["quantity_presence"],
            option_type=u"param"
        ),
    ]


def get_definition():
    return CheckDefinition(
        "quantity_presence",
        u"2. Пустое или нулевое ADSK_количество",
        u"Ищет трубы и воздуховоды с пустым или нулевым параметром количества.",
        _run_quantity_presence_check,
        option_keys=["quantity_param"]
    )
