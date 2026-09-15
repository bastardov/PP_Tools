# -*- coding: utf-8 -*-
"""Проверка «14. Вложенные семейства (отопление)» (ключ nested_heat).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    SORT_ORDER_CATEGORY_OPTIONS,
    _get_element_label,
    _get_parameter_text,
    _normalize_space,
    _parse_category_keys,
    _resolve_categories,
)

from spec_checks.common_nested import (
    _get_super_component,
    _show_value,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "nested_heat_param": u"ADSK_Система_Сокращение",
    "nested_heat_categories": u", ".join([
        key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
    ]),
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _run_nested_heat_check(doc, config, cache):
    title = u"14. Вложенные семейства (отопление)"

    param = config.get("nested_heat_param", DEFAULT_SPEC_CONFIG["nested_heat_param"])
    category_keys = _parse_category_keys(
        config.get("nested_heat_categories", DEFAULT_SPEC_CONFIG["nested_heat_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult("nested_heat", title, 0, [])

    elements = cache.get_by_categories(categories)
    issues = []
    checked_count = 0

    for element in elements:
        parent = _get_super_component(element)
        if parent is None:
            continue

        checked_count += 1
        child_value = _normalize_space(_get_parameter_text(element, [param]))
        parent_value = _normalize_space(_get_parameter_text(parent, [param]))

        if child_value != parent_value:
            issues.append(CheckIssue(
                u"{0} | {1}: вложенное '{2}' ≠ родитель '{3}'".format(
                    _get_element_label(doc, element),
                    param,
                    _show_value(child_value),
                    _show_value(parent_value)
                ),
                [element.Id.IntegerValue, parent.Id.IntegerValue]
            ))

    return CheckResult("nested_heat", title, checked_count, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "nested_heat_param",
            u"Параметр для показа",
            ["nested_heat"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "nested_heat_categories",
            u"Категории для проверки",
            ["nested_heat"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "nested_heat",
        u"14. Вложенные семейства (отопление)",
        u"Ищет вложенные экземпляры (подкомпоненты), у которых "
        u"ADSK_Система_Сокращение не совпадает с родительским семейством.",
        _run_nested_heat_check,
        option_keys=[
            "nested_heat_param",
            "nested_heat_categories"
        ]
    )
