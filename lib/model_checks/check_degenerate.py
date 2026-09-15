# -*- coding: utf-8 -*-
"""Проверка «6. Вырожденная геометрия» (ключ degenerate_geometry).

Перенесено из pp_model_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

from model_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from model_checks.common import (
    CURVE_CATEGORY_OPTIONS,
    _element_label,
    _element_length_mm,
    _parse_float,
    _pipe_system_name,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Вырожденная геометрия» (длина ≈ 0).
    "degenerate_tolerance_mm": u"1",
}


# Все осевые категории для «Вырожденной геометрии» (без настройки категорий).
DEGENERATE_CATEGORIES = [
    built_in for _key, _label, built_in in CURVE_CATEGORY_OPTIONS
]


def _run_degenerate_check(doc, config, cache):
    tol_mm = _parse_float(config.get("degenerate_tolerance_mm"), 1.0)
    if tol_mm < 0:
        tol_mm = 1.0

    elements = cache.get_by_categories(DEGENERATE_CATEGORIES)
    issues = []
    checked = 0

    for element in elements:
        length = _element_length_mm(element)
        if length is None:
            continue
        checked += 1
        if length <= tol_mm:
            try:
                eid = element.Id.IntegerValue
            except:
                continue
            system_name = _pipe_system_name(element)
            issues.append(CheckIssue(
                u"Вырожденная (длина ~{0} мм) — {1}".format(
                    _format_length(length), _element_label(element, system_name)),
                [eid]))

    return CheckResult(u"degenerate_geometry", u"6. Вырожденная геометрия",
                       checked, issues)


def _format_length(length):
    try:
        text = u"{0:.2f}".format(length)
    except:
        return unicode(length)
    if u"." in text:
        text = text.rstrip(u"0").rstrip(u".")
    return text


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "degenerate_tolerance_mm",
            u"Порог вырожденной длины, мм (длина ≤ порога)",
            ["degenerate_geometry"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "degenerate_geometry",
        u"6. Вырожденная геометрия",
        u"Показывает трубы и воздуховоды с длиной практически ноль (меньше "
        u"или равно порогу, по умолчанию 1 мм). Такие элементы Revit иногда "
        u"допускает при ошибочном редактировании; они не видны, ломают "
        u"соединения и подсчёты, их нужно удалить. Порог задаётся в мм.",
        runner=_run_degenerate_check,
        option_keys=["degenerate_tolerance_mm"],
        kind=u"report",
    )
