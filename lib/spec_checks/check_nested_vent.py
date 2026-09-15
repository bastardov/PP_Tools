# -*- coding: utf-8 -*-
"""Проверка «15. Вложенные семейства (вентиляция)» (ключ nested_vent).

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
    "nested_vent_param1": u"Имя системы",
    "nested_vent_param2": u"ADSK_Группирование",
    "nested_vent_categories": u", ".join([
        key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
    ]),
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _run_nested_vent_check(doc, config, cache):
    title = u"15. Вложенные семейства (вентиляция)"

    param1 = config.get("nested_vent_param1", DEFAULT_SPEC_CONFIG["nested_vent_param1"])
    param2 = config.get("nested_vent_param2", DEFAULT_SPEC_CONFIG["nested_vent_param2"])
    category_keys = _parse_category_keys(
        config.get("nested_vent_categories", DEFAULT_SPEC_CONFIG["nested_vent_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult("nested_vent", title, 0, [])

    elements = cache.get_by_categories(categories)
    issues = []
    checked_count = 0

    for element in elements:
        parent = _get_super_component(element)
        if parent is None:
            continue

        checked_count += 1
        mismatches = []

        for param in (param1, param2):
            child_value = _normalize_space(_get_parameter_text(element, [param]))
            parent_value = _normalize_space(_get_parameter_text(parent, [param]))
            if child_value != parent_value:
                mismatches.append(u"{0}: вложенное '{1}' ≠ родитель '{2}'".format(
                    param,
                    _show_value(child_value),
                    _show_value(parent_value)
                ))

        if mismatches:
            issues.append(CheckIssue(
                u"{0} | {1}".format(
                    _get_element_label(doc, element),
                    u" | ".join(mismatches)
                ),
                [element.Id.IntegerValue, parent.Id.IntegerValue]
            ))

    return CheckResult("nested_vent", title, checked_count, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "nested_vent_param1",
            u"Параметр 1 для показа",
            ["nested_vent"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "nested_vent_param2",
            u"Параметр 2 для показа",
            ["nested_vent"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "nested_vent_categories",
            u"Категории для проверки",
            ["nested_vent"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "nested_vent",
        u"15. Вложенные семейства (вентиляция)",
        u"Ищет вложенные экземпляры (подкомпоненты), у которых «Имя системы» "
        u"или ADSK_Группирование не совпадают с родительским семейством.",
        _run_nested_vent_check,
        option_keys=[
            "nested_vent_param1",
            "nested_vent_param2",
            "nested_vent_categories"
        ]
    )
