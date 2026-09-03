# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
# Заполнить плиту — версия 1.
#
# Полы и кровли, нарезанные по пространствам, повторяют границу помещения и
# потому обходят колонны «зубцом». Для расчёта теплопотерь и для вида такие
# вырезы не нужны: инструмент убирает их из контура, оставляя саму плиту
# на месте (правится только эскиз, id и параметры сохраняются).
#
# Вся геометрия — в lib/pp_slab_fill.py, окно — в pp_slab_fill_window.py.
# Здесь только выделение, настройки, транзакция и отчёт.

import os
import sys
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import script

import pp_wpf
import pp_settings
import pp_slab_fill

_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

from pp_slab_fill_window import ask_options


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


TOOL_TITLE = u"Заполнить плиту"
CONFIG_KEY = u"pp_slab_fill"

SLAB_CATEGORIES = (
    int(BuiltInCategory.OST_Floors),
    int(BuiltInCategory.OST_Roofs)
)


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


# ─────────────────────────────────────────────
# ВЫБОР ПЛИТ
# ─────────────────────────────────────────────

class SlabFilter(ISelectionFilter):
    def AllowElement(self, element):
        try:
            if element.Category is None:
                return False

            return element.Category.Id.IntegerValue in SLAB_CATEGORIES
        except:
            return False

    def AllowReference(self, ref, point):
        return False


def is_slab(element):
    try:
        if element is None or element.Category is None:
            return False

        return element.Category.Id.IntegerValue in SLAB_CATEGORIES
    except:
        return False


def collect_slabs():
    u"""Плиты из текущего выделения, а если его нет — выбор мышью."""
    slabs = []

    for element_id in uidoc.Selection.GetElementIds():
        element = doc.GetElement(element_id)

        if is_slab(element):
            slabs.append(element)

    if slabs:
        return slabs

    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        SlabFilter(),
        u"Выберите полы и кровли → Готово"
    )

    for ref in refs:
        element = doc.GetElement(ref.ElementId)

        if is_slab(element):
            slabs.append(element)

    return slabs


# ─────────────────────────────────────────────
# НАСТРОЙКИ КНОПКИ
# ─────────────────────────────────────────────

def load_start():
    options = {
        u"by_column": True,
        u"by_size": True,
        u"max_mm": 600.0,
        u"fill_holes": True
    }

    try:
        config = script.get_config(CONFIG_KEY)
    except Exception:
        return options

    for key in (u"by_column", u"by_size", u"fill_holes"):
        try:
            options[key] = bool(config.get_option(key, options[key]))
        except Exception:
            pass

    try:
        options[u"max_mm"] = float(config.get_option(u"max_mm", options[u"max_mm"]))
    except Exception:
        pass

    return options


def save_options(options):
    try:
        config = script.get_config(CONFIG_KEY)
        config.by_column = bool(options.get(u"by_column"))
        config.by_size = bool(options.get(u"by_size"))
        config.fill_holes = bool(options.get(u"fill_holes"))
        config.max_mm = float(options.get(u"max_mm") or 600.0)
        script.save_config()
    except Exception:
        pass


# ─────────────────────────────────────────────
# СТРОКИ РАЗБОРА ДЛЯ ОКНА
# ─────────────────────────────────────────────

def build_rows(items, rejects):
    rows = []

    for item in items:
        rows.append({u"text": item.describe(), u"bad": False})

        for line in item.detail_rows():
            rows.append({u"text": u"        ↳ {}".format(line), u"bad": False})

    for reject in rejects:
        rows.append({
            u"text": u"Не получится · {} — {}".format(
                reject[u"title"], reject[u"reason"]
            ),
            u"bad": True
        })

    return rows


try:
    slabs = collect_slabs()

    if not slabs:
        fail(
            u"Плиты не выбраны.\n\n"
            u"Выделите полы или кровли, у которых надо выправить контур, "
            u"и запустите инструмент заново.",
            u"Нечего заполнять"
        )

    # Разбор зависит от условий поиска, поэтому окно просит пересчитать его.
    # Транзакции здесь нет — чтение модели безопасно.
    state = {u"items": [], u"rejects": [], u"columns": None}

    def replan(options):
        columns = []

        if options.get(u"by_column"):
            if state[u"columns"] is None:
                state[u"columns"] = pp_slab_fill.collect_columns(doc, slabs)

            columns = state[u"columns"]

        items, rejects = pp_slab_fill.plan(doc, slabs, options, columns)

        state[u"items"] = items
        state[u"rejects"] = rejects

        return {
            u"rows": build_rows(items, rejects),
            u"pockets_count": sum(item.total for item in items),
            u"slabs_count": len([item for item in items if item.total]),
            u"reject_count": len(rejects)
        }

    options = ask_options(load_start(), replan)

    if options is None:
        raise Stop()

    save_options(options)

    items = [item for item in state[u"items"] if item.has_edits()]
    rejects = state[u"rejects"]

    if not items:
        fail(
            u"Заполнять нечего: в выбранных плитах подходящих вырезов не нашлось.",
            u"Нечего заполнять"
        )

    # ── Правка контуров ──────────────────────────────────────
    done = []
    errors = []

    group = TransactionGroup(doc, u"PP: заполнить плиту")
    group.Start()

    try:
        for item in items:
            try:
                if pp_slab_fill.apply_item(doc, item):
                    done.append(item)
            except Exception as ex:
                errors.append(u"{} — {}".format(item.title, unicode(ex)))

        group.Assimilate()
    except Exception:
        try:
            group.RollBack()
        except:
            pass

        raise

    # ── Отчёт ────────────────────────────────────────────────
    if not done:
        fail(
            u"Ни одну плиту поправить не удалось.\n\n" + u"\n".join(errors),
            u"Контуры не изменены"
        )

    filled = sum(item.total for item in done)

    success = [u"Заполнено: {} {} в {} {}.".format(
        filled, pp_slab_fill.plural_pockets(filled),
        len(done), pp_slab_fill.plural_slabs_in(len(done))
    )]

    for item in done:
        success.append(u"  • {}".format(item.describe()))

    success.append(u"")
    success.append(
        u"Плиты остались прежними: id, тип, параметры, марки и размеры на месте — "
        u"поправлен только контур эскиза."
    )
    success.append(u"Если результат не устроил — отмена по Ctrl+Z.")

    warning = list(success)

    if rejects:
        warning.append(u"")
        warning.append(u"Не получилось разобрать:")

        for reject in rejects:
            warning.append(u"  • {} — {}".format(reject[u"title"], reject[u"reason"]))

    if errors:
        warning.append(u"")
        warning.append(u"Revit отказался менять контур:")

        for line in errors:
            warning.append(u"  • {}".format(line))

    pp_settings.show_report(
        None,
        TOOL_TITLE,
        u"\n".join(success),
        u"\n".join(warning),
        bool(rejects or errors)
    )

except Stop:
    pass

except OperationCanceledException:
    pass

except Exception as ex:
    details = u""

    try:
        details = unicode(traceback.format_exc())
    except Exception:
        pass

    pp_wpf.show_report(
        u"{}\n\n{}".format(unicode(ex), details).strip(),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True,
        monospace=True,
        width=900
    )
