# -*- coding: utf-8 -*-
"""Каркас «Проверки теплопотерь»: модель данных и контекст запуска.

CheckIssue / CheckResult / CheckDefinition / CheckOptionDefinition,
RunContext (область проверки, SpaceFinder, прогресс с отменой через
CheckCancelled). Про конкретные проверки здесь ничего не знает.
Описание пакета — CHECKS.md.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
)

import pp_heatloss_spaces as spaces


class CheckCancelled(Exception):
    """Пользователь нажал «Отмена» в окне прогресса."""


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
        # Свободный текст итога (показывается первой строкой в отчёте).
        self.info = info or u""


class CheckDefinition(object):
    def __init__(self, key, title, description, runner=None, option_keys=None):
        self.key = key
        self.title = title
        self.description = description
        self.runner = runner
        self.option_keys = option_keys or []


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


class RunContext(object):
    """Контекст одного запуска: кэш элементов, поиск пространств, прогресс.

    scope_element_ids:
        None      — проверяем всю модель;
        множество — проверяем только эти элементы (текущее выделение).

    progress:
        вызываемый объект progress(current, total); если он вернёт False,
        проверка прерывается исключением CheckCancelled.
    """

    def __init__(self, doc, scope_element_ids=None, progress=None):
        self.doc = doc
        self.finder = spaces.SpaceFinder(doc)
        self.scope_element_ids = scope_element_ids
        self.progress = progress
        self._by_category = {}

    @property
    def whole_model(self):
        return self.scope_element_ids is None

    def get_by_categories(self, categories):
        key = tuple(sorted(unicode(category) for category in categories))
        cached = self._by_category.get(key)
        if cached is not None:
            return cached

        result = _collect_elements(self.doc, categories)
        self._by_category[key] = result
        return result

    def report(self, current, total):
        if self.progress is None:
            return
        if self.progress(current, total) is False:
            raise CheckCancelled()


def create_run_context(doc, scope_element_ids=None, progress=None):
    return RunContext(doc, scope_element_ids, progress)


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
