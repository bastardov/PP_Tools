# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import os
import sys

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import *
from Autodesk.Revit.Exceptions import OperationCanceledException

import pp_wpf
import pp_settings


_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

from pp_section_window import ask_options



TOOL_TITLE = u"Создать разрез из 3D вида"

doc = __revit__.ActiveUIDocument.Document
view = doc.ActiveView

NO_TEMPLATE_ID = -1
TEMPLATE_SETTINGS_KEY = "create_section_from_3d_template"


# ─────────────────────────────────────────────
# ФУНКЦИИ
# ─────────────────────────────────────────────

def get_section_type_id():
    types = FilteredElementCollector(doc).OfClass(ViewFamilyType)

    for t in types:
        try:
            if t.ViewFamily == ViewFamily.Section:
                return t.Id
        except:
            pass

    return ElementId.InvalidElementId


def get_section_view_templates():
    templates = []

    for template in FilteredElementCollector(doc).OfClass(View):
        try:
            if template.IsTemplate and template.ViewType == ViewType.Section:
                templates.append({
                    "id": template.Id.IntegerValue,
                    "name": unicode(template.Name)
                })
        except:
            pass

    templates.sort(key=lambda item: item["name"].lower())
    return templates


def get_saved_template_id(templates):
    try:
        settings = pp_settings.load_settings()
        saved = settings.get(TEMPLATE_SETTINGS_KEY, {})
        saved_id = int(saved.get("id", NO_TEMPLATE_ID))
        saved_name = saved.get("name", u"")
    except:
        return NO_TEMPLATE_ID

    if saved_id == NO_TEMPLATE_ID:
        return NO_TEMPLATE_ID

    for item in templates:
        if item["id"] == saved_id and (
                not saved_name or item["name"] == saved_name):
            return item["id"]

    for item in templates:
        if saved_name and item["name"] == saved_name:
            return item["id"]

    return NO_TEMPLATE_ID


def get_template_name(templates, template_id):
    for item in templates:
        if item["id"] == template_id:
            return item["name"]

    return u""


def save_template_choice(templates, template_id):
    settings = pp_settings.load_settings()
    settings[TEMPLATE_SETTINGS_KEY] = {
        "id": template_id,
        "name": get_template_name(templates, template_id)
    }
    pp_settings.save_settings(settings)


def apply_view_template(section_view, template_id, template_name):
    if template_id == NO_TEMPLATE_ID:
        return

    template_element_id = ElementId(template_id)

    if not section_view.IsValidViewTemplate(template_element_id):
        raise Exception(
            u"Шаблон вида «{}» нельзя применить к созданному разрезу.".format(
                template_name
            )
        )

    section_view.ViewTemplateId = template_element_id


def normalize(v):
    try:
        return v.Normalize()
    except:
        return v


def neg(v):
    return XYZ(-v.X, -v.Y, -v.Z)


def local_to_world(transform, pt):
    return transform.OfPoint(pt)


def make_unique_view_name(section_view, base_name):
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

    section_view.Name = name
    return name


def make_section_name_from_3d_name(view_name):
    name = view_name

    if name.startswith(u"3D_"):
        name = name[3:]

    return u"Разрез_{}".format(name)


def create_section_box_from_3d_box(source_box, side):
    source_transform = source_box.Transform

    min_pt = source_box.Min
    max_pt = source_box.Max

    x_min = min_pt.X
    x_max = max_pt.X
    y_min = min_pt.Y
    y_max = max_pt.Y
    z_min = min_pt.Z
    z_max = max_pt.Z

    x_mid = (x_min + x_max) / 2.0
    y_mid = (y_min + y_max) / 2.0
    z_mid = (z_min + z_max) / 2.0

    x_size = x_max - x_min
    y_size = y_max - y_min
    z_size = z_max - z_min

    box_x = normalize(source_transform.BasisX)
    box_y = normalize(source_transform.BasisY)
    box_z = normalize(source_transform.BasisZ)

    up_dir = box_z

    if side == "front":
        origin_local = XYZ(x_mid, y_min, z_mid)
        view_dir = box_y
        width = x_size
        height = z_size
        depth = y_size

    elif side == "back":
        origin_local = XYZ(x_mid, y_max, z_mid)
        view_dir = neg(box_y)
        width = x_size
        height = z_size
        depth = y_size

    elif side == "left":
        origin_local = XYZ(x_min, y_mid, z_mid)
        view_dir = box_x
        width = y_size
        height = z_size
        depth = x_size

    elif side == "right":
        origin_local = XYZ(x_max, y_mid, z_mid)
        view_dir = neg(box_x)
        width = y_size
        height = z_size
        depth = x_size

    else:
        origin_local = XYZ(x_mid, y_min, z_mid)
        view_dir = box_y
        width = x_size
        height = z_size
        depth = y_size

    origin_world = local_to_world(source_transform, origin_local)

    view_dir = normalize(view_dir)
    up_dir = normalize(up_dir)

    right_dir = normalize(up_dir.CrossProduct(view_dir))

    section_transform = Transform.Identity
    section_transform.Origin = origin_world
    section_transform.BasisX = right_dir
    section_transform.BasisY = up_dir
    section_transform.BasisZ = view_dir

    section_box = BoundingBoxXYZ()
    section_box.Transform = section_transform

    section_box.Min = XYZ(
        -width / 2.0,
        -height / 2.0,
        0
    )

    section_box.Max = XYZ(
        width / 2.0,
        height / 2.0,
        depth
    )

    return section_box


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

try:
    if not isinstance(view, View3D):
        pp_wpf.show_report(
            u"Активный вид должен быть 3D видом.\n\nОткрой 3D вид коллектора и запусти инструмент снова.",
            title=u"Нужен 3D вид",
            subtitle=TOOL_TITLE,
            is_error=True
        )
        raise OperationCanceledException()

    if not view.IsSectionBoxActive:
        pp_wpf.show_report(
            u"У активного 3D вида не включен Section Box.\n\nВключи границы 3D вида и настрой коробку вокруг коллектора.",
            title=u"Не включены границы вида",
            subtitle=TOOL_TITLE,
            is_error=True
        )
        raise OperationCanceledException()

    templates = get_section_view_templates()
    saved_template_id = get_saved_template_id(templates)
    options = ask_options(templates, saved_template_id)

    if options is None:
        raise OperationCanceledException()

    side = options["side"]
    template_id = options["template_id"]
    template_name = get_template_name(templates, template_id)

    try:
        save_template_choice(templates, template_id)
    except:
        pass

    section_type_id = get_section_type_id()

    if section_type_id == ElementId.InvalidElementId:
        pp_wpf.show_report(
            u"Не найден тип вида Разрез.",
            title=u"Не найден тип вида",
            subtitle=TOOL_TITLE,
            is_error=True
        )
        raise OperationCanceledException()

    source_box = view.GetSectionBox()

    section_box = create_section_box_from_3d_box(source_box, side)

    t = Transaction(doc, u"PP: Создать разрез из 3D вида")
    t.Start()

    new_section = ViewSection.CreateSection(
        doc,
        section_type_id,
        section_box
    )

    apply_view_template(new_section, template_id, template_name)

    base_name = make_section_name_from_3d_name(view.Name)
    final_name = make_unique_view_name(new_section, base_name)

    t.Commit()

    pp_wpf.show_report(
        u"Создан разрез:\n{}\n\nШаблон вида: {}".format(
            final_name,
            template_name if template_name else u"не назначен"
        ),
        title=u"Разрез создан",
        subtitle=TOOL_TITLE
    )

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
