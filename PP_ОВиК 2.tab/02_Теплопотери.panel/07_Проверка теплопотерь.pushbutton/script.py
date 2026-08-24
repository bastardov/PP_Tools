# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
"""Инструмент «Проверка теплопотерь» (панель «Теплопотери»).

Каркас по образцу «Проверки Модели»: окно со списком проверок, у каждой свои
настройки, запуск отмеченных; отчёт, из которого можно выделить и подкрасить
проблемные элементы.

Первая проверка сверяет «PP_Номер имя помещения» на строительных элементах
с пространством рядом — чтобы поймать переименованные архитектором помещения
после актуализации пространств.

Поиск пространства долгий (зонды по геометрии), поэтому проверка идёт под
прогресс-баром с отменой, а область можно сузить до текущего выделения.

Окна лежат в pp_heatloss_check_windows.py, логика проверок — в
lib/pp_heatloss_checks.py. Здесь остались только обработчики ExternalEvent
и связывание всего вместе.
"""

__persistentengine__ = True

import os
import sys

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

try:
    clr.AddReference("Microsoft.VisualBasic")
    from Microsoft.VisualBasic import Interaction
except:
    Interaction = None

from Autodesk.Revit.DB import (
    Color,
    ElementId,
    FillPatternElement,
    FilteredElementCollector,
    OverrideGraphicSettings,
    Transaction,
)
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent
from System.Collections.Generic import List
import System

from pyrevit import forms

from pp_settings import load_settings, save_settings
from pp_heatloss_checks import (
    CheckCancelled,
    create_run_context,
    get_check_definitions,
    get_check_option_definitions,
    get_default_config,
    run_report_checks,
)

# Модуль окон лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_param_source
import pp_heatloss_check_windows as windows


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TOOL_TITLE = u"Проверка теплопотерь"

try:
    OPEN_REPORT_WINDOWS
except NameError:
    OPEN_REPORT_WINDOWS = []

# Запомненные покрашенные элементы по видам: {view_id_int: set(element_id_int)}.
try:
    PAINTED_ERROR_ELEMENTS
except NameError:
    PAINTED_ERROR_ELEMENTS = {}

ERROR_COLOR = Color(255, 0, 0)


# --------------------------------------------------------------------------- #
#                        выделение и окраска (ExternalEvent)                   #
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
                _report(self, u"Ни один из элементов не найден в текущем документе.", True)
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

            _report(self, u"Выделено в модели: {0}.".format(len(valid_ids)))

        except Exception as ex:
            _report(self, u"Не удалось выделить элемент: {0}".format(unicode(ex)), True)

    def GetName(self):
        return "PP Tools Select Heatloss Check Elements"


def get_solid_fill_id(target_doc):
    try:
        patterns = FilteredElementCollector(target_doc) \
            .OfClass(FillPatternElement) \
            .ToElements()
        for pattern in patterns:
            try:
                if pattern.GetFillPattern().IsSolidFill:
                    return pattern.Id
            except:
                pass
    except:
        pass
    return ElementId.InvalidElementId


def make_error_ogs(solid_id):
    ogs = OverrideGraphicSettings()
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetSurfaceForegroundPatternId(solid_id)
            ogs.SetSurfaceForegroundPatternVisible(True)
        ogs.SetSurfaceForegroundPatternColor(ERROR_COLOR)
    except:
        pass
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetSurfaceBackgroundPatternId(solid_id)
            ogs.SetSurfaceBackgroundPatternVisible(True)
        ogs.SetSurfaceBackgroundPatternColor(ERROR_COLOR)
    except:
        pass
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetCutForegroundPatternId(solid_id)
            ogs.SetCutForegroundPatternVisible(True)
        ogs.SetCutForegroundPatternColor(ERROR_COLOR)
    except:
        pass
    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetCutBackgroundPatternId(solid_id)
            ogs.SetCutBackgroundPatternVisible(True)
        ogs.SetCutBackgroundPatternColor(ERROR_COLOR)
    except:
        pass
    try:
        ogs.SetProjectionLineColor(ERROR_COLOR)
        ogs.SetCutLineColor(ERROR_COLOR)
    except:
        pass
    return ogs


