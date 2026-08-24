# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
"""Инструмент «Аудит модели» (панель «Проверка»).

Обёртка над штатными предупреждениями Revit (doc.GetWarnings()): собирает все
нерешённые предупреждения, группирует одинаковые по тексту, показывает с
счётчиками и даёт быстро выделить проблемные элементы в модели. По сути —
адаптация встроенного «Просмотр предупреждений» под рабочий процесс.

Окно немодальное, поэтому используется __persistentengine__ и выделение
элементов через ExternalEvent — как в «Проверке Модели».
"""

__persistentengine__ = True

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

try:
    clr.AddReference("Microsoft.VisualBasic")
    from Microsoft.VisualBasic import Interaction
except:
    Interaction = None

from Autodesk.Revit.DB import ElementId
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent
from System.Collections.Generic import List
import System
import json
import hashlib
from datetime import datetime

from pyrevit import script


import os
import sys

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_audit_window

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TOOL_TITLE = u"Аудит модели"

try:
    OPEN_AUDIT_WINDOWS
except NameError:
    OPEN_AUDIT_WINDOWS = []


# --------------------------------------------------------------------------- #
#                       выделение элементов (ExternalEvent)                    #
# --------------------------------------------------------------------------- #

def activate_revit_window():
    if Interaction is None:
        return
    try:
        process = System.Diagnostics.Process.GetCurrentProcess()
        title = process.MainWindowTitle
        try:
            Interaction.AppActivate(process.Id)
        except:
            if title:
                Interaction.AppActivate(title)
    except:
        pass


def _report(handler, text, is_error=False):
    """Обработчик отчитывается в строку статуса окна, а не окном поверх окна.

    Окно могло закрыться, пока событие ждало своей очереди, поэтому молча
    проглатываем любую осечку.
    """
    callback = getattr(handler, "report", None)

    if callback is None:
        return

    try:
        callback(text, is_error)
    except:
        pass


class SelectElementsHandler(IExternalEventHandler):
    def __init__(self):
        self.element_ids = []
        self.report = None
    def Execute(self, uiapp):
        try:
            active_uidoc = uiapp.ActiveUIDocument
            if active_uidoc is None:
                return

            active_doc = active_uidoc.Document
            valid_ids = []
            for int_id in self.element_ids:
                try:
                    element = active_doc.GetElement(ElementId(int(int_id)))
                    if element is not None:
                        valid_ids.append(element.Id)
                except:
                    pass

            if not valid_ids:
                _report(self, u"Элементы этого предупреждения не найдены в модели "
                    u"(возможно, уже удалены).", False)
                return

            net_ids = List[ElementId]()
            for element_id in valid_ids:
                net_ids.Add(element_id)

            activate_revit_window()
            try:
                active_uidoc.ShowElements(net_ids)
            except:
                try:
                    active_uidoc.ShowElements(valid_ids[0])
                except:
                    pass

            empty_ids = List[ElementId]()
            active_uidoc.Selection.SetElementIds(empty_ids)
            active_uidoc.Selection.SetElementIds(net_ids)
            active_uidoc.RefreshActiveView()
            activate_revit_window()

        except Exception as ex:
            _report(self, u"Не удалось выделить элементы: {0}".format(unicode(ex)), True)

    def GetName(self):
        return "PP Tools Select Audit Elements"


# ExternalEvent создаётся один раз в API-контексте и переиспользуется.
try:
    AUDIT_SELECT_HANDLER
except NameError:
    AUDIT_SELECT_HANDLER = SelectElementsHandler()
    AUDIT_SELECT_EVENT = ExternalEvent.Create(AUDIT_SELECT_HANDLER)


# --------------------------------------------------------------------------- #
#                         сбор предупреждений модели                          #
# --------------------------------------------------------------------------- #

def _severity_text(warning):
    try:
        return unicode(warning.GetSeverity())
    except:
        return u""


def _warning_ids(warning):
    ids = []
    # Revit 2022 использует имена с суффиксом Ids. Старые варианты оставлены
    # как запасной путь для совместимости с уже встречавшимися обёртками API.
    for getter in ("GetFailingElementIds", "GetAdditionalElementIds",
                   "GetFailingElements", "GetAdditionalElements"):
        try:
            method = getattr(warning, getter, None)
            if method is None:
                continue
            for element_id in method():
                try:
                    ids.append(element_id.IntegerValue)
                except:
                    pass
        except:
            pass
    return ids


