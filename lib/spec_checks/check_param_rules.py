# -*- coding: utf-8 -*-
"""Проверка «13. ADSK_Система_Сокращение» (ключ param_rules).

Перенесено из pp_spec_checks.py без изменения логики. Как устроен файл — CHECKS.md.
"""

import re

import pp_mep_filter

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
)

from spec_checks.common import (
    SORT_ORDER_CATEGORY_OPTIONS,
    _get_element_label,
    _get_parameter_text,
    _normalize_space,
    _resolve_categories,
)


# Значения по умолчанию опций этой проверки.
DEFAULTS = {
    "param_rules_target": u"ADSK_Система_Сокращение",
    "param_rules_text": (
        u"# формат: Значение | Категории через запятую | все|любое | условие ; условие\n"
        u"# операторы: ~ содержит, !~ не содержит, = равно, != не равно, ^= начинается с\n"
        u"# проверяются только элементы, подходящие под условие правила\n"
        u"Отопление | Трубы, Арматура трубопроводов, Соединительные детали трубопроводов, Материалы изоляции труб, Оборудование | любое | Имя системы ~ T11 ; Имя системы ~ T21\n"
        u"Вентснабжение | Трубы, Арматура трубопроводов, Соединительные детали трубопроводов, Материалы изоляции труб, Оборудование | любое | Имя системы ~ T12 ; Имя системы ~ T22\n"
        u"Теплоснабжение | Трубы, Арматура трубопроводов, Соединительные детали трубопроводов, Материалы изоляции труб, Оборудование | все | Имя системы ~ T1 ; Имя системы !~ T11 ; Имя системы !~ T12\n"
        u"Теплоснабжение | Трубы, Арматура трубопроводов, Соединительные детали трубопроводов, Материалы изоляции труб, Оборудование | все | Имя системы ~ T2 ; Имя системы !~ T21 ; Имя системы !~ T22\n"
        u"Дренаж | Арматура трубопроводов, Соединительные детали трубопроводов, Трубы | все | Имя системы ~ Др"
    ),
}

# Прежнее имя: код проверки читает значения по умолчанию как
# DEFAULT_SPEC_CONFIG["ключ"] — оставлено без изменений. Здесь это
# словарь ТОЛЬКО этого файла, общий словарь собирает registry.py.
DEFAULT_SPEC_CONFIG = DEFAULTS


# Операторы условий в правилах (порядок важен: многосимвольные сначала).
# Значения ключей — как в pp_mep_filter (FILTER_OP_KEYS).
_PARAM_RULE_OPERATORS = [
    (u"!~", "not_contains"),
    (u"^=", "starts_with"),
    (u"!=", "ne"),
    (u"~", "contains"),
    (u"=", "eq"),
]


def _parse_one_condition(text):
    text = _normalize_space(text)
    if not text:
        return None

    for token, op in _PARAM_RULE_OPERATORS:
        index = text.find(token)
        if index >= 0:
            param = _normalize_space(text[:index])
            value = _normalize_space(text[index + len(token):])
            if param:
                return {"param": param, "op": op, "value": value, "group": 1}

    return None


def _resolve_category_tokens(text):
    # Категории можно писать ключами OST_... или русскими названиями.
    lookup = {}
    for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS:
        lookup[key.lower()] = key
        lookup[_normalize_space(label).lower()] = key

    result = []
    for token in re.split(r"[;,/\n]+", unicode(text or u"")):
        cleaned = _normalize_space(token).lower()
        if not cleaned:
            continue
        key = lookup.get(cleaned)
        if key and key not in result:
            result.append(key)

    return result


def _parse_param_rules(raw_text):
    rules = []

    for line in re.split(r"[\r\n]+", unicode(raw_text or u"")):
        stripped = line.strip()
        if not stripped or stripped.startswith(u"#"):
            continue

        parts = stripped.split(u"|")
        if len(parts) < 4:
            continue

        value = _normalize_space(parts[0])
        category_keys = _resolve_category_tokens(parts[1])
        match = _normalize_space(parts[2]).lower()
        match_key = "any" if match in (u"любое", u"any", u"или", u"or") else "all"

        conditions = []
        for chunk in re.split(r"[;]+", parts[3]):
            condition = _parse_one_condition(chunk)
            if condition is not None:
                conditions.append(condition)

        if not value or not category_keys or not conditions:
            continue

        rules.append({
            "value": value,
            "category_keys": category_keys,
            "match": match_key,
            "conditions": conditions,
        })

    return rules


def _run_param_rules_check(doc, config, cache):
    title = u"13. ADSK_Система_Сокращение"

    target_param = config.get("param_rules_target", DEFAULT_SPEC_CONFIG["param_rules_target"])
    rules = _parse_param_rules(
        config.get("param_rules_text", DEFAULT_SPEC_CONFIG["param_rules_text"])
    )

    if not rules:
        return CheckResult("param_rules", title, 0, [])

    issues = []
    checked_count = 0

    for rule in rules:
        categories = _resolve_categories(rule["category_keys"])
        if not categories:
            continue

        filter_config = {
            "enabled": True,
            "match": rule["match"],
            "group_combine": "and",
            "conditions": rule["conditions"],
        }

        expected = _normalize_space(rule["value"])
        elements = cache.get_by_categories(categories)

        for element in elements:
            if not pp_mep_filter.element_passes_filter(doc, element, filter_config):
                continue

            checked_count += 1
            actual = _normalize_space(_get_parameter_text(element, [target_param]))

            if actual != expected:
                shown = actual if actual else u"(пусто)"
                issues.append(CheckIssue(
                    u"{0} | {1}: '{2}' (ожидалось '{3}')".format(
                        _get_element_label(doc, element),
                        target_param,
                        shown,
                        expected
                    ),
                    [element.Id.IntegerValue]
                ))

    return CheckResult("param_rules", title, checked_count, issues)


# --------------------------------------------------------------------------- #
#                          описание для реестра                               #
# --------------------------------------------------------------------------- #

def get_options():
    return [
        CheckOptionDefinition(
            "param_rules_target",
            u"Целевой параметр",
            ["param_rules"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "param_rules_text",
            u"Правила: Значение | Категории | все/любое | условия (по одному на строку)",
            ["param_rules"],
            option_type=u"multiline"
        ),
    ]


def get_definition():
    return CheckDefinition(
        "param_rules",
        u"13. ADSK_Система_Сокращение",
        u"Ищет элементы по условиям правил (как в параметризации) и проверяет, "
        u"что целевой параметр точно равен значению правила. Не подходящие под "
        u"условия элементы игнорируются.",
        _run_param_rules_check,
        option_keys=[
            "param_rules_target",
            "param_rules_text"
        ]
    )