class PaintErrorsHandler(IExternalEventHandler):
    def __init__(self):
        self.element_ids = []
        self.mode = "paint"
        self.report = None

    def Execute(self, uiapp):
        try:
            active_uidoc = uiapp.ActiveUIDocument
            if active_uidoc is None:
                return
            active_doc = active_uidoc.Document
            view = active_doc.ActiveView
            if view is None:
                _report(self, u"Нет активного вида.", True)
                return
            view_key = view.Id.IntegerValue
            if self.mode == "reset":
                self._reset(active_uidoc, active_doc, view, view_key)
            else:
                self._paint(active_uidoc, active_doc, view, view_key)
        except Exception as ex:
            _report(self, u"Не удалось изменить окраску: {0}".format(unicode(ex)), True)

    def _paint(self, active_uidoc, active_doc, view, view_key):
        visible_ids = set()
        try:
            collector = FilteredElementCollector(active_doc, view.Id) \
                .WhereElementIsNotElementType()
            for element in collector:
                visible_ids.add(element.Id.IntegerValue)
        except:
            pass

        total = len(self.element_ids)
        to_paint = [int_id for int_id in self.element_ids if int_id in visible_ids]

        solid_id = get_solid_fill_id(active_doc)
        ogs = make_error_ogs(solid_id)
        painted = PAINTED_ERROR_ELEMENTS.get(view_key, set())

        transaction = Transaction(active_doc, u"PP: Подкрасить ошибки теплопотерь")
        transaction.Start()
        applied = 0
        for int_id in to_paint:
            try:
                view.SetElementOverrides(ElementId(int(int_id)), ogs)
                painted.add(int_id)
                applied += 1
            except:
                pass
        transaction.Commit()

        PAINTED_ERROR_ELEMENTS[view_key] = painted
        active_uidoc.RefreshActiveView()
        activate_revit_window()

        if applied < total:
            _report(
                self,
                u"Покрашено {0} из {1}: остальные не видны на активном виде.".format(
                    applied, total),
                True
            )
        else:
            _report(self, u"Покрашено на активном виде: {0}.".format(applied))

    def _reset(self, active_uidoc, active_doc, view, view_key):
        clean_ogs = OverrideGraphicSettings()
        try:
            elements = list(
                FilteredElementCollector(active_doc, view.Id)
                .WhereElementIsNotElementType()
                .ToElements()
            )
        except:
            elements = []

        transaction = Transaction(active_doc, u"PP: Сброс окраски на виде")
        transaction.Start()
        for element in elements:
            try:
                view.SetElementOverrides(element.Id, clean_ogs)
            except:
                pass
        transaction.Commit()

        PAINTED_ERROR_ELEMENTS[view_key] = set()
        active_uidoc.RefreshActiveView()
        activate_revit_window()

        _report(self, u"Окраска на активном виде сброшена.")

    def GetName(self):
        return "PP Tools Paint Heatloss Check Errors"


# ExternalEvent создаём один раз в API-контексте (первый запуск из команды) и
# переиспользуем — «Проверить заново» вызывает окна из обработчика WPF.
try:
    SELECT_HANDLER
except NameError:
    SELECT_HANDLER = SelectElementsHandler()
    SELECT_EVENT = ExternalEvent.Create(SELECT_HANDLER)
    PAINT_HANDLER = PaintErrorsHandler()
    PAINT_EVENT = ExternalEvent.Create(PAINT_HANDLER)


# --------------------------------------------------------------------------- #
#                             настройки инструмента                           #
# --------------------------------------------------------------------------- #

def _load_saved_settings():
    settings = load_settings()
    state = settings.get("heatloss_check_settings", {})
    if not isinstance(state, dict):
        state = {}
    return settings, state


def _save_settings(config, selected_keys, last_selected_key, scope):
    settings = load_settings()
    settings["heatloss_check_settings"] = {
        "config": dict(config or {}),
        "selected_keys": list(selected_keys or []),
        "last_selected_key": last_selected_key or u"",
        "scope": scope or u"model",
    }
    save_settings(settings)


def get_current_selection_ids():
    try:
        return list(uidoc.Selection.GetElementIds())
    except:
        return []


# --------------------------------------------------------------------------- #
#                              окно настроек                                   #
# --------------------------------------------------------------------------- #

