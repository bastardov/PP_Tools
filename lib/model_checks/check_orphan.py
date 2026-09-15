# -*- coding: utf-8 -*-
"""Проверка «3. Элементы без системы (висящие)» (ключ orphan_no_system).

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
)

from model_checks.common import (
    _element_label,
    _is_yes,
    _parse_category_keys,
    _pipe_system_name,
)


# Значения по умолчанию опций, которые описаны в этом файле.
DEFAULTS = {
    # Проверка «Элементы без системы (висящие)».
    # Ключи категорий через запятую (пусто = набор по умолчанию в runner).
    "orphan_categories": (
        u"OST_PipeAccessory, OST_PipeFitting, OST_MechanicalEquipment, "
        u"OST_DuctAccessory, OST_DuctTerminal"
    ),
    # Показывать частично подключённые (есть свободный конец, но не все).
    "orphan_include_partial": u"нет",
}


# Категории для проверки «висящих» элементов: (ключ, подпись, BuiltInCategory).
ORPHAN_CATEGORY_OPTIONS = [
    (u"OST_PipeAccessory", u"Арматура трубопроводов", BuiltInCategory.OST_PipeAccessory),
    (u"OST_PipeFitting", u"Соединительные детали трубопроводов", BuiltInCategory.OST_PipeFitting),
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_DuctFitting", u"Соединительные детали воздуховодов", BuiltInCategory.OST_DuctFitting),
]


ORPHAN_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in ORPHAN_CATEGORY_OPTIONS
}


# По умолчанию отмечены самые ходовые «висящие» категории.
ORPHAN_DEFAULT_KEYS = [
    u"OST_PipeAccessory",
    u"OST_PipeFitting",
    u"OST_MechanicalEquipment",
    u"OST_DuctAccessory",
    u"OST_DuctTerminal",
]


def _connector_stats(element):
    """(всего физических коннекторов Piping/HVAC, из них подключённых).

    Возвращает (0, 0), если у элемента нет MEP-коннекторов (значит он не может
    «висеть» на сети и в проверке не участвует).
    """
    try:
        mep_model = element.MEPModel
    except:
        mep_model = None
    if mep_model is None:
        return (0, 0)

    try:
        manager = mep_model.ConnectorManager
    except:
        manager = None
    if manager is None:
        return (0, 0)

    try:
        connectors = manager.Connectors
    except:
        return (0, 0)

    total = 0
    connected = 0
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
        total += 1
        try:
            if connector.IsConnected:
                connected += 1
        except:
            pass
    return (total, connected)


def _run_orphan_check(doc, config, cache):
    keys = _parse_category_keys(config.get("orphan_categories"))
    if not keys:
        keys = list(ORPHAN_DEFAULT_KEYS)
    include_partial = _is_yes(config.get("orphan_include_partial"))

    categories = []
    for key in keys:
        built_in = ORPHAN_CATEGORY_MAP.get(key)
        if built_in is not None:
            categories.append(built_in)

    elements = cache.get_by_categories(categories)
    issues = []
    checked = 0

    for element in elements:
        total, connected = _connector_stats(element)
        if total == 0:
            continue

        try:
            eid = element.Id.IntegerValue
        except:
            continue

        checked += 1
        system_name = _pipe_system_name(element)
        label = _element_label(element, system_name)

        if connected == 0:
            issues.append(CheckIssue(
                u"Не подключён (висит) — {0}".format(label), [eid]))
        elif include_partial and connected < total:
            issues.append(CheckIssue(
                u"Частично подключён: свободно {0} из {1} — {2}".format(
                    total - connected, total, label), [eid]))

    return CheckResult(u"orphan_no_system",
                       u"3. Элементы без системы (висящие)", checked, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "orphan_categories",
            u"Категории для проверки",
            ["orphan_no_system"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _b in ORPHAN_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "orphan_include_partial",
            u"Показывать частично подключённые (да/нет)",
            ["orphan_no_system"]
        ),
    ]


def get_definition():
    return CheckDefinition(
        "orphan_no_system",
        u"3. Элементы без системы (висящие)",
        u"Ищет элементы выбранных категорий (клапаны, краны, фитинги, "
        u"оборудование и т.п.), у которых есть MEP-коннекторы, но ни один не "
        u"подключён — то есть элемент «висит» и не входит ни в одну систему. "
        u"Если включить «частично подключённые», в отчёт добавятся элементы "
        u"с частью свободных концов. Элементы без MEP-коннекторов "
        u"пропускаются.",
        runner=_run_orphan_check,
        option_keys=["orphan_categories", "orphan_include_partial"],
        kind=u"report",
    )
