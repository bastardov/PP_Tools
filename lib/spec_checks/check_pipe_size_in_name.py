# -*- coding: utf-8 -*-
"""Проверка «10. Размер в ADSK_Наименование на ОТОПЛЕНИЕ» (ключ pipe_size_in_name).

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
    _get_type_name,
    _normalize_space,
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
    "pipe_size_name_param": u"ADSK_Наименование",
    "pipe_size_categories": u"OST_PipeCurves",
    "pipe_gost1_keyword": u"3262-75",
    "pipe_gost1_size_param": u"Диаметр",
    "pipe_gost2_keyword": u"10704-91",
    "pipe_gost2_size_param": u"Внешний диаметр",
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


def _run_pipe_size_in_name_check(doc, config, cache):
    title = u"10. Размер в ADSK_Наименование на ОТОПЛЕНИЕ"

    name_param = config.get("pipe_size_name_param", DEFAULT_SPEC_CONFIG["pipe_size_name_param"])
    category_keys = _parse_category_keys(
        config.get("pipe_size_categories", DEFAULT_SPEC_CONFIG["pipe_size_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult("pipe_size_in_name", title, 0, [])

    # Правила «текст в имени типа -> параметр размера». Тип трубы определяется
    # по вхождению текста в имя типа. Два ГОСТа — два разных параметра.
    rules = []
    for keyword_key, size_key in [
        ("pipe_gost1_keyword", "pipe_gost1_size_param"),
        ("pipe_gost2_keyword", "pipe_gost2_size_param"),
    ]:
        keyword = _normalize_space(config.get(keyword_key, DEFAULT_SPEC_CONFIG[keyword_key])).lower()
        size_param = config.get(size_key, DEFAULT_SPEC_CONFIG[size_key])
        if keyword:
            rules.append((keyword, size_param))

    elements = cache.get_by_categories(categories)
    issues = []
    checked_count = 0

    for element in elements:
        type_name = _get_type_name(doc, element).lower()

        matched = None
        for keyword, size_param in rules:
            if keyword in type_name:
                matched = (keyword, size_param)
                break

        # Тип трубы не распознан ни одним правилом — пропускаем.
        if matched is None:
            continue

        keyword, size_param = matched
        size_text = _get_size_text(element, [size_param])

        if not size_text:
            continue

        checked_count += 1
        name_text = _get_parameter_text(element, [name_param])

        if not name_text:
            issues.append(CheckIssue(
                u"{0} | тип '{1}' | наименование пустое, размер '{2}'".format(
                    _get_element_label(doc, element),
                    keyword,
                    size_text
                ),
                [element.Id.IntegerValue]
            ))
            continue

        if not _size_matches_name(_normalize_size(size_text), _normalize_size(name_text)):
            issues.append(CheckIssue(
                u"{0} | тип '{1}' | размер '{2}' не найден в наименовании: '{3}'".format(
                    _get_element_label(doc, element),
                    keyword,
                    size_text,
                    name_text
                ),
                [element.Id.IntegerValue]
            ))

    return CheckResult("pipe_size_in_name", title, checked_count, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "pipe_size_name_param",
            u"Параметр наименования",
            ["pipe_size_in_name"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "pipe_size_categories",
            u"Категории для проверки",
            ["pipe_size_in_name"],
            option_type=u"category_multiselect",
            choices=[
                (u"OST_PipeCurves", u"Трубы"),
            ]
        ),
        CheckOptionDefinition(
            "pipe_gost1_keyword",
            u"Тип трубы 1: текст в имени типа",
            ["pipe_size_in_name"]
        ),
        CheckOptionDefinition(
            "pipe_gost1_size_param",
            u"Тип трубы 1: параметр размера",
            ["pipe_size_in_name"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "pipe_gost2_keyword",
            u"Тип трубы 2: текст в имени типа",
            ["pipe_size_in_name"]
        ),
        CheckOptionDefinition(
            "pipe_gost2_size_param",
            u"Тип трубы 2: параметр размера",
            ["pipe_size_in_name"],
            option_type=u"param"
        ),
    ]


def get_definition():
    return CheckDefinition(
        "pipe_size_in_name",
        u"10. Размер в ADSK_Наименование на ОТОПЛЕНИЕ",
        u"Проверяет совпадение размера трубы с ADSK_Наименование. Тип трубы "
        u"определяется по имени типа: для «3262-75» берется «Диаметр», для "
        u"«10704-91» — «Внешний диаметр». Отчет общий по обоим типам.",
        _run_pipe_size_in_name_check,
        option_keys=[
            "pipe_size_name_param",
            "pipe_size_categories",
            "pipe_gost1_keyword",
            "pipe_gost1_size_param",
            "pipe_gost2_keyword",
            "pipe_gost2_size_param"
        ]
    )
