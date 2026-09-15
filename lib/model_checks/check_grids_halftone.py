# -*- coding: utf-8 -*-
"""Проверка «11. Полутон у Осей в шаблонах» (ключ grids_halftone).

Перенесено из pp_model_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    ElementId,
    FilteredElementCollector,
    View,
    ViewType,
)

from model_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from model_checks.common import (
    _is_yes,
    _matches_any,
    _parse_masks,
    _safe_name,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Полутон у Осей в шаблонах».
    # да = полутон должен стоять (покажем шаблоны, где снят); нет = наоборот.
    "grids_halftone_expected": u"да",
    "grids_tpl_include": u"",
    "grids_tpl_exclude": u"",
}


# Только планы этажей (обычный и инженерный ОВиК-план).
# 3D, разрезы, фасады, потолки и т.п. в этой проверке не участвуют.
GRID_TEMPLATE_VIEW_TYPES = (
    ViewType.FloorPlan,
    ViewType.EngineeringPlan,
)


def _run_grids_halftone_check(doc, config, cache):
    expected_on = _is_yes(config.get("grids_halftone_expected"))
    include = _parse_masks(config.get("grids_tpl_include"))
    exclude = _parse_masks(config.get("grids_tpl_exclude"))

    grids_cat = ElementId(BuiltInCategory.OST_Grids)
    issues = []
    checked = 0

    try:
        views = list(FilteredElementCollector(doc).OfClass(View).ToElements())
    except:
        views = []

    for view in views:
        try:
            if not view.IsTemplate:
                continue
        except:
            continue
        try:
            if view.ViewType not in GRID_TEMPLATE_VIEW_TYPES:
                continue
        except:
            continue

        name = _safe_name(view)
        if include and not _matches_any(name, include):
            continue
        if exclude and _matches_any(name, exclude):
            continue

        try:
            ogs = view.GetCategoryOverrides(grids_cat)
            halftone = bool(ogs.Halftone)
        except:
            continue

        checked += 1
        if halftone == expected_on:
            continue

        try:
            eid = view.Id.IntegerValue
            ids = [eid]
        except:
            ids = []

        if expected_on:
            message = u"Полутон снят (ожидается включён) — шаблон «{0}»".format(name)
        else:
            message = u"Полутон включён (ожидается снят) — шаблон «{0}»".format(name)
        issues.append(CheckIssue(message, ids))

    return CheckResult(u"grids_halftone", u"11. Полутон у Осей в шаблонах",
                       checked, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "grids_halftone_expected",
            u"Полутон у Осей должен стоять (да/нет)",
            ["grids_halftone"]
        ),
        CheckOptionDefinition(
            "grids_tpl_include",
            u"Проверять только шаблоны (маски имён через ;, пусто = все)",
            ["grids_halftone"]
        ),
        CheckOptionDefinition(
            "grids_tpl_exclude",
            u"Исключить шаблоны (маски имён через ;)",
            ["grids_halftone"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "grids_halftone",
        u"11. Полутон у Осей в шаблонах",
        u"Проверяет переопределение полутона у категории аннотаций «Оси» в "
        u"шаблонах видов. Берутся ТОЛЬКО планы этажей (обычные и инженерные "
        u"ОВиК-планы); 3D, разрезы, фасады, потолки и прочее не проверяются. "
        u"По умолчанию полутон должен стоять — проверка показывает шаблоны, "
        u"где он снят (ожидание меняется полем да/нет). Область сужается "
        u"масками имён шаблонов.",
        runner=_run_grids_halftone_check,
        option_keys=[
            "grids_halftone_expected",
            "grids_tpl_include",
            "grids_tpl_exclude",
        ],
        kind=u"report",
    )
