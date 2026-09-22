# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from System.Collections.Generic import List
from Autodesk.Revit.DB import FilteredElementCollector, FamilyInstance, ElementId
import pp_param_picker
import pp_wpf

from pp_settings import show_report


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


CONTROLLER_FAMILY_NAME = u"PP_Семейство контроллер 1"


def get_family_name(el):
    try:
        if el.Symbol and el.Symbol.Family:
            return el.Symbol.Family.Name
    except:
        pass

    return None


def get_element_info(el):
    try:
        cat = el.Category.Name
    except:
        cat = u"?"

    try:
        level = doc.GetElement(el.LevelId).Name
    except:
        level = u"?"

    return u"ID: {} | Категория: {} | Уровень: {}".format(
        el.Id.IntegerValue,
        cat,
        level
    )


TOOL_TITLE = u"Найти контроллер"


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


try:
    controllers = []

    collector = FilteredElementCollector(doc) \
        .OfClass(FamilyInstance) \
        .WhereElementIsNotElementType()

    for el in collector:
        try:
            if get_family_name(el) == CONTROLLER_FAMILY_NAME:
                controllers.append(el)
        except:
            pass

    if not controllers:
        fail(
            u"Контроллер не найден в проекте.\n\nИскомое семейство:\n{}".format(
                CONTROLLER_FAMILY_NAME
            ),
            u"Контроллер не найден"
        )

    target = None
    selected_from_list = False

    if len(controllers) == 1:
        target = controllers[0]

    else:
        items = {}

        for el in controllers:
            label = get_element_info(el)
            items[label] = el

        selected = pp_param_picker.ask(
            sorted(items.keys()),
            title=u"Найдено несколько контроллеров",
            subtitle=TOOL_TITLE,
            empty_text=u"Список контроллеров пуст"
        )

        if not selected:
            raise Stop()

        target = items[selected]
        selected_from_list = True

    ids = List[ElementId]()
    ids.Add(target.Id)

    uidoc.Selection.SetElementIds(ids)

    try:
        uidoc.ShowElements(target.Id)
    except:
        pass

    success_msg = u"Готово.\n\nКонтроллер найден и выделен.\n\n{}\n\nВсего найдено контроллеров: {}".format(
        get_element_info(target),
        len(controllers)
    )

    warning_msg = success_msg

    if len(controllers) > 1:
        warning_msg += u"\n\nПредупреждение:\nВ проекте найдено несколько контроллеров. Был выбран один из списка."

    show_report(
        None,
        u"Найти контроллер",
        success_msg,
        warning_msg,
        len(controllers) > 1
    )

except Stop:
    pass

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )