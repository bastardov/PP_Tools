# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
u"""Управляет галочками видимости RVT-связей в шаблонах видов и в самих видах.

Вкладка «Связанные файлы RVT» в диалоге В/Г хранит видимость связи как обычное
скрытие элемента в виде, поэтому этим можно управлять через API:
View.HideElements / View.UnhideElements. Работает и для шаблонов видов.

Связь выключается ЦЕЛИКОМ: скрывается и сам тип связи (верхняя строка-галочка
в диалоге В/Г), и все её экземпляры (вложенные строки).

Логика окна:
  - слева отмечаются шаблоны видов (вкладка «Шаблоны видов») и/или обычные виды
    (вкладка «Виды») — то, к чему применяем;
  - справа галочки связей задают ЖЕЛАЕМОЕ состояние: галочка стоит — связь видна,
    галочка снята — связь выключается.

Выбор запоминается в конфиге pyRevit между запусками.

Важно про API Revit 2022: у View НЕТ метода IsElementHidden — состояние читается
через Element.IsHidden(View).
"""

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    Element,
    View,
    ViewType,
    RevitLinkInstance,
    ElementId,
    Transaction,
)

from System.Collections.Generic import List

from pyrevit import script
from pp_settings import show_report, load_settings

import os
import sys

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_links_window


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
active_view = doc.ActiveView

TITLE = u"Связи в шаблоне"

cfg = script.get_config()

# Окно вывода pyRevit НЕ трогаем без нужды: любой print_md открывает его,
# даже когда всё прошло чисто. Берём его лениво — только если есть что показать.

