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
from pyrevit import forms

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
        forms.alert(
            u"Контроллер не найден в проекте.\n\nИскомое семейство:\n{}".format(
                CONTROLLER_FAMILY_NAME
            ),
            title=u"Найти контроллер",
            exitscript=True
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

        selected = forms.SelectFromList.show(
            sorted(items.keys()),
            title=u"Найдено несколько контроллеров",
            button_name=u"Выбрать"
        )

        if not selected:
            forms.alert(
                u"Контроллер не выбран.",
                title=u"Найти контроллер",
                exitscript=True
            )

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
        forms,
        u"Найти контроллер",
        success_msg,
        warning_msg,
        len(controllers) > 1
    )

except Exception as ex:
    forms.alert(
        u"Ошибка:\n\n{}".format(unicode(ex)),
        title=u"Найти контроллер"
    )