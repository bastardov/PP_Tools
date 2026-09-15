# -*- coding: utf-8 -*-
"""Проверка «9. Размер в ADSK_Наименование на ВЕНТИЛЯЦИИ» (ключ size_in_name).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    _get_element_label,
    _get_parameter_text,
    _parse_category_keys,
    _resolve_categories,
)

from spec_checks.common_size import (
    _get_size_text,
    _normalize_size,
    _size_matches_name,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "size_name_name_param": u"ADSK_Наименование",
    "size_name_size_param": u"Размер",
    "size_name_categories": u"OST_DuctCurves",
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _run_size_in_name_check(doc, config, cache):
    title = u"9. Размер в ADSK_Наименование на ВЕНТИЛЯЦИИ"

    size_param = config.get("size_name_size_param", DEFAULT_SPEC_CONFIG["size_name_size_param"])
    name_param = config.get("size_name_name_param", DEFAULT_SPEC_CONFIG["size_name_name_param"])
    category_keys = _parse_category_keys(
        config.get("size_name_categories", DEFAULT_SPEC_CONFIG["size_name_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult("size_in_name", title, 0, [])

    elements = cache.get_by_categories(categories)
    issues = []
    checked_count = 0

    for element in elements:
        size_text = _get_size_text(element, [size_param])

        # Нет размера — сравнивать нечего (пустые размеры ловят другие проверки).
        if not size_text:
            continue

        checked_count += 1
        name_text = _get_parameter_text(element, [name_param])

        if not name_text:
            issues.append(CheckIssue(
                u"{0} | наименование пустое, размер '{1}'".format(
                    _get_element_label(doc, element),
                    size_text
                ),
                [element.Id.IntegerValue]
            ))
            continue

        if not _size_matches_name(_normalize_size(size_text), _normalize_size(name_text)):
            issues.append(CheckIssue(
                u"{0} | размер '{1}' не найден в наименовании: '{2}'".format(
                    _get_element_label(doc, element),
                    size_text,
                    name_text
                ),
                [element.Id.IntegerValue]
            ))

    return CheckResult("size_in_name", title, checked_count, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "size_name_name_param",
            u"Параметр наименования",
            ["size_in_name"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "size_name_size_param",
            u"Параметр размера",
            ["size_in_name"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "size_name_categories",
            u"Категории для проверки",
            ["size_in_name"],
            option_type=u"category_multiselect",
            choices=[
                (u"OST_DuctCurves", u"Воздуховоды"),
                (u"OST_DuctFitting", u"Соединительные детали воздуховодов"),
            ]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "size_in_name",
        u"9. Размер в ADSK_Наименование на ВЕНТИЛЯЦИИ",
        u"Проверяет, что значение параметра «Размер» присутствует в "
        u"ADSK_Наименование (после сборки имени размер должен совпадать).",
        _run_size_in_name_check,
        option_keys=[
            "size_name_name_param",
            "size_name_size_param",
            "size_name_categories"
        ]
    )
