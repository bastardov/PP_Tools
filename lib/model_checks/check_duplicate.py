# -*- coding: utf-8 -*-
"""Проверка «7. Дубли в одной точке» (ключ duplicate_at_point).

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
    _category_id,
    _cell,
    _duplicate_label,
    _is_yes,
    _location_curve,
    _location_point,
    _parse_category_keys,
    _parse_float,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Дубли в одной точке».
    "duplicate_tolerance_mm": u"10",
    # Учитывать только совпадение типоразмера (да) или любой элемент категории (нет).
    "duplicate_same_type": u"да",
    "duplicate_categories": (
        u"OST_MechanicalEquipment, OST_PipeAccessory, OST_DuctAccessory, "
        u"OST_PipeCurves, OST_DuctCurves"
    ),
}


# Категории для проверки «Дубли в одной точке».
DUPLICATE_CATEGORY_OPTIONS = [
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_PipeAccessory", u"Арматура трубопроводов", BuiltInCategory.OST_PipeAccessory),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_PipeFitting", u"Соединит. детали труб", BuiltInCategory.OST_PipeFitting),
    (u"OST_DuctFitting", u"Соединит. детали возд.", BuiltInCategory.OST_DuctFitting),
    (u"OST_PipeCurves", u"Трубы", BuiltInCategory.OST_PipeCurves),
    (u"OST_DuctCurves", u"Воздуховоды", BuiltInCategory.OST_DuctCurves),
]


DUPLICATE_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in DUPLICATE_CATEGORY_OPTIONS
}


DUPLICATE_DEFAULT_KEYS = [
    u"OST_MechanicalEquipment",
    u"OST_PipeAccessory",
    u"OST_DuctAccessory",
    u"OST_PipeCurves",
    u"OST_DuctCurves",
]


# Осевые категории (дубль = совпадение обоих концов, а не точки вставки).
DUPLICATE_CURVE_KEYS = set([
    u"OST_PipeCurves",
    u"OST_DuctCurves",
    u"OST_FlexPipeCurves",
    u"OST_FlexDuctCurves",
])


def _run_duplicate_check(doc, config, cache):
    tol_mm = _parse_float(config.get("duplicate_tolerance_mm"), 10.0)
    if tol_mm <= 0:
        tol_mm = 10.0
    tol_ft = tol_mm / FEET_TO_MM
    same_type = _is_yes(config.get("duplicate_same_type"))

    keys = _parse_category_keys(config.get("duplicate_categories"))
    if not keys:
        keys = list(DUPLICATE_DEFAULT_KEYS)

    categories = []
    curve_category_ids = set()
    for key in keys:
        built_in = DUPLICATE_CATEGORY_MAP.get(key)
        if built_in is None:
            continue
        categories.append(built_in)
        if key in DUPLICATE_CURVE_KEYS:
            try:
                curve_category_ids.add(int(built_in))
            except:
                pass

    elements = cache.get_by_categories(categories)

    groups = {}
    checked = 0
    for element in elements:
        cat_id = _category_id(element)
        if cat_id is None:
            continue
        type_id = 0
        if same_type:
            try:
                type_id = element.GetTypeId().IntegerValue
            except:
                type_id = 0

        geom_key = None
        if cat_id in curve_category_ids:
            geom_key = _curve_endpoints_key(element, tol_ft)
        else:
            geom_key = _point_key(element, tol_ft)
        if geom_key is None:
            continue

        checked += 1
        full_key = (cat_id, type_id, geom_key)
        try:
            eid = element.Id.IntegerValue
        except:
            continue
        groups.setdefault(full_key, []).append((eid, element))

    issues = []
    for _key, entries in groups.items():
        if len(entries) < 2:
            continue
        ids = [eid for eid, _el in entries]
        first_element = entries[0][1]
        label = _duplicate_label(doc, first_element)
        issues.append(CheckIssue(
            u"Наложение {0} шт. — {1} (id: {2})".format(
                len(entries), label,
                u", ".join(unicode(i) for i in ids[:8])),
            ids))

    return CheckResult(u"duplicate_at_point", u"7. Дубли в одной точке",
                       checked, issues)


def _point_key(element, tol_ft):
    point = _location_point(element)
    if point is None:
        return None
    return (_cell(point.X, tol_ft), _cell(point.Y, tol_ft),
            _cell(point.Z, tol_ft))


def _curve_endpoints_key(element, tol_ft):
    curve = _location_curve(element)
    if curve is None:
        return None
    try:
        p0 = curve.GetEndPoint(0)
        p1 = curve.GetEndPoint(1)
    except:
        return None
    a = (_cell(p0.X, tol_ft), _cell(p0.Y, tol_ft), _cell(p0.Z, tol_ft))
    b = (_cell(p1.X, tol_ft), _cell(p1.Y, tol_ft), _cell(p1.Z, tol_ft))
    # Направление трубы не важно — концы упорядочиваем.
    return (a, b) if a <= b else (b, a)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "duplicate_tolerance_mm",
            u"Допуск совпадения точек, мм",
            ["duplicate_at_point"]
        ),
        CheckOptionDefinition(
            "duplicate_same_type",
            u"Только одинаковый типоразмер (да/нет)",
            ["duplicate_at_point"]
        ),
        CheckOptionDefinition(
            "duplicate_categories",
            u"Категории для проверки",
            ["duplicate_at_point"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in DUPLICATE_CATEGORY_OPTIONS]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "duplicate_at_point",
        u"7. Дубли в одной точке",
        u"Ищет наложенные друг на друга элементы одной категории: "
        u"оборудование, арматуру, фитинги и воздухораспределители — по "
        u"точке вставки; трубы и воздуховоды — по совпадению обоих концов. "
        u"Совпадение проверяется с допуском (по умолчанию 10 мм). Опция "
        u"«только одинаковый типоразмер» (по умолчанию да) отсекает случаи "
        u"разных элементов в одном месте. Набор категорий — галочками.",
        runner=_run_duplicate_check,
        option_keys=[
            "duplicate_tolerance_mm",
            "duplicate_same_type",
            "duplicate_categories",
        ],
        kind=u"report",
    )
