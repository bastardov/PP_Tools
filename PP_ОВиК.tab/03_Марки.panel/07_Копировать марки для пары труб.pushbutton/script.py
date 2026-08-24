# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

clr.AddReference('System')
from System.Collections.Generic import List

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

import pp_wpf
from pp_settings import show_report, load_settings

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView


settings = load_settings()

MAX_PAIR_DISTANCE_MM = float(settings.get("pipe_pair_distance_mm", 600.0))
MAX_DIST = MAX_PAIR_DISTANCE_MM / 304.8

TARGET_CATEGORIES = [
    int(BuiltInCategory.OST_PipeCurves)
]


def xyz_add(a, b):
    return XYZ(a.X + b.X, a.Y + b.Y, a.Z + b.Z)


def xyz_sub(a, b):
    return XYZ(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def distance(a, b):
    try:
        return a.DistanceTo(b)
    except:
        return 999999999


def average_point(p1, p2):
    return XYZ(
        (p1.X + p2.X) / 2.0,
        (p1.Y + p2.Y) / 2.0,
        (p1.Z + p2.Z) / 2.0
    )


def get_category_id(el):
    try:
        if el and el.Category:
            return el.Category.Id.IntegerValue
    except:
        pass
    return None


def is_pipe(el):
    return get_category_id(el) in TARGET_CATEGORIES


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


def get_tagged_refs(tag):
    try:
        return list(tag.GetTaggedReferences())
    except:
        return []


def get_anchor_point(el):
    try:
        loc = el.Location
        if isinstance(loc, LocationCurve):
            return loc.Curve.Evaluate(0.5, True)
    except:
        pass

    try:
        bbox = el.get_BoundingBox(view)
        if bbox:
            return XYZ(
                (bbox.Min.X + bbox.Max.X) / 2.0,
                (bbox.Min.Y + bbox.Max.Y) / 2.0,
                (bbox.Min.Z + bbox.Max.Z) / 2.0
            )
    except:
        pass

    return None


def get_tag_head(tag):
    try:
        return tag.TagHeadPosition
    except:
        return None


def set_tag_head(tag, pt):
    try:
        tag.TagHeadPosition = pt
        return True
    except:
        return False


def set_free_leader(tag):
    try:
        tag.HasLeader = True
    except:
        pass

    try:
        tag.LeaderEndCondition = LeaderEndCondition.Free
        return True
    except:
        return False


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


def try_change_type(tag, type_id):
    try:
        if type_id and type_id != ElementId.InvalidElementId:
            tag.ChangeTypeId(type_id)
            return True
    except:
        pass
    return False


def create_tag(el, head_pt):
    try:
        return IndependentTag.Create(
            doc,
            view.Id,
            Reference(el),
            True,
            TagMode.TM_ADDBY_CATEGORY,
            TagOrientation.Horizontal,
            head_pt
        )
    except:
        return None


def add_second_reference(tag, el2):
    try:
        refs = List[Reference]()
        refs.Add(Reference(el2))
        tag.AddReferences(refs)
        return True
    except:
        return False


def get_ref_element_id(ref):
    try:
        return ref.ElementId.IntegerValue
    except:
        return None


def sort_refs_by_element_order(refs, el1, el2):
    result = []
    id1 = el1.Id.IntegerValue
    id2 = el2.Id.IntegerValue

    r1 = None
    r2 = None

    for r in refs:
        rid = get_ref_element_id(r)
        if rid == id1:
            r1 = r
        elif rid == id2:
            r2 = r

    if r1:
        result.append(r1)
    if r2:
        result.append(r2)

    for r in refs:
        if r not in result:
            result.append(r)

    return result


def collect_ref_leader_end_points(ref_tag):
    pts = []
    for r in get_tagged_refs(ref_tag):
        pt = safe_get_leader_end(ref_tag, r)
        if pt:
            pts.append(pt)
    return pts


def collect_ref_elbow_offsets_from_head(ref_tag, ref_head):
    offsets = []
    for r in get_tagged_refs(ref_tag):
        elbow = safe_get_leader_elbow(ref_tag, r)
        if elbow:
            offsets.append(xyz_sub(elbow, ref_head))
        else:
            offsets.append(None)
    return offsets


def make_auto_pairs(elements):
    items = []

    for el in elements:
        p = get_anchor_point(el)
        if p:
            items.append({
                "el": el,
                "pt": p
            })

    unused = list(items)
    pairs = []
    skipped = []

    while len(unused) > 1:
        base = unused[0]

        nearest = None
        nearest_dist = 999999999

        for other in unused[1:]:
            d = distance(base["pt"], other["pt"])
            if d < nearest_dist:
                nearest = other
                nearest_dist = d

        if nearest is None:
            skipped.append(u"Элемент {}: не найдена пара".format(base["el"].Id.IntegerValue))
            unused.remove(base)
            continue

        if nearest_dist > MAX_DIST:
            skipped.append(
                u"Элемент {}: ближайшая пара {} слишком далеко: {} мм".format(
                    base["el"].Id.IntegerValue,
                    nearest["el"].Id.IntegerValue,
                    round(nearest_dist * 304.8, 1)
                )
            )
            unused.remove(base)
            continue

        pairs.append((base, nearest))
        unused.remove(base)
        unused.remove(nearest)

    if len(unused) == 1:
        skipped.append(
            u"Элемент {}: остался без пары".format(
                unused[0]["el"].Id.IntegerValue
            )
        )

    return pairs, skipped


def element_has_tag_in_view(el):
    try:
        collector = FilteredElementCollector(doc, view.Id).OfClass(IndependentTag)

        for tag in collector:
            refs = get_tagged_refs(tag)
            for r in refs:
                try:
                    if r.ElementId == el.Id:
                        return True
                except:
                    pass
    except:
        pass

    return False


class PipeTagSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            if not isinstance(elem, IndependentTag):
                return False

            tagged_el = get_tagged_element_from_tag(elem)

            if tagged_el is None:
                return False

            return is_pipe(tagged_el)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


class PipeElementSelectionFilter(ISelectionFilter):
    def __init__(self, required_category_id):
        self.required_category_id = required_category_id

    def AllowElement(self, elem):
        try:
            return get_category_id(elem) == self.required_category_id
        except:
            return False

    def AllowReference(self, reference, point):
        return False


TOOL_TITLE = u"Копировать марки труб"


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


try:
    ref_pick = uidoc.Selection.PickObject(
        ObjectType.Element,
        PipeTagSelectionFilter(),
        u"Выберите эталонную сдвоенную марку трубы"
    )

    ref_tag = doc.GetElement(ref_pick.ElementId)
    ref_el = get_tagged_element_from_tag(ref_tag)

    if ref_el is None:
        fail(
            u"У эталонной марки не найден элемент.",
            u"Ошибка"
        )

    ref_cat_id = get_category_id(ref_el)
    ref_head = get_tag_head(ref_tag)
    ref_type_id = ref_tag.GetTypeId()

    ref_end_points = collect_ref_leader_end_points(ref_tag)

    if len(ref_end_points) >= 2:
        ref_end_center = average_point(ref_end_points[0], ref_end_points[1])
        ref_end_offsets = [
            xyz_sub(ref_end_points[0], ref_end_center),
            xyz_sub(ref_end_points[1], ref_end_center)
        ]
        ref_pair_center = ref_end_center
    else:
        ref_end_offsets = []
        ref_pair_center = get_anchor_point(ref_el)

    if ref_head is None or ref_pair_center is None:
        fail(
            u"Не удалось получить голову или центр эталона.",
            u"Ошибка"
        )

    head_offset_from_pair_center = xyz_sub(ref_head, ref_pair_center)
    ref_elbow_offsets = collect_ref_elbow_offsets_from_head(ref_tag, ref_head)

    target_picks = uidoc.Selection.PickObjects(
        ObjectType.Element,
        PipeElementSelectionFilter(ref_cat_id),
        u"Выберите трубы пачкой. Пары будут найдены автоматически по ближайшему соседу."
    )

    elements = []

    for p in target_picks:
        el = doc.GetElement(p.ElementId)
        if el and el.Id != ref_el.Id:
            elements.append(el)

    if len(elements) < 2:
        fail(
            u"Нужно выбрать минимум 2 трубы.",
            u"Копировать марки труб"
        )

    pairs, pair_skipped = make_auto_pairs(elements)

    if len(pairs) == 0:
        msg = u"Пары не найдены."
        if pair_skipped:
            msg += u"\n\n" + u"\n".join(pair_skipped[:15])

        fail(
            msg,
            u"Копировать марки труб"
        )

    created = []
    skipped = list(pair_skipped)

    t = Transaction(doc, u"PP: Копировать марки труб")
    t.Start()

    pair_index = 0

    for base, nearest in pairs:
        pair_index += 1

        el1 = base["el"]
        el2 = nearest["el"]
        p1 = base["pt"]
        p2 = nearest["pt"]

        try:
            if element_has_tag_in_view(el1) or element_has_tag_in_view(el2):
                skipped.append(
                    u"Пара {}: у одной из труб уже есть марка".format(pair_index)
                )
                continue

            pair_center = average_point(p1, p2)
            new_head = xyz_add(pair_center, head_offset_from_pair_center)

            new_tag = create_tag(el1, new_head)

            if new_tag is None:
                skipped.append(
                    u"Пара {}: не удалось создать марку".format(pair_index)
                )
                continue

            try_change_type(new_tag, ref_type_id)
            add_second_reference(new_tag, el2)

            set_tag_head(new_tag, new_head)
            set_free_leader(new_tag)

            refs = get_tagged_refs(new_tag)
            refs = sort_refs_by_element_order(refs, el1, el2)

            if len(ref_end_offsets) >= 2:
                end_points = [
                    xyz_add(pair_center, ref_end_offsets[0]),
                    xyz_add(pair_center, ref_end_offsets[1])
                ]
            else:
                end_points = [p1, p2]

            for idx, r in enumerate(refs):
                if idx < len(end_points):
                    safe_set_leader_end(new_tag, r, end_points[idx])

            for idx, r in enumerate(refs):
                if idx < len(ref_elbow_offsets):
                    elbow_offset = ref_elbow_offsets[idx]
                    if elbow_offset is not None:
                        target_elbow = xyz_add(new_head, elbow_offset)
                        safe_set_leader_elbow(new_tag, r, target_elbow)

            set_free_leader(new_tag)
            set_tag_head(new_tag, new_head)

            for idx, r in enumerate(refs):
                if idx < len(end_points):
                    safe_set_leader_end(new_tag, r, end_points[idx])

            created.append(new_tag.Id.IntegerValue)

        except Exception as ex:
            skipped.append(
                u"Пара {}: {}".format(pair_index, str(ex))
            )

    t.Commit()

    success_msg = u"Готово.\n\nМаксимальное расстояние пары: {} мм\nВыбрано труб: {}\nНайдено пар: {}\nСоздано марок: {}\nПропущено: {}".format(
        MAX_PAIR_DISTANCE_MM,
        len(elements),
        len(pairs),
        len(created),
        len(skipped)
    )

    warning_msg = success_msg

    if skipped:
        warning_msg += u"\n\nПервые ошибки/пропуски:\n" + u"\n".join(skipped[:15])

    show_report(
        None,
        u"Копировать марки труб",
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