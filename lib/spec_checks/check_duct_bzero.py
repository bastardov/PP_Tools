# -*- coding: utf-8 -*-
"""Проверка «3. Запрещенные тексты в наименовании» (ключ duct_bzero).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import re

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    _get_element_label,
    _get_parameter_text,
    _normalize_space,
    _parse_category_keys,
    _resolve_categories,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "bzero_name_param": u"ADSK_Наименование",
    "bzero_text": u"b=0 мм; класс герметичности ,",
    "bzero_categories": u"OST_DuctCurves, OST_DuctFitting",
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _normalize_search_text(text):
    text = _normalize_space(text).lower()
    if not text:
        return u""
    return re.sub(r"\s+([,.;:])", r"\1", text)


def _parse_search_text_list(raw_text):
    result = []
    seen = set()
    parts = re.split(r"[;\n]+", unicode(raw_text or u""))

    for part in parts:
        display_text = _normalize_space(part)
        search_text = _normalize_search_text(display_text)

        if not search_text or search_text in seen:
            continue

        seen.add(search_text)
        result.append((display_text, search_text))

    return result


def _upgrade_bzero_search_text(raw_text):
    normalized = _normalize_search_text(raw_text)

    if normalized in [u"b=0", u"b=0 мм"]:
        return DEFAULT_SPEC_CONFIG["bzero_text"]

    return raw_text


def _run_duct_bzero_check(doc, config, cache):
    category_keys = _parse_category_keys(
        config.get("bzero_categories", DEFAULT_SPEC_CONFIG["bzero_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult(
            "duct_bzero",
            u"3. Запрещенные тексты в наименовании",
            0,
            []
        )

    elements = cache.get_by_categories(categories)
    param_name = config.get("bzero_name_param", DEFAULT_SPEC_CONFIG["bzero_name_param"])
    raw_search_text = _upgrade_bzero_search_text(
        config.get("bzero_text", DEFAULT_SPEC_CONFIG["bzero_text"])
    )
    search_texts = _parse_search_text_list(raw_search_text)
    issues = []

    for element in elements:
        value = _get_parameter_text(element, [param_name])
        if not value:
            continue

        normalized_value = _normalize_search_text(value)

        for display_text, search_text in search_texts:
            if not search_text or search_text not in normalized_value:
                continue

            issues.append(CheckIssue(
                u"{0} | найден текст '{1}'".format(
                    _get_element_label(doc, element),
                    display_text
                ),
                [element.Id.IntegerValue]
            ))
            break

    return CheckResult(
        "duct_bzero",
        u"3. Запрещенные тексты в наименовании",
        len(elements),
        issues
    )


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "bzero_name_param",
            u"Параметр для проверки",
            ["duct_bzero"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "bzero_text",
            u"Тексты для поиска через ; (логика ИЛИ)",
            ["duct_bzero"]
        ),
        CheckOptionDefinition(
            "bzero_categories",
            u"Категории для проверки",
            ["duct_bzero"],
            option_type=u"category_multiselect",
            choices=[
                (u"OST_DuctCurves", u"Воздуховоды"),
                (u"OST_DuctFitting", u"Соединительные детали воздуховодов"),
            ]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "duct_bzero",
        u"3. Запрещенные тексты в наименовании",
        u"Ищет любой фрагмент из списка. Несколько значений пишите через ; "
        u"например: b=0 мм; класс герметичности ,",
        _run_duct_bzero_check,
        option_keys=["bzero_name_param", "bzero_text", "bzero_categories"]
    )
