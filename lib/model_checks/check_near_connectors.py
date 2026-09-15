# -*- coding: utf-8 -*-
"""Проверка «4. Почти соединённые концы» (ключ near_connectors).

Перенесено из pp_model_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    ConnectorType,
    Domain,
)

from model_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
    FEET_TO_MM,
)

from model_checks.common import (
    _cell,
    _parse_float,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Почти соединённые концы».
    # Допуск, мм: два свободных End-коннектора ближе этого — вероятно забыли соединить.
    "near_tolerance_mm": u"50",
}


# Категории для проверки «Почти соединённые концы» — всё, что несёт MEP-коннекторы.
NEAR_CATEGORIES = [
    BuiltInCategory.OST_PipeCurves,
    BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_PipeFitting,
    BuiltInCategory.OST_DuctFitting,
    BuiltInCategory.OST_PipeAccessory,
    BuiltInCategory.OST_DuctAccessory,
    BuiltInCategory.OST_MechanicalEquipment,
    BuiltInCategory.OST_DuctTerminal,
]


def _get_connector_manager(element):
    """ConnectorManager элемента: у осевых (труба/воздуховод) он свой, у
    семейств (фитинг/арматура/оборудование) — через MEPModel."""
    try:
        cm = element.ConnectorManager
        if cm is not None:
            return cm
    except:
        pass
    try:
        mep = element.MEPModel
        if mep is not None:
            return mep.ConnectorManager
    except:
        pass
    return None


def _iter_end_connectors(element):
    """Список End-коннекторов домена Piping/HVAC (физические концы сети)."""
    manager = _get_connector_manager(element)
    if manager is None:
        return []
    try:
        connectors = manager.Connectors
    except:
        return []

    result = []
    for connector in connectors:
        try:
            if connector.ConnectorType != ConnectorType.End:
                continue
        except:
            continue
        try:
            if connector.Domain not in (Domain.DomainPiping, Domain.DomainHvac):
                continue
        except:
            pass
        result.append(connector)
    return result


def _run_near_connectors_check(doc, config, cache):
    tol_mm = _parse_float(config.get("near_tolerance_mm"), 50.0)
    if tol_mm <= 0:
        tol_mm = 50.0
    tol_ft = tol_mm / FEET_TO_MM

    elements = cache.get_by_categories(NEAR_CATEGORIES)

    # Свободные концы: (element_id, XYZ).
    free = []
    for element in elements:
        try:
            eid = element.Id.IntegerValue
        except:
            continue
        for connector in _iter_end_connectors(element):
            try:
                if connector.IsConnected:
                    continue
            except:
                pass
            try:
                origin = connector.Origin
            except:
                continue
            if origin is None:
                continue
            free.append((eid, origin))

    # Пространственная сетка с шагом = допуску: пара ближе tol попадает в
    # соседние ячейки, поэтому достаточно перебрать 27 ячеек-соседей.
    grid = {}
    for index, item in enumerate(free):
        origin = item[1]
        key = (_cell(origin.X, tol_ft), _cell(origin.Y, tol_ft),
               _cell(origin.Z, tol_ft))
        grid.setdefault(key, []).append(index)

    used = set()
    issues = []
    for index, item in enumerate(free):
        if index in used:
            continue
        eid, origin = item
        cx = _cell(origin.X, tol_ft)
        cy = _cell(origin.Y, tol_ft)
        cz = _cell(origin.Z, tol_ft)

        best = None
        best_dist = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    bucket = grid.get((cx + dx, cy + dy, cz + dz))
                    if not bucket:
                        continue
                    for other in bucket:
                        if other <= index or other in used:
                            continue
                        other_eid, other_origin = free[other]
                        if other_eid == eid:
                            continue
                        try:
                            dist = origin.DistanceTo(other_origin)
                        except:
                            continue
                        if dist <= tol_ft and (best_dist is None
                                               or dist < best_dist):
                            best_dist = dist
                            best = other

        if best is None:
            continue

        used.add(index)
        used.add(best)
        other_eid = free[best][0]
        dist_mm = best_dist * FEET_TO_MM
        issues.append(CheckIssue(
            u"Свободные концы в {0} мм, но не соединены — id {1} и id {2}".format(
                int(round(dist_mm)), eid, other_eid),
            [eid, other_eid]))

    return CheckResult(u"near_connectors", u"4. Почти соединённые концы",
                       len(free), issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "near_tolerance_mm",
            u"Допуск сближения концов, мм",
            ["near_connectors"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "near_connectors",
        u"4. Почти соединённые концы",
        u"Ищет пары свободных (не подключённых) End-коннекторов труб, "
        u"воздуховодов, фитингов, арматуры, оборудования и "
        u"воздухораспределителей, которые расположены ближе заданного "
        u"допуска друг к другу, но не соединены. Это типовая ошибка «забыл "
        u"дотянуть/состыковать»: визуально примыкает, а по факту разрыв "
        u"сети. Допуск сближения задаётся в мм (по умолчанию 50). "
        u"Коннекторы одного элемента между собой не сравниваются.",
        runner=_run_near_connectors_check,
        option_keys=["near_tolerance_mm"],
        kind=u"report",
    )
