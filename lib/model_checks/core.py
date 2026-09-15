# -*- coding: utf-8 -*-
"""Каркас «Проверки Модели»: модель данных и кэш элементов.

CheckIssue / CheckResult / CheckDefinition / CheckOptionDefinition,
ElementCache и сборщики коллекций. Про проверки здесь ничего не знает.
Описание пакета — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
)


# Внутренние единицы Revit — футы. Пороги в UI задаём в мм.
FEET_TO_MM = 304.8


class CheckIssue(object):
    def __init__(self, message, element_ids=None):
        self.message = message
        self.element_ids = []

        if element_ids is None:
            return

        for element_id in element_ids:
            if element_id is None:
                continue
            try:
                self.element_ids.append(int(element_id))
            except:
                pass


class CheckResult(object):
    def __init__(self, key, title, checked_count, issues, info=None):
        self.key = key
        self.title = title
        self.checked_count = checked_count
        self.issues = issues or []
        # Свободный текст итога (для проверок-действий, у которых нет issues).
        self.info = info or u""


class CheckDefinition(object):
    # kind:
    #   "report" — обычная проверка: runner(doc, config, cache) -> CheckResult
    #   "action" — интерактивное действие; выполняется в script.py по action_key
    def __init__(self, key, title, description, runner=None,
                 option_keys=None, kind=u"report", action_key=None):
        self.key = key
        self.title = title
        self.description = description
        self.runner = runner
        self.option_keys = option_keys or []
        self.kind = kind
        self.action_key = action_key


class CheckOptionDefinition(object):
    # option_type:
    #   "text"                 — текстовое поле (по умолчанию)
    #   "param"                — имя параметра: поле плюс кнопка
    #                            «Выбрать…» (см. lib/pp_param_picker)
    #   "category_multiselect" — список категорий с галочками (choices)
    def __init__(self, key, label, check_keys, option_type=u"text", choices=None):
        self.key = key
        self.label = label
        self.check_keys = check_keys
        self.option_type = option_type
        self.choices = choices or []


class ElementCache(object):
    """Кэш коллекций элементов на время одного запуска (как в pp_spec_checks).

    Пока «читающих» проверок нет, но кэш готов для будущих стратегий и
    сохраняет ту же архитектуру, что и в «Проверке спецификации».
    """

    def __init__(self, doc):
        self.doc = doc
        self._by_category = {}
        self._all = None

    def get_by_categories(self, categories):
        key = tuple(sorted(unicode(category) for category in categories))
        cached = self._by_category.get(key)
        if cached is not None:
            return cached

        result = _collect_elements(self.doc, categories)
        self._by_category[key] = result
        return result

    def get_all(self):
        if self._all is None:
            self._all = _collect_all_elements(self.doc)
        return self._all


def create_element_cache(doc):
    return ElementCache(doc)


def _collect_elements(doc, categories):
    result = []
    for category in categories:
        try:
            collector = FilteredElementCollector(doc) \
                .OfCategory(category) \
                .WhereElementIsNotElementType()
            result.extend(list(collector.ToElements()))
        except:
            pass
    return result


def _collect_all_elements(doc):
    try:
        return list(
            FilteredElementCollector(doc)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except:
        return []
