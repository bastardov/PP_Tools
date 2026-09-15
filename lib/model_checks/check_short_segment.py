# -*- coding: utf-8 -*-
"""Проверка «5. Короткие обрезки» (ключ short_segment).

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
    _parse_category_map,
    _parse_float,
    _pipe_system_name,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Короткие обрезки».
    "short_segment_mm": u"100",
    "short_segment_categories": u"OST_PipeCurves, OST_DuctCurves",
}


CURVE_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in CURVE_CATEGORY_OPTIONS
}


SHORT_DEFAULT_KEYS = [u"OST_PipeCurves", u"OST_DuctCurves"]


LENGTH_EPS_MM = 1e-4


def _run_short_segment_check(doc, config, cache):
    threshold = _parse_float(config.get("short_segment_mm"), 100.0)
    if threshold <= 0:
        threshold = 100.0
    categories = _parse_category_map(
        config.get("short_segment_categories"),
        CURVE_CATEGORY_MAP, SHORT_DEFAULT_KEYS)

    elements = cache.get_by_categories(categories)
    issues = []
    checked = 0

    for element in elements:
        length = _element_length_mm(element)
        if length is None:
            continue
        checked += 1
        if LENGTH_EPS_MM < length < threshold:
            try:
                eid = element.Id.IntegerValue
            except:
                continue
            system_name = _pipe_system_name(element)
            issues.append(CheckIssue(
                u"Длина {0} мм — {1}".format(
                    int(round(length)), _element_label(element, system_name)),
                [eid]))

    return CheckResult(u"short_segment", u"5. Короткие обрезки", checked, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "short_segment_mm",
            u"Порог короткого участка, мм",
            ["short_segment"]
        ),
        CheckOptionDefinition(
            "short_segment_categories",
            u"Категории для проверки",
            ["short_segment"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in CURVE_CATEGORY_OPTIONS]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "short_segment",
        u"5. Короткие обрезки",
        u"Находит трубы и воздуховоды короче заданного порога — обычно это "
        u"мусорные обрезки, оставшиеся от редактирования (разрыв, обрезка, "
        u"перетаскивание). Они засоряют спецификацию и подсчёты. Порог "
        u"задаётся в мм (по умолчанию 100), набор категорий — галочками. "
        u"Совсем вырожденные участки (длина ≈ 0) показывает отдельная "
        u"проверка «Вырожденная геометрия».",
        runner=_run_short_segment_check,
        option_keys=["short_segment_mm", "short_segment_categories"],
        kind=u"report",
    )
