# -*- coding: utf-8 -*-
"""Проверка «1. Трубы без расхода» (ключ pipe_flow_missing).

Перенесено из pp_model_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    StorageType,
)

from model_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from model_checks.common import (
    _matches_any,
    _parse_masks,
    _pipe_label,
    _pipe_system_name,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Трубы без расхода».
    # Пусто = встроенный «Расход» Revit (RBS_PIPE_FLOW_PARAM); иначе имя
    # своего параметра (напр. ADSK-параметр из внешнего расчёта).
    "pipe_flow_param": u"",
    # Маски имён систем через ; — проверять только их (пусто = все системы).
    "pipe_flow_include_systems": u"",
    # Маски имён систем через ; — исключить (напр. канализация, дренаж).
    "pipe_flow_exclude_systems": u"",
}


FLOW_EPS = 1e-9


def _pipe_flow_value(pipe, param_name):
    """Возвращает расход (float) или None, если значение отсутствует."""
    if param_name:
        try:
            p = pipe.LookupParameter(param_name)
            if (p is not None and p.StorageType == StorageType.Double
                    and p.HasValue):
                return p.AsDouble()
        except:
            pass
        return None
    try:
        p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_FLOW_PARAM)
        if p is not None and p.HasValue:
            return p.AsDouble()
    except:
        pass
    return None


def _run_pipe_flow_check(doc, config, cache):
    param_name = unicode(config.get("pipe_flow_param") or u"").strip()
    include = _parse_masks(config.get("pipe_flow_include_systems"))
    exclude = _parse_masks(config.get("pipe_flow_exclude_systems"))

    pipes = cache.get_by_categories([BuiltInCategory.OST_PipeCurves])
    issues = []
    checked = 0

    for pipe in pipes:
        system_name = _pipe_system_name(pipe)
        if include and not _matches_any(system_name, include):
            continue
        if exclude and _matches_any(system_name, exclude):
            continue

        try:
            eid = pipe.Id.IntegerValue
        except:
            continue

        checked += 1
        label = _pipe_label(pipe, system_name)
        value = _pipe_flow_value(pipe, param_name)

        if value is None:
            issues.append(CheckIssue(
                u"Расход не заполнен — {0}".format(label), [eid]))
        elif abs(value) < FLOW_EPS:
            if not system_name:
                issues.append(CheckIssue(
                    u"Нет системы, расход 0 — {0}".format(label), [eid]))
            else:
                issues.append(CheckIssue(
                    u"Расход 0 — {0}".format(label), [eid]))

    return CheckResult(u"pipe_flow_missing", u"1. Трубы без расхода",
                       checked, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "pipe_flow_param",
            u"Параметр расхода (пусто = встроенный «Расход» Revit)",
            ["pipe_flow_missing"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "pipe_flow_include_systems",
            u"Проверять только системы (маски через ;, пусто = все)",
            ["pipe_flow_missing"]
        ),
        CheckOptionDefinition(
            "pipe_flow_exclude_systems",
            u"Исключить системы (маски через ;)",
            ["pipe_flow_missing"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "pipe_flow_missing",
        u"1. Трубы без расхода",
        u"Ищет трубы с пустым или нулевым расходом. Параметр расхода "
        u"настраивается: пусто = встроенный «Расход» Revit, иначе имя своего "
        u"параметра (напр. из внешнего расчёта). Можно ограничить проверку "
        u"по именам систем (маски-подстроки) и исключить системы без "
        u"расхода (канализация, дренаж и т.п.). Трубы без назначенной "
        u"системы помечаются отдельно.",
        runner=_run_pipe_flow_check,
        option_keys=[
            "pipe_flow_param",
            "pipe_flow_include_systems",
            "pipe_flow_exclude_systems",
        ],
        kind=u"report",
    )