# Виды и шаблоны, в которых связи не отображаются в принципе
SKIP_VIEW_TYPES = (
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


def _plan_view_types():
    u"""Плановые типы видов — то, что чипс «Шаблоны видов ПЭ» оставляет в списке.

    Через getattr, чтобы отсутствие какого-то члена в конкретной версии API
    не роняло скрипт.
    """
    result = []

    for name in ("FloorPlan", "CeilingPlan", "EngineeringPlan", "AreaPlan"):
        value = getattr(ViewType, name, None)

        if value is not None:
            result.append(value)

    return tuple(result)


PLAN_VIEW_TYPES = _plan_view_types()


# ---------------------------------------------------------------- утилиты

def elem_name(element):
    u"""Имя элемента без конфликта свойства Name в IronPython."""
    try:
        return Element.Name.GetValue(element)
    except:
        pass
    try:
        return element.Name
    except:
        return u"<без имени>"


def err_text(ex):
    u"""Читаемый текст исключения .NET / Python."""
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


def elem_hidden(view, element):
    u"""(скрыт?, ошибка). Первое значение None — прочитать не удалось.

    В Revit 2022 состояние читается через Element.IsHidden(View);
    метода View.IsElementHidden не существует.
    """
    try:
        return element.IsHidden(view), None
    except Exception as ex:
        return None, err_text(ex)


# ---------------------------------------------------------------- сбор данных

def is_usable_view(v):
    try:
        if v.ViewType in SKIP_VIEW_TYPES:
            return False
    except:
        return False
    return True


def is_plan_view(v):
    u"""Плановый ли вид (или шаблон плана) — фильтр чипса «Шаблоны видов ПЭ»."""
    try:
        return v.ViewType in PLAN_VIEW_TYPES
    except:
        return False


def collect_templates():
    u"""Шаблоны видов, в которых имеет смысл управлять связями."""
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


def collect_views():
    u"""Обычные (не шаблонные) виды."""
    result = []
    for v in FilteredElementCollector(doc).OfClass(View).ToElements():
        try:
            if v.IsTemplate:
                continue
        except:
            continue
        if not is_usable_view(v):
            continue
        result.append(v)
    result.sort(key=elem_name)
    return result


class LinkEntry(object):
    u"""Связь целиком: тип связи + все её экземпляры.

    Тип связи — это верхняя строка с галочкой в диалоге В/Г, экземпляры —
    вложенные строки. Скрываем и то, и другое, иначе связь выключается
    не полностью.
    """

    def __init__(self, link_type, instances):
        self.link_type = link_type
        self.instances = instances
        self.label = elem_name(link_type)

    def elements(self):
        result = []
        if self.link_type is not None:
            result.append(self.link_type)
        result.extend(self.instances)
        return result

    def probe(self):
        u"""Элемент, по которому определяем текущее состояние связи."""
        if self.link_type is not None:
            return self.link_type
        if self.instances:
            return self.instances[0]
        return None


def collect_link_entries():
    u"""Все RVT-связи модели, сгруппированные по типу связи."""
    by_type = {}
    order = []
    for inst in FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements():
        try:
            type_id = inst.GetTypeId()
        except:
            continue
        key = type_id.IntegerValue
        if key not in by_type:
            by_type[key] = (type_id, [])
            order.append(key)
        by_type[key][1].append(inst)

    entries = []
    for key in order:
        type_id, instances = by_type[key]
        link_type = None
        try:
            link_type = doc.GetElement(type_id)
        except:
            link_type = None
        entries.append(LinkEntry(link_type, instances))

    entries.sort(key=lambda e: e.label)
    return entries


# ---------------------------------------------------------------- конфиг

def load_saved(key):
    try:
        saved = cfg.get_option(key, [])
    except:
        saved = []
    if not isinstance(saved, list):
        saved = []
    return saved


def save_selection(tpl_names, view_names, visible_links):
    try:
        cfg.saved_templates = tpl_names
        cfg.saved_views = view_names
        cfg.saved_visible_links = visible_links
        script.save_config()
    except:
        pass


# ---------------------------------------------------------------- список с фильтром

def current_view_choice():
    u"""Кнопка «Текущий вид»: возвращает (имя, None) или (None, объяснение)."""
    if active_view is None or not is_usable_view(active_view):
        return None, u"Текущий вид не подходит для работы со связями."

    try:
        if active_view.IsTemplate:
            return None, (u"Текущий вид — это шаблон, ищите его в списке "
                          u"«Шаблоны видов».")
    except:
        pass

    return elem_name(active_view), None


def link_visible_in_active_view(entry):
    u"""Видна ли связь в активном виде — для кнопки «Как в текущем виде»."""
    if active_view is None:
        return True

    probe = entry.probe()

    if probe is None:
        return True

    state, _ = elem_hidden(active_view, probe)

    return not bool(state)


def show_setup_dialog(templates, views, entries):
    u"""Окно выбора. Возврат: (цели, флаги видимости) или (None, None)."""
    window, targets, visible_flags = pp_links_window.ask(_HERE, {
        u"templates": [(elem_name(item), item) for item in templates],
        u"views": [(elem_name(item), item) for item in views],
        u"links": [(entry.label, entry) for entry in entries],
        u"saved_templates": load_saved("saved_templates"),
        u"saved_views": load_saved("saved_views"),
        u"saved_visible_links": load_saved("saved_visible_links"),
        u"on_current_view": current_view_choice,
        u"link_state": link_visible_in_active_view,
        u"is_plan": is_plan_view,
    })

    if targets is None:
        return None, None

    saved_templates, saved_views, saved_links = window.checked_names()
    save_selection(saved_templates, saved_views, saved_links)

    return targets, visible_flags


# ---------------------------------------------------------------- применение

class Stats(object):
    u"""Счётчики и сообщения по итогам работы."""

    def __init__(self):
        self.hidden = 0
        self.shown = 0
        self.untouched = 0
        self.problems = []
        self.templated = []
        self.diagnostics = []


def apply_entry(view, entry, want_visible, stats):
    u"""Приводит одну связь (тип + экземпляры) в одном виде к нужному состоянию."""
    to_change = List[ElementId]()

    for element in entry.elements():
        state, read_err = elem_hidden(view, element)

        if state is None:
            # состояние неизвестно — пробуем применить вслепую
            if len(stats.diagnostics) < 3:
                stats.diagnostics.append(
                    u"{0} / {1}: состояние не читается — {2}".format(
                        elem_name(view), entry.label, read_err
                    )
                )
            to_change.Add(element.Id)
            continue

        if want_visible and state:
            to_change.Add(element.Id)
        elif (not want_visible) and (not state):
            try:
                if not element.CanBeHidden(view):
                    stats.problems.append(
                        u"{0} / {1}: элемент связи нельзя скрыть в этом виде".format(
                            elem_name(view), entry.label
                        )
                    )
                    continue
            except:
                pass
            to_change.Add(element.Id)

    if to_change.Count == 0:
        stats.untouched += 1
        return

    try:
        if want_visible:
            view.UnhideElements(to_change)
            stats.shown += 1
        else:
            view.HideElements(to_change)
            stats.hidden += 1
        return
    except Exception as ex:
        batch_err = err_text(ex)

    # пачкой не прошло — пробуем поэлементно
    done = 0
    for element in entry.elements():
        one = List[ElementId]()
        one.Add(element.Id)
        try:
            if want_visible:
                view.UnhideElements(one)
            else:
                view.HideElements(one)
            done += 1
        except:
            pass

    if done:
        if want_visible:
            stats.shown += 1
        else:
            stats.hidden += 1
    else:
        stats.problems.append(
            u"{0} / {1}: {2}".format(elem_name(view), entry.label, batch_err)
        )


def apply_changes(targets, entries, visible_flags):
    u"""Приводит видимость связей в целевых видах к заданному состоянию."""
    stats = Stats()

    t = Transaction(doc, TITLE)
    t.Start()
    try:
        for view in targets:
            for i, entry in enumerate(entries):
                apply_entry(view, entry, visible_flags[i], stats)

            # вид с назначенным шаблоном может вернуть настройки шаблона
            try:
                if (not view.IsTemplate) and \
                        view.ViewTemplateId != ElementId.InvalidElementId:
                    stats.templated.append(elem_name(view))
            except:
                pass

        t.Commit()
    except:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise

    return stats


# ---------------------------------------------------------------- точка входа

def main():
    entries = collect_link_entries()

    if not entries:
        pp_wpf.show_report(
            u"В модели нет связанных файлов RVT.",
            title=u"Нечего настраивать",
            subtitle=TITLE
        )
        return

    templates = collect_templates()
    views = collect_views()

    if not templates and not views:
        pp_wpf.show_report(
            u"В модели нет подходящих видов и шаблонов.",
            title=u"Нечего настраивать",
            subtitle=TITLE
        )
        return

    targets, visible_flags = show_setup_dialog(templates, views, entries)

    if targets is None:
        return

    stats = apply_changes(targets, entries, visible_flags)

    issues = stats.problems + stats.diagnostics
    has_issues = bool(issues) or bool(stats.templated)

    # Подробный отчёт в окне pyRevit — только когда есть что разбирать,
    # либо если в настройках включён отчёт при успешном выполнении.
    try:
        want_output = load_settings().get("show_success_report", False)
    except:
        want_output = False

    if has_issues or want_output:
        output = script.get_output()
        output.print_md(u"### {0}".format(TITLE))
        output.print_md(u"Обработано шаблонов и видов: **{0}**".format(len(targets)))
        output.print_md(u"Связей выключено: **{0}**".format(stats.hidden))
        output.print_md(u"Связей включено: **{0}**".format(stats.shown))
        output.print_md(
            u"Уже было в нужном состоянии: **{0}**".format(stats.untouched)
        )

        if stats.diagnostics:
            output.print_md(u"**Диагностика:**")
            for line in stats.diagnostics:
                output.print_md(u"- {0}".format(line))

        if stats.templated:
            output.print_md(
                u"**Внимание:** у этих видов назначен шаблон — если шаблон управляет "
                u"видимостью связей, он перебьёт изменения. Уберите шаблон или "
                u"правьте сам шаблон:"
            )
            for name in sorted(set(stats.templated)):
                output.print_md(u"- {0}".format(name))

        if stats.problems:
            output.print_md(u"**Не удалось обработать:**")
            for line in stats.problems:
                output.print_md(u"- {0}".format(line))

    success_msg = u"Выключено: {0}\nВключено: {1}\nБез изменений: {2}".format(
        stats.hidden, stats.shown, stats.untouched
    )

    warning_msg = success_msg
    if issues:
        warning_msg += u"\n\nПервые ошибки/пропуски:\n" + u"\n".join(issues[:15])
    if stats.templated:
        warning_msg += (
            u"\n\nУ этих видов назначен шаблон, он может перебить изменения:\n"
            + u"\n".join(sorted(set(stats.templated))[:15])
        )

    show_report(
        None,
        TITLE,
        success_msg,
        warning_msg,
        has_issues,
    )


main()
