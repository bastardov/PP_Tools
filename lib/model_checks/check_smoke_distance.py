# -*- coding: utf-8 -*-
"""Проверка «12. Расстояние между ДП и ДВ (противодымная)» (ключ smoke_distance).

Перенесено из pp_model_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
)

from model_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
    FEET_TO_MM,
)

from model_checks.common import (
    _is_yes,
    _location_curve,
    _location_point,
    _matches_any,
    _parse_category_map,
    _parse_float,
    _parse_masks,
    _smoke_label,
    _smoke_search_text,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    "smoke_categories": u"OST_MechanicalEquipment",
    # Проверка «Расстояние между ДП и ДВ (противодымная вентиляция)».
    # Маски через ; — по ним элемент относится к притоку (ДП) или к
    # вытяжке/дымоудалению (ДВ). Регистр не важен, сравнение по вхождению.
    "smoke_supply_masks": u"ДП",
    "smoke_exhaust_masks": u"ДВ",
    # Минимальное расстояние между притоком и вытяжкой, мм (норма — 5 м).
    "smoke_min_distance_mm": u"5000",
    # Если имя системы пустое — искать маски в имени типа/семейства.
    "smoke_name_fallback": u"да",
}


# Категории для проверки «Расстояние между ДП и ДВ».
SMOKE_CATEGORY_OPTIONS = [
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
    (u"OST_DuctCurves", u"Воздуховоды", BuiltInCategory.OST_DuctCurves),
]


SMOKE_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in SMOKE_CATEGORY_OPTIONS
}


SMOKE_DEFAULT_KEYS = [u"OST_MechanicalEquipment"]


def _element_points(element):
    """Характерные точки элемента: точка вставки либо концы и середина оси."""
    point = _location_point(element)
    if point is not None:
        return [point]

    curve = _location_curve(element)
    if curve is None:
        return []

    points = []
    try:
        points.append(curve.GetEndPoint(0))
        points.append(curve.GetEndPoint(1))
    except:
        return []
    try:
        points.append(curve.Evaluate(0.5, True))
    except:
        pass
    return points


def _min_distance_ft(points_a, points_b):
    """Минимальное расстояние (футы) между двумя наборами точек."""
    best = None
    for point_a in points_a:
        for point_b in points_b:
            try:
                distance = point_a.DistanceTo(point_b)
            except:
                continue
            if best is None or distance < best:
                best = distance
    return best


def _run_smoke_distance_check(doc, config, cache):
    supply_masks = _parse_masks(config.get("smoke_supply_masks")) or [u"дп"]
    exhaust_masks = _parse_masks(config.get("smoke_exhaust_masks")) or [u"дв"]
    use_name_fallback = _is_yes(config.get("smoke_name_fallback"))

    min_mm = _parse_float(config.get("smoke_min_distance_mm"), 5000.0)
    if min_mm <= 0:
        min_mm = 5000.0
    min_ft = min_mm / FEET_TO_MM

    categories = _parse_category_map(
        config.get("smoke_categories"), SMOKE_CATEGORY_MAP, SMOKE_DEFAULT_KEYS)
    elements = cache.get_by_categories(categories)

    supply = []
    exhaust = []
    for element in elements:
        search_text, system_name = _smoke_search_text(
            doc, element, use_name_fallback)
        if not search_text:
            continue

        is_supply = _matches_any(search_text, supply_masks)
        is_exhaust = _matches_any(search_text, exhaust_masks)
        # Ни одна маска не подошла или подошли обе — судить нельзя, пропускаем.
        if is_supply == is_exhaust:
            continue

        points = _element_points(element)
        if not points:
            continue

        try:
            element_id = element.Id.IntegerValue
        except:
            continue

        display = system_name or search_text
        record = (element, display, points, element_id)
        if is_supply:
            supply.append(record)
        else:
            exhaust.append(record)

    found = []
    for s_element, s_display, s_points, s_id in supply:
        for e_element, e_display, e_points, e_id in exhaust:
            distance = _min_distance_ft(s_points, e_points)
            if distance is None or distance >= min_ft:
                continue
            found.append((distance, s_element, s_display, s_id,
                          e_element, e_display, e_id))

    found.sort(key=lambda item: item[0])

    issues = []
    for distance, s_element, s_display, s_id, e_element, e_display, e_id in found:
        issues.append(CheckIssue(
            u"{0} м (норма {1} м) — ДП: {2}; ДВ: {3}".format(
                _format_metres(distance * FEET_TO_MM),
                _format_metres(min_mm),
                _smoke_label(s_element, s_display),
                _smoke_label(e_element, e_display)),
            [s_id, e_id]))

    info = u"Приток (ДП): {0}, вытяжка (ДВ): {1}, проверено пар: {2}".format(
        len(supply), len(exhaust), len(supply) * len(exhaust))
    if not supply or not exhaust:
        info += (u". Одна из групп пуста — проверьте маски имён "
                 u"и категории в настройках проверки")

    return CheckResult(u"smoke_distance",
                       u"12. Расстояние между ДП и ДВ (противодымная)",
                       len(supply) + len(exhaust), issues, info)


def _format_metres(value_mm):
    try:
        return (u"{0:.2f}".format(value_mm / 1000.0)).replace(u".", u",")
    except:
        return u"?"


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "smoke_categories",
            u"Категории для проверки",
            ["smoke_distance"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in SMOKE_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "smoke_supply_masks",
            u"Приток противодымной — маски имён через ; (напр. ДП)",
            ["smoke_distance"]
        ),
        CheckOptionDefinition(
            "smoke_exhaust_masks",
            u"Вытяжка противодымной — маски имён через ; (напр. ДВ)",
            ["smoke_distance"]
        ),
        CheckOptionDefinition(
            "smoke_min_distance_mm",
            u"Минимальное расстояние, мм",
            ["smoke_distance"]
        ),
        CheckOptionDefinition(
            "smoke_name_fallback",
            u"Если системы нет — искать маски в имени типа (да/нет)",
            ["smoke_distance"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "smoke_distance",
        u"12. Расстояние между ДП и ДВ (противодымная)",
        u"Проверяет разрыв между приточной противодымной вентиляцией (ДП) "
        u"и вытяжной / дымоудалением (ДВ): расстояние по прямой должно быть "
        u"не меньше заданного (по умолчанию 5000 мм). Элемент относится к ДП "
        u"или ДВ по маскам в имени системы; если имя системы пустое (частый "
        u"случай у крышных вентиляторов), маска ищется в имени типа или "
        u"семейства — это отключается полем «да/нет». Сравниваются только "
        u"пары приток↔вытяжка, внутри одной группы расстояние не "
        u"проверяется. Элементы, попавшие сразу под обе маски или ни под "
        u"одну, пропускаются.",
        runner=_run_smoke_distance_check,
        option_keys=[
            "smoke_categories",
            "smoke_supply_masks",
            "smoke_exhaust_masks",
            "smoke_min_distance_mm",
            "smoke_name_fallback",
        ],
        kind=u"report",
    )
