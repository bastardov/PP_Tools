# -*- coding: utf-8 -*-
"""Проверка «10. Дубли ADSK_Позиция у оборудования» (ключ position_duplicate).

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
    CheckResult,
)

from model_checks.common import (
    _param_text_from,
    _type_name,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {}

# Опции, которые описаны в другой проверке (там же их DEFAULTS):
#   position_param — в check_position_category.py


def _read_param_text(element, param_name):
    try:
        return _param_text_from(element.LookupParameter(param_name))
    except:
        return u""


def _run_position_duplicate_check(doc, config, cache):
    param_name = unicode(config.get("position_param") or u"ADSK_Позиция").strip()

    equipment = cache.get_by_categories([BuiltInCategory.OST_MechanicalEquipment])
    groups = {}
    order = []
    checked = 0

    for element in equipment:
        value = _read_param_text(element, param_name)
        if not value:
            continue
        checked += 1
        try:
            eid = element.Id.IntegerValue
        except:
            continue
        if value not in groups:
            groups[value] = []
            order.append(value)
        groups[value].append((eid, element))

    issues = []
    for value in order:
        entries = groups[value]
        if len(entries) < 2:
            continue
        ids = [eid for eid, _el in entries]
        names = []
        for _eid, element in entries[:8]:
            name = _read_param_text(element, u"ADSK_Наименование")
            if not name:
                name = _type_name(doc, element)
            names.append(name or u"—")
        issues.append(CheckIssue(
            u"Позиция «{0}» — {1} шт.: {2} (id: {3})".format(
                value, len(entries), u"; ".join(names),
                u", ".join(unicode(i) for i in ids[:8])),
            ids))

    return CheckResult(u"position_duplicate",
                       u"10. Дубли ADSK_Позиция у оборудования",
                       checked, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return []


def get_definition():
    return CheckDefinition(
        "position_duplicate",
        u"10. Дубли ADSK_Позиция у оборудования",
        u"Среди Оборудования ищет одинаковые значения параметра позиции "
        u"(по умолчанию ADSK_Позиция): позиции должны быть уникальными, "
        u"поэтому любое повторение значения у двух и более единиц — ошибка. "
        u"Пустые значения не сравниваются.",
        runner=_run_position_duplicate_check,
        option_keys=["position_param"],
        kind=u"report",
    )
