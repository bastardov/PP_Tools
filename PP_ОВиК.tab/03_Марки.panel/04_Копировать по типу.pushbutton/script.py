# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

import pp_wpf
from pp_settings import show_report

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView


ALLOWED_CATEGORIES = [
    int(BuiltInCategory.OST_DuctTerminal),
    int(BuiltInCategory.OST_DuctAccessory),
    int(BuiltInCategory.OST_MechanicalEquipment),
    int(BuiltInCategory.OST_PipeAccessory),
    int(BuiltInCategory.OST_GenericModel)
]

ALLOWED_CATEGORY_NAMES = {
    int(BuiltInCategory.OST_DuctTerminal): u"Воздухораспределители",
    int(BuiltInCategory.OST_DuctAccessory): u"Арматура воздуховодов",
    int(BuiltInCategory.OST_MechanicalEquipment): u"Оборудование",
    int(BuiltInCategory.OST_PipeAccessory): u"Арматура трубопроводов",
    int(BuiltInCategory.OST_GenericModel): u"Обобщенные модели"
}


def get_tagged_element_from_tag(tag):
    try:
        el_id = tag.TaggedLocalElementId
        if el_id and el_id != ElementId.InvalidElementId:
            return doc.GetElement(el_id)
    except:
        pass

    try:
        refs = tag.GetTaggedReferences()
        if refs and len(refs) > 0:
            return doc.GetElement(refs[0].ElementId)
    except:
        pass

    return None


def get_element_category_id(el):
    try:
        if el and el.Category:
            return el.Category.Id.IntegerValue
    except:
        pass
    return None


def get_category_name(el):
    try:
        cid = get_element_category_id(el)
        if cid in ALLOWED_CATEGORY_NAMES:
            return ALLOWED_CATEGORY_NAMES[cid]
        if el and el.Category:
            return el.Category.Name
    except:
        pass
    return u"Неизвестная категория"


def is_allowed_element(el):
    return get_element_category_id(el) in ALLOWED_CATEGORIES


def get_family_name(el):
    try:
        if hasattr(el, "Symbol") and el.Symbol and el.Symbol.Family:
            return el.Symbol.Family.Name
    except:
        pass

    try:
        t = doc.GetElement(el.GetTypeId())
        if t and hasattr(t, "FamilyName"):
            return t.FamilyName
    except:
        pass

    return None


def get_type_id(el):
    try:
        return el.GetTypeId()
    except:
        return ElementId.InvalidElementId


def get_type_name(el):
    try:
        t = doc.GetElement(el.GetTypeId())
        if t:
            p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
            if p:
                return p.AsString()
    except:
        pass

    return None


def get_element_anchor_point(el):
    if el is None:
        return None

    try:
        loc = el.Location
        if isinstance(loc, LocationPoint):
            return loc.Point
    except:
        pass

    try:
        bbox = el.get_BoundingBox(None)
        if bbox:
            return XYZ(
                (bbox.Min.X + bbox.Max.X) / 2.0,
                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                (bbox.Min.Z + bbox.Max.Z) / 2.0
            )
    except:
        pass

    return None


