# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
u"""Пакетно ставит галочку «Полутон» выбранным категориям в шаблонах видов.

Столбец «Полутон» в диалоге «Видимость/Графика» — это часть переопределения
графики категории (OverrideGraphicSettings.Halftone). Управляется через API:
    ogs = view.GetCategoryOverrides(cat.Id)
    ogs.SetHalftone(True)
    view.SetCategoryOverrides(cat.Id, ogs)
Работает и для шаблонов видов (шаблон — это тоже View).

Логика окна:
  - слева отмечаются шаблоны видов — то, к чему применяем;
  - справа отмечаются категории, которым нужен полутон. По умолчанию уже стоят
    Оси, Марки помещений и Марки пространств (определяются по BuiltInCategory,
    не по имени, чтобы не зависеть от языка Revit).

Инструмент только ВКЛЮЧАЕТ полутон у отмеченных категорий. Категории со снятой
галочкой не трогаются — их полутон не выключается. Прочие переопределения
графики (цвет, линии, штриховка) не меняются.

Выбор запоминается в конфиге pyRevit между запусками.
"""

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Element,
    View,
    ViewType,
    Category,
    CategoryType,
    BuiltInCategory,
    Transaction,
)

from pyrevit import script
from pp_settings import show_report, load_settings


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
active_view = doc.ActiveView

import os
import sys

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_halftone_window


TITLE = u"Полутона"

cfg = script.get_config()

# Категории, отмеченные по умолчанию при первом запуске.
DEFAULT_BICS = (
    BuiltInCategory.OST_Grids,        # Оси
    BuiltInCategory.OST_RoomTags,     # Марки помещений
    BuiltInCategory.OST_MEPSpaceTags,  # Марки пространств
)

# Типы видов, в которых переопределения категорий не имеют смысла,
# плюс 3D-виды — они в выборке не нужны.
SKIP_VIEW_TYPES = (
    ViewType.ThreeD,
    ViewType.Schedule,
    ViewType.ColumnSchedule,
    ViewType.PanelSchedule,
    ViewType.DrawingSheet,
    ViewType.Legend,
    ViewType.Report,
    ViewType.SystemBrowser,
    ViewType.ProjectBrowser,
    ViewType.Internal,
    ViewType.Undefined,
)


# ---------------------------------------------------------------- утилиты

def elem_name(element):
    u"""Имя вида/шаблона без конфликта свойства Name в IronPython."""
    try:
        return Element.Name.GetValue(element)
    except:
        pass
    try:
        return element.Name
    except:
        return u"<без имени>"


def cat_name(cat):
    try:
        return cat.Name
    except:
        return u"<без имени>"


def err_text(ex):
    try:
        msg = getattr(ex, "Message", None)
        if msg:
            return unicode(msg)
    except:
        pass
    try:
        return unicode(ex)
    except:
        return u"<не удалось прочитать текст ошибки>"


def is_usable_view(v):
    try:
        if v.ViewType in SKIP_VIEW_TYPES:
            return False
    except:
        return False
    return True


# ---------------------------------------------------------------- сбор данных

def collect_templates():
    u"""Шаблоны видов, в которых имеет смысл переопределять графику категорий."""
    result = []
    for v in FilteredElementCollector(doc).OfClass(View).ToElements():
        try:
            if not v.IsTemplate:
                continue
        except:
            continue
        if not is_usable_view(v):
            continue
        result.append(v)
    result.sort(key=elem_name)
    return result


def collect_categories():
    u"""Категории модели и аннотаций верхнего уровня, видимые в интерфейсе."""
    result = []
    try:
        cats = doc.Settings.Categories
    except:
        cats = []
    for cat in cats:
        try:
            if cat.CategoryType not in (CategoryType.Model,
                                        CategoryType.Annotation):
                continue
        except:
            continue
        try:
            if not cat.IsVisibleInUI:
                continue
        except:
            pass
        try:
            if cat.Parent is not None:
                continue
        except:
            pass
        result.append(cat)
    result.sort(key=cat_name)
    return result


def default_category_ids():
    u"""Id категорий по умолчанию (Оси/Помещения/Пространства)."""
    ids = set()
    for bic in DEFAULT_BICS:
        try:
            c = Category.GetCategory(doc, bic)
        except:
            c = None
        if c is not None:
            try:
                ids.add(c.Id.IntegerValue)
            except:
                pass
    return ids


# ---------------------------------------------------------------- конфиг

def load_saved(key):
    try:
        saved = cfg.get_option(key, [])
    except:
        saved = []
    if not isinstance(saved, list):
        saved = []
    return saved


def save_selection(tpl_names, cat_names):
    try:
        cfg.saved_ht_templates = tpl_names
        cfg.saved_ht_categories = cat_names
        script.save_config()
    except:
        pass


# ---------------------------------------------------------------- список с фильтром

def show_setup_dialog(templates, categories, default_ids):
    u"""Окно выбора. Возврат: (шаблоны, категории) или (None, None)."""
    saved_cats = load_saved("saved_ht_categories")

    if not saved_cats:
        # Первый запуск: отмечаем набор категорий по умолчанию
        saved_cats = [cat_name(c) for c in categories if _cat_id(c) in default_ids]

    window, templates_sel, categories_sel = pp_halftone_window.ask(_HERE, {
        u"templates": [(elem_name(item), item) for item in templates],
        u"categories": [(cat_name(item), item) for item in categories],
        u"saved_templates": load_saved("saved_ht_templates"),
        u"saved_categories": saved_cats,
    })

    if templates_sel is None:
        return None, None

    saved_templates, saved_categories = window.checked_names()
    save_selection(saved_templates, saved_categories)

    return templates_sel, categories_sel


def _cat_id(cat):
    try:
        return cat.Id.IntegerValue
    except:
        return None


# ---------------------------------------------------------------- применение

class Stats(object):
    def __init__(self):
        self.applied = 0     # включили полутон
        self.untouched = 0   # уже был включён
        self.problems = []


def apply_halftone(view, cat, stats):
    u"""Включает полутон одной категории в одном шаблоне/виде."""
    try:
        ogs = view.GetCategoryOverrides(cat.Id)
    except Exception as ex:
        stats.problems.append(
            u"{0} / {1}: категорию нельзя переопределить — {2}".format(
                elem_name(view), cat_name(cat), err_text(ex)))
        return

    try:
        if ogs.Halftone:
            stats.untouched += 1
            return
    except:
        pass

    try:
        ogs.SetHalftone(True)
        view.SetCategoryOverrides(cat.Id, ogs)
        stats.applied += 1
    except Exception as ex:
        stats.problems.append(
            u"{0} / {1}: {2}".format(
                elem_name(view), cat_name(cat), err_text(ex)))


def apply_changes(templates, categories):
    stats = Stats()
    t = Transaction(doc, TITLE)
    t.Start()
    try:
        for view in templates:
            for cat in categories:
                apply_halftone(view, cat, stats)
        t.Commit()
    except:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return stats


# ---------------------------------------------------------------- точка входа

def main():
    templates = collect_templates()

    if not templates:
        pp_wpf.show_report(
            u"В модели нет подходящих шаблонов видов.",
            title=u"Нечего настраивать",
            subtitle=TITLE
        )
        return

    categories = collect_categories()

    if not categories:
        pp_wpf.show_report(
            u"Не удалось собрать категории модели.",
            title=u"Нечего настраивать",
            subtitle=TITLE
        )
        return

    templates_sel, categories_sel = show_setup_dialog(
        templates, categories, default_category_ids())

    if templates_sel is None:
        return

    stats = apply_changes(templates_sel, categories_sel)

    has_issues = bool(stats.problems)

    try:
        want_output = load_settings().get("show_success_report", False)
    except:
        want_output = False

    if has_issues or want_output:
        output = script.get_output()
        output.print_md(u"### {0}".format(TITLE))
        output.print_md(u"Обработано шаблонов: **{0}**".format(len(templates_sel)))
        output.print_md(u"Категорий отмечено: **{0}**".format(len(categories_sel)))
        output.print_md(u"Полутон включён (категория×шаблон): **{0}**".format(
            stats.applied))
        output.print_md(u"Уже был включён: **{0}**".format(stats.untouched))
        if stats.problems:
            output.print_md(u"**Не удалось обработать:**")
            for line in stats.problems:
                output.print_md(u"- {0}".format(line))

    success_msg = u"Полутон включён: {0}\nУже был включён: {1}".format(
        stats.applied, stats.untouched)

    warning_msg = success_msg
    if stats.problems:
        warning_msg += u"\n\nПервые ошибки/пропуски:\n" + u"\n".join(
            stats.problems[:15])

    show_report(None, TITLE, success_msg, warning_msg, has_issues)


main()
