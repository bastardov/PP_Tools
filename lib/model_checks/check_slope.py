# -*- coding: utf-8 -*-
"""Проверка «2. Уклон у труб и воздуховодов» (ключ slope_unexpected).

Перенесено из pp_model_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
)

from model_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from model_checks.common import (
    _element_label,
    _parse_float,
    _pipe_system_name,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Уклон у труб и воздуховодов».
    "slope_param": u"Уклон",
    # Значения уклона в ПРОЦЕНТАХ, которые считаем штатными и не показываем.
    # Разделитель — точка с запятой (запятая внутри числа = десятичная).
    # 100% = 45°, 57.7350% = 30°, 173.2051% = 60°; 0 = уклона нет.
    "slope_exclude_values": u"0; 100; 57.7350; 173.2051",
    # Допуск сравнения с исключениями, в процентных пунктах.
    "slope_tolerance": u"0.01",
}


SLOPE_CATEGORIES = [
    BuiltInCategory.OST_PipeCurves,
    BuiltInCategory.OST_DuctCurves,
]


# Имена BuiltInParameter уклона перебираем через getattr: набор отличается
# между версиями Revit, отсутствующие просто пропускаем.
SLOPE_BIP_NAMES = ["RBS_SLOPE", "RBS_PIPE_SLOPE", "RBS_DUCT_SLOPE"]


def _parse_number_list(raw_text):
    """Числа через ';' (запятая внутри числа = десятичный разделитель)."""
    result = []
    cleaned = unicode(raw_text or u"").replace(u"\n", u";")
    for part in cleaned.split(u";"):
        token = part.strip().replace(u",", u".")
        if not token:
            continue
        try:
            result.append(float(token))
        except:
            pass
    return result


def _get_slope_param(element, param_name):
    if param_name:
        try:
            p = element.LookupParameter(param_name)
            if p is not None:
                return p
        except:
            pass
    for name in SLOPE_BIP_NAMES:
        try:
            bip = getattr(BuiltInParameter, name, None)
            if bip is None:
                continue
            p = element.get_Parameter(bip)
            if p is not None:
                return p
        except:
            pass
    return None


def _parse_slope_text(text):
    """Разбирает то, что Revit показывает в свойствах: «57.7350%», «1:100»."""
    t = unicode(text or u"").strip()
    if not t:
        return None
    t = t.replace(u" ", u" ").replace(u",", u".")

    if u":" in t:
        parts = t.split(u":")
        if len(parts) == 2:
            try:
                a = float(_keep_number_chars(parts[0]))
                b = float(_keep_number_chars(parts[1]))
                if b:
                    return a / b * 100.0
            except:
                return None
        return None

    cleaned = _keep_number_chars(t)
    try:
        return float(cleaned)
    except:
        return None


def _keep_number_chars(text):
    out = []
    for ch in unicode(text or u""):
        if ch.isdigit() or ch in u".-":
            out.append(ch)
    return u"".join(out)


def _slope_percent(param):
    """Уклон в процентах (float) или None, если значения нет."""
    try:
        if not param.HasValue:
            return None
    except:
        pass

    text = None
    try:
        text = param.AsValueString()
    except:
        text = None

    if text:
        value = _parse_slope_text(text)
        if value is not None:
            return value

    # Запасной путь: внутреннее значение уклона — отношение (1.0 = 100%).
    try:
        return param.AsDouble() * 100.0
    except:
        return None


def _format_slope(value):
    try:
        text = u"{0:.4f}".format(value)
    except:
        return unicode(value)
    if u"." in text:
        text = text.rstrip(u"0").rstrip(u".")
    return u"{0}%".format(text)


def _run_slope_check(doc, config, cache):
    param_name = unicode(config.get("slope_param") or u"").strip()
    excluded = _parse_number_list(config.get("slope_exclude_values"))
    tolerance = abs(_parse_float(config.get("slope_tolerance"), 0.01))

    elements = cache.get_by_categories(SLOPE_CATEGORIES)
    issues = []
    checked = 0

    for element in elements:
        param = _get_slope_param(element, param_name)
        if param is None:
            continue

        slope = _slope_percent(param)
        if slope is None:
            continue

        checked += 1

        skip = False
        for value in excluded:
            if abs(slope - value) <= tolerance:
                skip = True
                break
        if skip:
            continue

        try:
            eid = element.Id.IntegerValue
        except:
            continue

        system_name = _pipe_system_name(element)
        issues.append(CheckIssue(
            u"Уклон {0} — {1}".format(
                _format_slope(slope), _element_label(element, system_name)),
            [eid]))

    return CheckResult(u"slope_unexpected", u"2. Уклон у труб и воздуховодов",
                       checked, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "slope_param",
            u"Параметр уклона",
            ["slope_unexpected"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "slope_exclude_values",
            u"Пропускать уклоны, % (через ; — напр. 0; 100; 57.7350; 173.2051)",
            ["slope_unexpected"]
        ),
        CheckOptionDefinition(
            "slope_tolerance",
            u"Допуск сравнения, процентных пунктов",
            ["slope_unexpected"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "slope_unexpected",
        u"2. Уклон у труб и воздуховодов",
        u"Показывает трубы и воздуховоды, у которых в параметре «Уклон» "
        u"есть значение, кроме перечисленных в поле исключений. Штатные "
        u"диагональные участки задаются как проценты: 100% = 45°, "
        u"57.7350% = 30°, 173.2051% = 60°, 0 = уклона нет. Всё, что не "
        u"попало в исключения, попадает в отчёт. Сравнение идёт по "
        u"значению, которое Revit показывает в свойствах (проценты), с "
        u"заданным допуском. Элементы без значения уклона (например "
        u"вертикальные участки) пропускаются.",
        runner=_run_slope_check,
        option_keys=[
            "slope_param",
            "slope_exclude_values",
            "slope_tolerance",
        ],
        kind=u"report",
    )