def xyz_sub(a, b):
    return XYZ(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def xyz_add(a, b):
    return XYZ(a.X + b.X, a.Y + b.Y, a.Z + b.Z)


def get_first_reference_from_tag(tag):
    try:
        refs = tag.GetTaggedReferences()
        if refs and len(refs) > 0:
            return refs[0]
    except:
        pass
    return None


def safe_get_leader_end(tag, ref):
    try:
        if ref:
            return tag.GetLeaderEnd(ref)
    except:
        pass
    return None


def safe_set_leader_end(tag, ref, pt):
    try:
        if ref and pt:
            tag.SetLeaderEnd(ref, pt)
            return True
    except:
        pass
    return False


def safe_get_leader_elbow(tag, ref):
    try:
        if ref:
            return tag.GetLeaderElbow(ref)
    except:
        pass
    return None


def safe_set_leader_elbow(tag, ref, pt):
    try:
        if ref and pt:
            tag.SetLeaderElbow(ref, pt)
            return True
    except:
        pass
    return False


def get_leader_end_condition(tag):
    try:
        if not tag.HasLeader:
            return "no_leader"
    except:
        pass

    try:
        cond_str = str(tag.LeaderEndCondition)
        if "Free" in cond_str:
            return "free"
        if "Attached" in cond_str:
            return "attached"
    except:
        pass

    try:
        ref = get_first_reference_from_tag(tag)
        pt = tag.GetLeaderEnd(ref)
        if pt is not None:
            return "free"
    except:
        pass

    return "attached"


def set_leader_end_condition(tag, mode):
    try:
        if mode == "no_leader":
            tag.HasLeader = False
            return True

        tag.HasLeader = True

        if mode == "free":
            tag.LeaderEndCondition = LeaderEndCondition.Free
            return True

        if mode == "attached":
            tag.LeaderEndCondition = LeaderEndCondition.Attached
            return True
    except:
        pass

    return False


def element_has_tag_in_view(el, view_id):
    collector = FilteredElementCollector(doc, view_id).OfClass(IndependentTag)

    for tag in collector:
        try:
            tagged_el = get_tagged_element_from_tag(tag)
            if tagged_el and tagged_el.Id == el.Id:
                return True
        except:
            pass

    return False


def try_change_tag_type(tag, type_id):
    try:
        if type_id and type_id != ElementId.InvalidElementId:
            tag.ChangeTypeId(type_id)
            return True
    except:
        pass

    return False


def collect_candidate_elements_in_active_view(category_id, ref_family_name, ref_type_id, ref_element_id):
    result = []
    collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()

    for el in collector:
        try:
            if el is None:
                continue

            if el.Id == ref_element_id:
                continue

            if get_element_category_id(el) != category_id:
                continue

            if get_family_name(el) != ref_family_name:
                continue

            if get_type_id(el) != ref_type_id:
                continue

            result.append(el)

        except:
            pass

    return result


class AllowedTagSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            if not isinstance(elem, IndependentTag):
                return False

            tagged_el = get_tagged_element_from_tag(elem)

            if tagged_el is None:
                return False

            return is_allowed_element(tagged_el)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


TOOL_TITLE = u"Копировать марки по типу"


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


try:
    ref_pick = uidoc.Selection.PickObject(
        ObjectType.Element,
        AllowedTagSelectionFilter(),
        u"Выберите эталонную марку"
    )

    ref_tag = doc.GetElement(ref_pick.ElementId)
    ref_element = get_tagged_element_from_tag(ref_tag)

    if ref_element is None:
        fail(u"У эталонной марки не найден связанный элемент.", u"Ошибка")

    if not is_allowed_element(ref_element):
        fail(u"Категория эталонной марки не поддерживается.", u"Ошибка")

    ref_cat_id = get_element_category_id(ref_element)
    ref_cat_name = get_category_name(ref_element)
    ref_family_name = get_family_name(ref_element)
    ref_type_id = get_type_id(ref_element)
    ref_type_name = get_type_name(ref_element)

    ref_anchor = get_element_anchor_point(ref_element)
    ref_head = ref_tag.TagHeadPosition
    ref_tag_type_id = ref_tag.GetTypeId()

    if ref_anchor is None:
        fail(u"Не удалось определить базовую точку эталонного элемента.", u"Ошибка")

    if ref_head is None:
        fail(u"Не удалось определить положение головы эталонной марки.", u"Ошибка")

    head_offset = xyz_sub(ref_head, ref_anchor)

    ref_ref = get_first_reference_from_tag(ref_tag)
    ref_leader_mode = get_leader_end_condition(ref_tag)

    ref_leader_end = safe_get_leader_end(ref_tag, ref_ref)
    ref_leader_elbow = safe_get_leader_elbow(ref_tag, ref_ref)

    end_offset_from_head = None
    elbow_offset_from_head = None

    if ref_leader_end is not None:
        end_offset_from_head = xyz_sub(ref_leader_end, ref_head)

    if ref_leader_elbow is not None:
        elbow_offset_from_head = xyz_sub(ref_leader_elbow, ref_head)

    candidates = collect_candidate_elements_in_active_view(
        ref_cat_id,
        ref_family_name,
        ref_type_id,
        ref_element.Id
    )

    if not candidates:
        fail(
            u"Подходящие элементы не найдены.\n\nКатегория: {}\nСемейство: {}\nТип: {}".format(
                ref_cat_name,
                ref_family_name,
                ref_type_name
            ),
            u"Копировать марки по типу"
        )

    created = []
    skipped = []

    t = Transaction(doc, u"PP: Копировать марки по типу")
    t.Start()

    for el in candidates:
        try:
            if element_has_tag_in_view(el, view.Id):
                skipped.append(u"Элемент {}: марка уже есть".format(el.Id.IntegerValue))
                continue

            target_anchor = get_element_anchor_point(el)

            if target_anchor is None:
                skipped.append(u"Элемент {}: не найдена базовая точка".format(el.Id.IntegerValue))
                continue

            new_head = xyz_add(target_anchor, head_offset)
            add_leader_on_create = ref_leader_mode != "no_leader"

            try:
                new_tag = IndependentTag.Create(
                    doc,
                    view.Id,
                    Reference(el),
                    add_leader_on_create,
                    TagMode.TM_ADDBY_CATEGORY,
                    TagOrientation.Horizontal,
                    new_head
                )
            except:
                new_tag = None

            if new_tag is None:
                skipped.append(u"Элемент {}: не удалось создать марку".format(el.Id.IntegerValue))
                continue

            try_change_tag_type(new_tag, ref_tag_type_id)

            set_leader_end_condition(new_tag, ref_leader_mode)
            new_tag.TagHeadPosition = new_head

            target_ref = get_first_reference_from_tag(new_tag)

            set_leader_end_condition(new_tag, ref_leader_mode)
            new_tag.TagHeadPosition = new_head

            if ref_leader_mode == "attached":
                if elbow_offset_from_head is not None and target_ref is not None:
                    target_elbow = xyz_add(new_head, elbow_offset_from_head)
                    safe_set_leader_elbow(new_tag, target_ref, target_elbow)

            elif ref_leader_mode == "free":
                target_elbow = None
                target_end = None

                if elbow_offset_from_head is not None:
                    target_elbow = xyz_add(new_head, elbow_offset_from_head)

                if end_offset_from_head is not None:
                    target_end = xyz_add(new_head, end_offset_from_head)

                if target_elbow is not None and target_ref is not None:
                    safe_set_leader_elbow(new_tag, target_ref, target_elbow)

                if target_end is not None and target_ref is not None:
                    safe_set_leader_end(new_tag, target_ref, target_end)

                set_leader_end_condition(new_tag, "free")

                if target_elbow is not None and target_ref is not None:
                    safe_set_leader_elbow(new_tag, target_ref, target_elbow)

                if target_end is not None and target_ref is not None:
                    safe_set_leader_end(new_tag, target_ref, target_end)

                new_tag.TagHeadPosition = new_head
                set_leader_end_condition(new_tag, "free")

            elif ref_leader_mode == "no_leader":
                try:
                    new_tag.HasLeader = False
                except:
                    pass

            created.append(new_tag.Id.IntegerValue)

        except Exception as ex:
            try:
                skipped.append(u"Элемент {}: {}".format(el.Id.IntegerValue, str(ex)))
            except:
                skipped.append(u"Ошибка: {}".format(str(ex)))

    t.Commit()

    success_msg = u"Готово.\n\nКатегория: {}\nСемейство: {}\nТип: {}\n\nНайдено элементов: {}\nСоздано марок: {}\nПропущено: {}".format(
        ref_cat_name,
        ref_family_name,
        ref_type_name,
        len(candidates),
        len(created),
        len(skipped)
    )

    warning_msg = success_msg

    if skipped:
        warning_msg += u"\n\nПервые ошибки/пропуски:\n" + u"\n".join(skipped[:15])

    show_report(
        None,
        u"Копировать марки по типу",
        success_msg,
        warning_msg,
        len(skipped) > 0
    )

except Stop:
    pass

except OperationCanceledException:
    pass

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )