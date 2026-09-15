# -*- coding: utf-8 -*-
"""Проверка «8. Изоляция труб (наличие и тип)» (ключ pipe_insulation).

Перенесено из pp_model_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    InsulationLiningBase,
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
    _pipe_label,
    _pipe_system_name,
    _type_name,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Изоляция труб (наличие и тип)».
    # Правила по строкам: <ключи имени типа трубы> => <ключи имени типа изоляции>.
    # Слева и справа — подстроки через ; (совпадение по вхождению, регистр не важен).
    "insul_rules": (
        u"ГОСТ 10704; ГОСТ 3262 => Цилиндры некашированные\n"
        u"Полиэтилен сшитый; PE-Xa; PPR; PP-R => Трубки теплоизоляционные"
    ),
    # Маски систем через ; — где изоляция нужна (пусто = все) / исключить.
    "insul_include_systems": u"",
    "insul_exclude_systems": u"",
    # Показывать трубы, не попавшие ни в одно правило (нераспознанный класс).
    "insul_report_unclassified": u"нет",
}


def _parse_insulation_rules(raw_text):
    """Строки «ключи типа трубы => ключи изоляции» -> [(type_keys, insul_keys)].

    Ключи по обе стороны — подстроки через ; в нижнем регистре.
    """
    rules = []
    text = unicode(raw_text or u"").replace(u"\r\n", u"\n").replace(u"\r", u"\n")
    for line in text.split(u"\n"):
        line = line.strip()
        if not line or u"=>" not in line:
            continue
        left, right = line.split(u"=>", 1)
        type_keys = [k.strip().lower()
                     for k in left.replace(u",", u";").split(u";") if k.strip()]
        insul_keys = [k.strip().lower()
                      for k in right.replace(u",", u";").split(u";") if k.strip()]
        if type_keys and insul_keys:
            rules.append((type_keys, insul_keys))
    return rules


def _insulation_type_names(doc, pipe):
    """Имена типов изоляции, навешенной на трубу (список строк)."""
    names = []
    try:
        insul_ids = InsulationLiningBase.GetInsulationIds(doc, pipe.Id)
    except:
        insul_ids = None
    if not insul_ids:
        return names
    for insul_id in insul_ids:
        try:
            insul = doc.GetElement(insul_id)
        except:
            insul = None
        if insul is None:
            continue
        name = _type_name(doc, insul)
        if name:
            names.append(name)
    return names


def _run_pipe_insulation_check(doc, config, cache):
    rules = _parse_insulation_rules(config.get("insul_rules"))
    include = _parse_masks(config.get("insul_include_systems"))
    exclude = _parse_masks(config.get("insul_exclude_systems"))
    report_unclassified = _is_yes(config.get("insul_report_unclassified"))

    pipes = cache.get_by_categories([BuiltInCategory.OST_PipeCurves])
    issues = []
    checked = 0

    for pipe in pipes:
        system_name = _pipe_system_name(pipe)
        if include and not _matches_any(system_name, include):
            continue
        if exclude and _matches_any(system_name, exclude):
            continue

        type_name = _type_name(doc, pipe)
        low_type = (type_name or u"").lower()

        insul_keys = None
        for rule_type_keys, rule_insul_keys in rules:
            if any(key in low_type for key in rule_type_keys):
                insul_keys = rule_insul_keys
                break

        try:
            eid = pipe.Id.IntegerValue
        except:
            continue

        base = _pipe_label(pipe, system_name)
        if type_name:
            label = u"{0}, тип «{1}»".format(base, type_name)
        else:
            label = base

        if insul_keys is None:
            if report_unclassified:
                checked += 1
                issues.append(CheckIssue(
                    u"Класс трубы не распознан правилами — {0}".format(label),
                    [eid]))
            continue

        checked += 1
        insul_names = _insulation_type_names(doc, pipe)

        if not insul_names:
            issues.append(CheckIssue(
                u"Нет изоляции — {0}".format(label), [eid]))
            continue

        matched = False
        for name in insul_names:
            low = name.lower()
            if any(key in low for key in insul_keys):
                matched = True
                break

        if not matched:
            issues.append(CheckIssue(
                u"Не тот тип изоляции: «{0}» — {1}".format(
                    u"; ".join(insul_names), label), [eid]))

    return CheckResult(u"pipe_insulation", u"8. Изоляция труб (наличие и тип)",
                       checked, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "insul_rules",
            u"Правила: <тип трубы содержит> => <изоляция содержит> (по строкам)",
            ["pipe_insulation"],
            option_type=u"multiline"
        ),
        CheckOptionDefinition(
            "insul_include_systems",
            u"Проверять только системы (маски через ;, пусто = все)",
            ["pipe_insulation"]
        ),
        CheckOptionDefinition(
            "insul_exclude_systems",
            u"Исключить системы (маски через ;)",
            ["pipe_insulation"]
        ),
        CheckOptionDefinition(
            "insul_report_unclassified",
            u"Показывать трубы без правила (да/нет)",
            ["pipe_insulation"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "pipe_insulation",
        u"8. Изоляция труб (наличие и тип)",
        u"Проверяет изоляцию труб по правилам, которые вы задаёте строками "
        u"вида «<тип трубы содержит> => <изоляция содержит>». Для каждой "
        u"трубы в зоне проверки: класс определяется по вхождению ключа в имя "
        u"типа трубы; если изоляции нет — «нет изоляции», если есть, но имя "
        u"её типа не содержит ожидаемого ключа — «не тот тип». Систем задают "
        u"зону проверки масками (где изоляция нужна). Трубы, не попавшие ни "
        u"в одно правило, по умолчанию пропускаются.",
        runner=_run_pipe_insulation_check,
        option_keys=[
            "insul_rules",
            "insul_include_systems",
            "insul_exclude_systems",
            "insul_report_unclassified",
        ],
        kind=u"report",
    )
