# -*- coding: utf-8 -*-
u"""Список параметров модели для окна выбора (lib/pp_param_picker).

Единственное место, где список собирается из Revit: инструменты не должны
каждый раз повторять обход ``doc.ParameterBindings``.

    import pp_param_source

    options = pp_param_source.project_options(doc)
    name = pp_param_picker.ask(options, current=box.Text, owner=window)

``project_options`` отдаёт параметры проекта (общие и проектные) с пометкой
привязки и числом категорий. Встроенные и семейные параметры в привязки не
попадают — поэтому кнопка «Выбрать…» всегда стоит РЯДОМ с полем ручного
ввода, а не вместо него.

``instance_only=True`` убирает привязки типа: инструменту, который читает
значение через ``LookupParameter`` у экземпляра, параметр типоразмера
показывать нельзя — он его всё равно не прочитает.

``category_ids`` включает подсчёт покрытия: параметры, привязанные ко всем
переданным категориям, поднимаются наверх списка.
"""

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import InstanceBinding


SCOPE_INSTANCE = u"экземпляр"
SCOPE_TYPE = u"тип"
SCOPE_BOTH = u"экземпляр / тип"


def plural_categories(count):
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return u"категория"

    if 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14):
        return u"категории"

    return u"категорий"


def project_parameters(doc):
    u"""Имя параметра -> {u"instance": set(id категорий), u"type": set(...)}."""
    result = {}

    try:
        iterator = doc.ParameterBindings.ForwardIterator()
        iterator.Reset()
    except:
        return result

    while True:
        try:
            if not iterator.MoveNext():
                break
        except:
            break

        try:
            definition = iterator.Key
            binding = iterator.Current
        except:
            continue

        if definition is None or binding is None:
            continue

        try:
            name = definition.Name
        except:
            name = None

        if not name:
            continue

        entry = result.get(name)

        if entry is None:
            entry = {u"instance": set(), u"type": set()}
            result[name] = entry

        slot = u"instance" if isinstance(binding, InstanceBinding) else u"type"

        try:
            for category in binding.Categories:
                try:
                    entry[slot].add(category.Id.IntegerValue)
                except:
                    pass
        except:
            pass

    return result


def project_options(doc, category_ids=None, instance_only=False):
    u"""Список для pp_param_picker: [{name, display, coverage}, ...]."""
    parameters = project_parameters(doc)

    wanted = set(category_ids or [])
    total = len(wanted)

    options = []

    for name in parameters:
        instance_ids = parameters[name][u"instance"]
        type_ids = set() if instance_only else parameters[name][u"type"]

        if not instance_ids and not type_ids:
            continue

        if instance_ids and type_ids:
            scope = SCOPE_BOTH
        elif instance_ids:
            scope = SCOPE_INSTANCE
        else:
            scope = SCOPE_TYPE

        all_ids = instance_ids.union(type_ids)

        if total:
            coverage = len(all_ids.intersection(wanted))
            tail = u"есть у {} из {} выбранных категорий".format(coverage, total)
        else:
            coverage = 0
            count = len(all_ids)
            tail = u"{} {}".format(count, plural_categories(count))

        options.append({
            u"name": name,
            u"display": u"{}    · {} · {}".format(name, scope, tail),
            u"coverage": coverage,
        })

    # Сверху — покрывающие все выбранные категории, дальше по алфавиту
    return sorted(
        options,
        key=lambda item: (-item[u"coverage"], item[u"name"].lower())
    )