def _param_provider(option_key, values):
    u"""Список для кнопки «Выбрать…» у опций с option_type=u"param".

    Параметры проекта целиком: сверка идёт и по экземпляру, и по типу, а
    встроенные и семейные параметры в привязки не попадают — их вписывают
    в поле руками."""
    return pp_param_source.project_options(doc)


def show_setup_dialog(selection_ids):
    definitions = get_check_definitions()
    option_definitions = get_check_option_definitions()
    defaults = get_default_config()

    _settings, saved_state = _load_saved_settings()

    selected_keys = saved_state.get("selected_keys", [])
    last_selected_key = saved_state.get("last_selected_key", u"")
    saved_config = saved_state.get("config", {})
    saved_scope = saved_state.get("scope", u"model")

    if not isinstance(selected_keys, list):
        selected_keys = []
    if not isinstance(saved_config, dict):
        saved_config = {}

    current_config = dict(defaults)
    current_config.update(saved_config)

    if not selected_keys:
        selected_keys = [definition.key for definition in definitions]

    window = windows.SetupWindow(
        _HERE,
        definitions,
        option_definitions,
        current_config,
        selected_keys,
        last_selected_key,
        saved_scope,
        len(selection_ids),
        param_provider=_param_provider
    )

    setup_data = window.show()

    if setup_data is None:
        return None

    _save_settings(
        setup_data["config"],
        setup_data["selected_keys"],
        setup_data["last_selected_key"],
        setup_data["scope"]
    )

    return setup_data


# --------------------------------------------------------------------------- #
#                                   отчёт                                     #
# --------------------------------------------------------------------------- #

def show_results_dialog(results):
    def do_select(element_ids, report):
        SELECT_HANDLER.element_ids = list(element_ids or [])
        SELECT_HANDLER.report = report
        activate_revit_window()
        SELECT_EVENT.Raise()

    def do_paint(mode, element_ids, report):
        PAINT_HANDLER.mode = mode
        PAINT_HANDLER.element_ids = list(element_ids or [])
        PAINT_HANDLER.report = report
        activate_revit_window()
        PAINT_EVENT.Raise()

    def on_closed(window):
        try:
            OPEN_REPORT_WINDOWS.remove(window)
        except:
            pass

    window = windows.ReportWindow(_HERE, results, {
        u"select": do_select,
        u"paint": do_paint,
        u"restart": run_tool_flow,
        u"on_closed": on_closed,
    })

    # Ссылку держим сами: иначе немодальное окно соберёт сборщик мусора
    OPEN_REPORT_WINDOWS.append(window)

    window.show()


# --------------------------------------------------------------------------- #
#                                  основной поток                             #
# --------------------------------------------------------------------------- #

def run_tool_flow():
    try:
        selection_ids = get_current_selection_ids()

        setup_data = show_setup_dialog(selection_ids)
        if setup_data is None:
            return

        selected_keys = setup_data["selected_keys"]
        config = setup_data["config"]
        scope = setup_data["scope"]

        scope_ids = selection_ids if scope == u"selection" else None
        if scope == u"selection" and not scope_ids:
            pp_wpf.show_report(
                u"В модели ничего не выделено. Выберите элементы "
                u"или переключите область на «Вся модель».",
                title=u"Нечего проверять",
                subtitle=TOOL_TITLE,
                is_error=True
            )
            return

        cancelled = [False]

        with forms.ProgressBar(title=u"Проверка теплопотерь: "
                                     u"{value} из {max_value}",
                               cancellable=True, step=25) as progress_bar:

            def report_progress(current, total):
                if progress_bar.cancelled:
                    cancelled[0] = True
                    return False
                progress_bar.update_progress(current, max(total, 1))
                return True

            context = create_run_context(doc, scope_ids, report_progress)

            try:
                results = run_report_checks(doc, selected_keys, config, context)
            except CheckCancelled:
                cancelled[0] = True
                results = None

        if cancelled[0]:
            pp_wpf.show_report(
                u"Проверка остановлена, модель не изменялась.",
                title=u"Отменено",
                subtitle=TOOL_TITLE
            )
            return

        if not results:
            pp_wpf.show_report(
                u"Проверки не дали результатов. Возможно, в области проверки "
                u"нет элементов с нужным параметром.",
                title=u"Пусто",
                subtitle=TOOL_TITLE
            )
            return

        show_results_dialog(results)

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
