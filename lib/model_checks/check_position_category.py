# -*- coding: utf-8 -*-
"""Проверка «9. ADSK_Позиция не на своей категории» (ключ position_wrong_category).

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
)

from model_checks.common import (
    _category_id,
    _duplicate_label,
    _param_text_from,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверки ADSK_Позиция (не на своей категории / дубли у оборудования).
    "position_param": u"ADSK_Позиция",
}


def _run_position_category_check(doc, config, cache):
    param_name = unicode(config.get("position_param") or u"ADSK_Позиция").strip()
    equip_cat = int(BuiltInCategory.OST_MechanicalEquipment)

    issues = []
    checked = 0

    for element in cache.get_all():
        cid = _category_id(element)
        if cid is None or cid == equip_cat:
            continue
        try:
            param = element.LookupParameter(param_name)
        except:
            param = None
        if param is None:
            continue

        checked += 1
        value = _param_text_from(param)
        if not value:
            continue

        try:
            eid = element.Id.IntegerValue
        except:
            continue

        issues.append(CheckIssue(
            u"{0} = «{1}» вне «Оборудования» — {2}".format(
                param_name, value, _duplicate_label(doc, element)),
            [eid]))

    return CheckResult(u"position_wrong_category",
                       u"9. ADSK_Позиция не на своей категории",
                       checked, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "position_param",
            u"Параметр позиции",
            ["position_wrong_category", "position_duplicate"],
            option_type=u"param"
        ),
    ]


def get_definition():
    return CheckDefinition(
        "position_wrong_category",
        u"9. ADSK_Позиция не на своей категории",
        u"Параметр позиции (по умолчанию ADSK_Позиция) должен быть заполнен "
        u"только у Оборудования. Проверка показывает элементы ВСЕХ прочих "
        u"категорий, у которых этот параметр заполнен — это ошибка "
        u"(позицию поставили не туда).",
        runner=_run_position_category_check,
        option_keys=["position_param"],
        kind=u"report",
    )
