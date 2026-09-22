# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import os
import sys

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List
from System import Guid

from pyrevit import forms
import pp_wpf
from pp_settings import show_report, load_settings, DEFAULT_SETTINGS


_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

from pp_proxy_window import ask_proxy_data



TOOL_TITLE = u"Создать прокси-позицию"

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


CONTROLLER_FAMILY_NAME = u"PP_Семейство контроллер 1"
PROXY_FAMILY_NAME = u"PP_Прокси позиция 1"

OFFSET_STEP_MM = 50.0
MM_TO_FT = 1.0 / 304.8

_settings = load_settings()
RULE_CODES     = _settings.get("proxy_rule_codes",     DEFAULT_SETTINGS["proxy_rule_codes"])
POSITION_TYPES = _settings.get("proxy_position_types", DEFAULT_SETTINGS["proxy_position_types"])


class Stop(Exception):
    pass


def get_family_name(el):
    try:
        if el.Symbol and el.Symbol.Family:
            return el.Symbol.Family.Name
    except:
        pass
    return None


def get_location_point(el):
    try:
        loc = el.Location
        if isinstance(loc, LocationPoint):
            return loc.Point
    except:
        pass

    try:
        bb = el.get_BoundingBox(None)
        if bb:
            return XYZ(
                (bb.Min.X + bb.Max.X) / 2.0,
                (bb.Min.Y + bb.Max.Y) / 2.0,
                (bb.Min.Z + bb.Max.Z) / 2.0
            )
    except:
        pass

    return None


def find_controller():
    result = []

    collector = FilteredElementCollector(doc) \
        .OfClass(FamilyInstance) \
        .WhereElementIsNotElementType()

    for el in collector:
        if get_family_name(el) == CONTROLLER_FAMILY_NAME:
            result.append(el)

    if len(result) == 1:
        return result[0]

    if len(result) == 0:
        return None

    items = {}

    for el in result:
        items[u"ID: {}".format(el.Id.IntegerValue)] = el

    selected = forms.SelectFromList.show(
        sorted(items.keys()),
        title=u"Найдено несколько контроллеров",
        button_name=u"Выбрать"
    )

    if not selected:
        return None

    return items[selected]


def find_proxy_symbol():
    symbols = FilteredElementCollector(doc) \
        .OfClass(FamilySymbol) \
        .ToElements()

    for sym in symbols:
        try:
            if sym.Family.Name == PROXY_FAMILY_NAME:
                return sym
        except:
            pass

    return None


def count_existing_proxies():
    count = 0

    collector = FilteredElementCollector(doc) \
        .OfClass(FamilyInstance) \
        .WhereElementIsNotElementType()

    for el in collector:
        if get_family_name(el) == PROXY_FAMILY_NAME:
            count += 1

    return count


def set_param(el, name, value):
    p = el.LookupParameter(name)

    if p is None:
        return False, u"Параметр '{}' не найден".format(name)

    if p.IsReadOnly:
        return False, u"Параметр '{}' только для чтения".format(name)

    try:
        if p.StorageType == StorageType.String:
            p.Set(unicode(value))
            return True, None

        if p.StorageType == StorageType.Double:
            p.Set(float(value))
            return True, None

        if p.StorageType == StorageType.Integer:
            if isinstance(value, bool):
                p.Set(1 if value else 0)
            else:
                p.Set(int(value))
            return True, None

    except Exception as ex:
        return False, unicode(ex)

    return False, u"Неподдерживаемый тип параметра '{}'".format(name)


try:
    data = ask_proxy_data(RULE_CODES, POSITION_TYPES)

    if data is None:
        raise Stop()

    controller = find_controller()

    if controller is None:
        pp_wpf.show_report(
            u"Контроллер не найден.\n\nИскомое семейство:\n{}".format(CONTROLLER_FAMILY_NAME),
            title=u"Контроллер не найден",
            subtitle=TOOL_TITLE,
            is_error=True
        )
        raise Stop()

    proxy_symbol = find_proxy_symbol()

    if proxy_symbol is None:
        pp_wpf.show_report(
            u"Семейство прокси не загружено в проект.\n\nИскомое семейство:\n{}".format(PROXY_FAMILY_NAME),
            title=u"Семейство не загружено",
            subtitle=TOOL_TITLE,
            is_error=True
        )
        raise Stop()

    controller_pt = get_location_point(controller)

    if controller_pt is None:
        pp_wpf.show_report(
            u"Не удалось определить точку контроллера.",
            title=u"Не найдена точка контроллера",
            subtitle=TOOL_TITLE,
            is_error=True
        )
        raise Stop()

    existing_count = count_existing_proxies()
    offset = (existing_count + 1) * OFFSET_STEP_MM * MM_TO_FT

    new_pt = XYZ(
        controller_pt.X + offset,
        controller_pt.Y,
        controller_pt.Z
    )

    errors = []

    t = Transaction(doc, u"PP: Создать прокси-позицию")
    t.Start()

    if not proxy_symbol.IsActive:
        proxy_symbol.Activate()
        doc.Regenerate()

    try:
        level = doc.GetElement(controller.LevelId)
    except:
        level = None

    try:
        if level:
            proxy = doc.Create.NewFamilyInstance(
                new_pt,
                proxy_symbol,
                level,
                StructuralType.NonStructural
            )
        else:
            proxy = doc.Create.NewFamilyInstance(
                new_pt,
                proxy_symbol,
                StructuralType.NonStructural
            )
    except:
        proxy = doc.Create.NewFamilyInstance(
            new_pt,
            proxy_symbol,
            StructuralType.NonStructural
        )

    for pname, value in data.items():
        ok, err = set_param(proxy, pname, value)
        if not ok:
            errors.append(err)

    ok, err = set_param(proxy, u"PP_GUID", str(Guid.NewGuid()))
    if not ok:
        errors.append(err)

    ok, err = set_param(proxy, u"PP_Создано плагином", True)
    if not ok:
        errors.append(err)

    t.Commit()

    ids = List[ElementId]()
    ids.Add(proxy.Id)
    uidoc.Selection.SetElementIds(ids)

    try:
        uidoc.ShowElements(proxy.Id)
    except:
        pass

    success_msg = u"Готово.\n\nСоздана прокси-позиция.\n\nID: {}\nКод правила: {}\nМарка: {}\nНаименование: {}\nКоличество: {}".format(
    proxy.Id.IntegerValue,
    data.get(u"PP_Код правила", u""),
    data.get(u"ADSK_Марка", u""),
    data.get(u"ADSK_Наименование", u""),
    data.get(u"ADSK_Количество", u"")
)

    warning_msg = success_msg

    if errors:
        warning_msg += u"\n\nПредупреждения:\n" + u"\n".join(errors[:10])

    show_report(
        None,
        TOOL_TITLE,
        success_msg,
        warning_msg,
        len(errors) > 0
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