def _warning_history_key(description, severity, element_ids):
    """Устойчивый ключ одного предупреждения для локальной истории аудита."""
    parts = [unicode(description or u""), unicode(severity or u"")]
    parts.extend(unicode(item) for item in sorted(element_ids))
    source = u"|".join(parts).encode("utf-8")
    return hashlib.sha1(source).hexdigest()


def _load_warning_history():
    """Читает даты первого обнаружения из настроек текущей кнопки pyRevit."""
    try:
        config = script.get_config()
        raw_history = getattr(config, "audit_warning_first_seen", u"")
        history = json.loads(raw_history) if raw_history else {}
        if isinstance(history, dict):
            return config, history
    except:
        pass
    return None, {}


def _save_warning_history(config, history):
    if config is None:
        return
    try:
        config.audit_warning_first_seen = json.dumps(
            history, ensure_ascii=False, sort_keys=True)
        script.save_config()
    except:
        pass


def collect_warning_groups(active_doc):
    """Группы предупреждений с датой первого обнаружения Аудитом."""
    try:
        warnings = active_doc.GetWarnings()
    except:
        warnings = []

    config, history = _load_warning_history()
    current_history = {}
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
    groups = {}
    order = []
    for warning in warnings:
        try:
            desc = warning.GetDescriptionText()
        except:
            desc = None
        if not desc:
            desc = u"(без описания)"

        severity = _severity_text(warning)
        warning_ids = _warning_ids(warning)
        history_key = _warning_history_key(desc, severity, warning_ids)
        first_seen = history.get(history_key, now_text)
        current_history[history_key] = first_seen

        group = groups.get(desc)
        if group is None:
            group = {"count": 0, "ids": [], "seen": set(),
                     "sev": severity, "newest_first_seen": first_seen}
            groups[desc] = group
            order.append(desc)

        group["count"] += 1
        if first_seen > group["newest_first_seen"]:
            group["newest_first_seen"] = first_seen
        for int_id in warning_ids:
            if int_id in group["seen"]:
                continue
            group["seen"].add(int_id)
            group["ids"].append(int_id)

    result = []
    for desc in order:
        group = groups[desc]
        result.append({
            "desc": desc,
            "count": group["count"],
            "ids": group["ids"],
            "sev": group["sev"],
            "newest_first_seen": group["newest_first_seen"],
        })
    _save_warning_history(config, current_history)
    result.sort(key=lambda item: (item["newest_first_seen"], item["count"]),
                reverse=True)
    return result


# --------------------------------------------------------------------------- #
#                                     окно                                     #
# --------------------------------------------------------------------------- #

def show_audit_window(groups):
    def do_select(element_ids, report):
        AUDIT_SELECT_HANDLER.element_ids = list(element_ids or [])
        AUDIT_SELECT_HANDLER.report = report
        activate_revit_window()
        AUDIT_SELECT_EVENT.Raise()

    def on_closed(window):
        try:
            OPEN_AUDIT_WINDOWS.remove(window)
        except:
            pass

    window = pp_audit_window.show(_HERE, groups, {
        u"select": do_select,
        u"refresh": run_tool_flow,
        u"on_closed": on_closed,
    })

    # Ссылку держим сами: иначе немодальное окно соберёт сборщик мусора
    OPEN_AUDIT_WINDOWS.append(window)


# --------------------------------------------------------------------------- #
#                                основной поток                               #
# --------------------------------------------------------------------------- #

def run_tool_flow():
    try:
        groups = collect_warning_groups(doc)
        if not groups:
            pp_wpf.show_report(
                u"Штатные предупреждения Revit в этой модели отсутствуют.",
                title=u"Предупреждений нет",
                subtitle=TOOL_TITLE
            )
            return
        show_audit_window(groups)
    except SystemExit:
        pass
    except Exception as ex:
        pp_wpf.show_report(
            unicode(ex),
            title=u"Ошибка",
            subtitle=TOOL_TITLE,
            is_error=True
        )


run_tool_flow()
