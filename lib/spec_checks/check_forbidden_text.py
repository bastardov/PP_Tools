# -*- coding: utf-8 -*-
"""Проверка «12. Запрещенные фразы в наименовании и марке» (ключ forbidden_text).

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
    SORT_ORDER_CATEGORY_OPTIONS,
    _get_element_label,
    _get_parameter_text,
    _normalize_space,
    _parse_category_keys,
    _resolve_categories,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "badtext_params": u"ADSK_Наименование, ADSK_Марка",
    "badtext_phrases": u"Не найдено\nНет в каталоге",
    "badtext_categories": u", ".join([
        key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
    ]),
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _parse_lines(raw_text):
    result = []
    for line in re.split(r"[\r\n]+", unicode(raw_text or u"")):
        line = _normalize_space(line)
        if line:
            result.append(line)
    return result


def _run_forbidden_text_check(doc, config, cache):
    title = u"12. Запрещенные фразы в наименовании и марке"

    param_names = _parse_category_keys(
        config.get("badtext_params", DEFAULT_SPEC_CONFIG["badtext_params"])
    )
    phrases = _parse_lines(
        config.get("badtext_phrases", DEFAULT_SPEC_CONFIG["badtext_phrases"])
    )
    category_keys = _parse_category_keys(
        config.get("badtext_categories", DEFAULT_SPEC_CONFIG["badtext_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories or not param_names or not phrases:
        return CheckResult("forbidden_text", title, 0, [])

    phrase_pairs = [(phrase, phrase.lower()) for phrase in phrases]
    elements = cache.get_by_categories(categories)
    issues = []

    for element in elements:
        for param_name in param_names:
            value = _get_parameter_text(element, [param_name])
            if not value:
                continue

            value_lower = value.lower()
            found = None
            for original, lowered in phrase_pairs:
                if lowered and lowered in value_lower:
                    found = original
                    break

            if found is not None:
                issues.append(CheckIssue(
                    u"{0} | {1}: найдена фраза '{2}' | значение '{3}'".format(
                        _get_element_label(doc, element),
                        param_name,
                        found,
                        value
                    ),
                    [element.Id.IntegerValue]
                ))

    return CheckResult("forbidden_text", title, len(elements), issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "badtext_params",
            u"Параметры для проверки (через запятую)",
            ["forbidden_text"]
        ),
        CheckOptionDefinition(
            "badtext_phrases",
            u"Запрещенные фразы (по одной на строку)",
            ["forbidden_text"],
            option_type=u"multiline"
        ),
        CheckOptionDefinition(
            "badtext_categories",
            u"Категории для проверки",
            ["forbidden_text"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "forbidden_text",
        u"12. Запрещенные фразы в наименовании и марке",
        u"Ищет элементы, у которых в выбранных параметрах (по умолчанию "
        u"ADSK_Наименование и ADSK_Марка) встречается одна из запрещенных фраз.",
        _run_forbidden_text_check,
        option_keys=[
            "badtext_params",
            "badtext_phrases",
            "badtext_categories"
        ]
    )
