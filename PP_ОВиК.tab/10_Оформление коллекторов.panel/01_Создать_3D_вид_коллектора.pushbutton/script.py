# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

import pp_wpf


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

PARAM_MARK = "ADSK_Марка"

# Запас вокруг коллектора, мм
OFFSET_XY_MM = 1200
OFFSET_Z_MM = 800

MM_TO_FT = 1.0 / 304.8


# ─────────────────────────────────────────────
# ФИЛЬТР ВЫБОРА
# ─────────────────────────────────────────────

class CollectorSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            if elem.Category is None:
                return False

            # Пока разрешаем выбирать любые модельные элементы.
            # Позже можно ограничить только коллекторами.
            return elem.Category.CategoryType == CategoryType.Model
        except:
            return False

    def AllowReference(self, reference, point):
        return False


# ─────────────────────────────────────────────
# ФУНКЦИИ
# ─────────────────────────────────────────────

def get_3d_view_type_id():
    view_types = FilteredElementCollector(doc).OfClass(ViewFamilyType)

    for vt in view_types:
        try:
            if vt.ViewFamily == ViewFamily.ThreeDimensional:
                return vt.Id
        except:
            pass

    return ElementId.InvalidElementId


def get_param_string(el, param_name):
    try:
        p = el.LookupParameter(param_name)
        if p:
            value = p.AsString()
            if value:
                return value.strip()
    except:
        pass

    try:
        type_el = doc.GetElement(el.GetTypeId())
        if type_el:
            p = type_el.LookupParameter(param_name)
            if p:
                value = p.AsString()
                if value:
                    return value.strip()
    except:
        pass

    return None


def clean_name(value):
    if not value:
        return ""

    bad_symbols = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']

    for s in bad_symbols:
        value = value.replace(s, "_")

    return value.strip()


def make_unique_view_name(view, base_name):
    existing_names = set()

    for v in FilteredElementCollector(doc).OfClass(View):
        try:
            existing_names.add(v.Name)
        except:
            pass

    name = base_name
    counter = 1

    while name in existing_names:
        name = u"{}_{}".format(base_name, counter)
        counter += 1

    view.Name = name
    return name


def get_element_bbox(el):
    try:
        bbox = el.get_BoundingBox(None)
        if bbox:
            return bbox
    except:
        pass

    return None


def create_section_box_from_bbox(bbox, offset_xy_ft, offset_z_ft):
    box = BoundingBoxXYZ()
    box.Transform = Transform.Identity

    box.Min = XYZ(
        bbox.Min.X - offset_xy_ft,
        bbox.Min.Y - offset_xy_ft,
        bbox.Min.Z - offset_z_ft
    )

    box.Max = XYZ(
        bbox.Max.X + offset_xy_ft,
        bbox.Max.Y + offset_xy_ft,
        bbox.Max.Z + offset_z_ft
    )

    return box


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

TOOL_TITLE = u"Создать 3D вид коллектора"


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


try:
    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        CollectorSelectionFilter(),
        u"Выберите коллекторы для создания 3D видов и нажмите Готово"
    )

    elements = []

    for r in refs:
        el = doc.GetElement(r.ElementId)
        if el:
            elements.append(el)

    if not elements:
        fail(u"Коллекторы не выбраны.", u"Нечего обрабатывать")

    view_type_id = get_3d_view_type_id()

    if view_type_id == ElementId.InvalidElementId:
        fail(
            u"Не найден тип 3D вида.\n\n"
            u"Создать вид невозможно: в проекте нет ни одного типа 3D-вида.",
            u"Нет типа 3D-вида"
        )

    offset_xy = OFFSET_XY_MM * MM_TO_FT
    offset_z = OFFSET_Z_MM * MM_TO_FT

    created = []
    skipped = []

    t = Transaction(doc, u"PP: Создать 3D виды коллекторов")
    t.Start()

    for el in elements:
        try:
            bbox = get_element_bbox(el)

            if bbox is None:
                skipped.append(
                    u"Элемент {}: не найден BoundingBox".format(
                        el.Id.IntegerValue
                    )
                )
                continue

            mark = get_param_string(el, PARAM_MARK)

            if mark:
                mark = clean_name(mark)
                base_name = u"3D_Коллектор_{}".format(mark)
            else:
                base_name = u"3D_Коллектор_ID{}".format(el.Id.IntegerValue)

            new_view = View3D.CreateIsometric(doc, view_type_id)

            new_view.IsSectionBoxActive = True

            section_box = create_section_box_from_bbox(
                bbox,
                offset_xy,
                offset_z
            )

            new_view.SetSectionBox(section_box)

            final_name = make_unique_view_name(new_view, base_name)

            created.append(final_name)

        except Exception as ex:
            skipped.append(
                u"Элемент {}: {}".format(
                    el.Id.IntegerValue,
                    unicode(ex)
                )
            )

    t.Commit()

    msg = u"Готово.\n\nВыбрано элементов: {}\nСоздано 3D видов: {}\nПропущено: {}".format(
        len(elements),
        len(created),
        len(skipped)
    )

    if created:
        msg += u"\n\nСозданные виды:\n" + u"\n".join(created[:20])

    if skipped:
        msg += u"\n\nПропуски / ошибки:\n" + u"\n".join(skipped[:10])

    pp_wpf.show_report(
        msg,
        title=u"Готово",
        subtitle=TOOL_TITLE
    )

except Stop:
    pass

except OperationCanceledException:
    pass

except Exception as ex:
    try:
        if "t" in globals() and t.HasStarted():
            t.RollBack()
    except:
        pass

    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )