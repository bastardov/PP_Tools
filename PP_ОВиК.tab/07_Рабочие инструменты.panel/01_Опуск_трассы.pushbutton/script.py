# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import os
import sys
import clr
import math

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from System.Collections.Generic import List

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.DB.Mechanical import Duct
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import script

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_drop_window


TITLE = u"Опуск / Подъем трассы"


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта, транзакция откатывается."""
    pass


class Skip(Exception):
    u"""Участок обработать нельзя. При одном участке = Stop, при нескольких — пропуск."""
    pass


def fail(message):
    raise Stop(message)


def fmt(value):
    u"""500.0 -> «500», 512.5 -> «512.5». Углы бывают дробные."""
    try:
        if float(value) == int(float(value)):
            return unicode(int(float(value)))
    except Exception:
        pass

    return unicode(value)


def rollback():
    u"""Откат незакрытой транзакции. Вызывается из каждой ветки except."""
    try:
        if "t" in globals() and t.HasStarted() and not t.HasEnded():
            t.RollBack()
    except Exception:
        pass

    try:
        if "tg" in globals() and tg.HasStarted() and not tg.HasEnded():
            tg.RollBack()
    except Exception:
        pass


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


my_config = script.get_config()


def load_settings():
    u"""Запомненные параметры окна. Уровень храним по имени: id в другой модели чужой."""
    saved = {}

    try:
        saved[u"mode"] = my_config.get_option("direction", u"Опуск")
    except:
        saved[u"mode"] = u"Опуск"

    try:
        saved[u"angle"] = float(my_config.get_option("angle", 90.0))
    except:
        saved[u"angle"] = 90.0

    try:
        saved[u"value_mm"] = float(my_config.get_option("value_mm", 500.0))
    except:
        saved[u"value_mm"] = 500.0

    try:
        saved[u"level_key"] = my_config.get_option("level_key", None)
    except:
        saved[u"level_key"] = None

    try:
        saved[u"ref_kind"] = my_config.get_option("ref_kind", u"низ")
    except:
        saved[u"ref_kind"] = u"низ"

    try:
        elev = my_config.get_option("elev_mm", None)
        saved[u"elev_mm"] = None if elev is None else float(elev)
    except:
        saved[u"elev_mm"] = None

    try:
        saved[u"multi"] = bool(my_config.get_option("multi", False))
    except:
        saved[u"multi"] = False

    # По умолчанию решётки и диффузоры остаются на месте: перенос их по высоте
    # почти всегда неверен (потолок никуда не уехал)
    try:
        saved[u"move_equipment"] = bool(my_config.get_option("move_equipment", False))
    except:
        saved[u"move_equipment"] = False

    return saved


def save_settings(result):
    try:
        my_config.direction = result[u"mode"]
        my_config.angle = result[u"angle"]

        if result[u"value_mm"] is not None:
            my_config.value_mm = result[u"value_mm"]

        if result[u"level_key"] is not None:
            my_config.level_key = result[u"level_key"]

        my_config.ref_kind = result[u"ref_kind"]

        if result[u"elev_mm"] is not None:
            my_config.elev_mm = result[u"elev_mm"]

        my_config.multi = result[u"multi"]
        my_config.move_equipment = result[u"move_equipment"]

        script.save_config()
    except:
        pass


MM_TO_FT = 1.0 / 304.8
MIN_SEGMENT_MM = 100.0
MIN_SEGMENT_FT = MIN_SEGMENT_MM * MM_TO_FT
MAX_CHAIN_DEPTH = 20


ALLOWED_MOVE_CATEGORIES = [
    int(BuiltInCategory.OST_DuctCurves),
    int(BuiltInCategory.OST_DuctFitting),
    int(BuiltInCategory.OST_DuctAccessory),
    int(BuiltInCategory.OST_DuctTerminal),
    int(BuiltInCategory.OST_FlexDuctCurves),

    int(BuiltInCategory.OST_PipeCurves),
    int(BuiltInCategory.OST_PipeFitting),
    int(BuiltInCategory.OST_PipeAccessory),
    int(BuiltInCategory.OST_FlexPipeCurves)
]


# Арматура считается концевым оборудованием, только если подключена одним концом
EQUIPMENT_IF_LEAF_CATEGORIES = [
    int(BuiltInCategory.OST_DuctAccessory),
    int(BuiltInCategory.OST_PipeAccessory)
]


class MepCurveSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return isinstance(elem, Pipe) or isinstance(elem, Duct)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


class SameElementPointSelectionFilter(ISelectionFilter):
    u"""Точку берём только на уже выбранных участках."""

    def __init__(self, element_ids):
        self.ids = set()

        for eid in element_ids:
            try:
                self.ids.add(eid.IntegerValue)
            except:
                pass

    def AllowElement(self, elem):
        try:
            return elem.Id.IntegerValue in self.ids
        except:
            return False

    def AllowReference(self, reference, point):
        try:
            return reference.ElementId.IntegerValue in self.ids
        except:
            return False


def pick_point_on_element(element_ids, message):
    ref = uidoc.Selection.PickObject(
        ObjectType.PointOnElement,
        SameElementPointSelectionFilter(element_ids),
        message
    )

    try:
        return ref.GlobalPoint
    except:
        return None


def pick_targets(multi):
    u"""Участки для обработки. При multi уважаем то, что уже выделено в модели."""
    if not multi:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            MepCurveSelectionFilter(),
            u"Выберите трубу или воздуховод"
        )

        return [doc.GetElement(ref.ElementId)]

    preselected = []

    try:
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)

            if isinstance(el, Pipe) or isinstance(el, Duct):
                preselected.append(el)
    except:
        preselected = []

    if preselected:
        return preselected

    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        MepCurveSelectionFilter(),
        u"Выберите трубы и воздуховоды, затем нажмите «Готово»"
    )

    return [doc.GetElement(r.ElementId) for r in refs]


def element_label(el):
    u"""«Воздуховоды 123456» — для строк отчёта."""
    try:
        name = el.Category.Name
    except:
        name = u"Элемент"

    try:
        return u"{} {}".format(name, el.Id.IntegerValue)
    except:
        return name


def format_elev_m(value_mm):
    u"""2750 -> «+2.750», -150 -> «-0.150», 0 -> «±0.000»."""
    try:
        value = float(value_mm)
    except:
        return u""

    if abs(value) < 0.5:
        return u"±0.000"

    sign = u"+" if value > 0 else u"-"

    return u"{}{:.3f}".format(sign, abs(value) / 1000.0)


def collect_levels():
    u"""Уровни модели снизу вверх. Отметка — та же система координат, что и Z элементов."""
    levels = []

    collector = FilteredElementCollector(doc)\
        .OfClass(Level)\
        .WhereElementIsNotElementType()

    for lv in collector:
        try:
            elev_ft = lv.Elevation
            name = lv.Name
        except:
            continue

        levels.append({
            u"id": lv.Id.IntegerValue,
            u"key": name,
            u"elev_ft": elev_ft,
            u"elev_mm": elev_ft / MM_TO_FT,
        })

    levels.sort(key=lambda item: item[u"elev_ft"])

    for level in levels:
        level[u"label"] = u"{}    {}".format(
            level[u"key"],
            format_elev_m(level[u"elev_mm"])
        )

    return levels


def ask_move_settings():
    u"""Показывает WPF-окно параметров. Возврат: словарь параметров или None."""
    saved = load_settings()

    levels = collect_levels()

    result = pp_drop_window.ask_settings(
        saved[u"mode"],
        saved[u"angle"],
        saved[u"value_mm"],
        levels,
        saved[u"level_key"],
        saved[u"ref_kind"],
        saved[u"elev_mm"],
        saved[u"multi"],
        saved[u"move_equipment"]
    )

    if result is None:
        return None

    save_settings(result)

    result[u"levels"] = levels

    return result


def half_size_ft(el, point):
    u"""Половина высоты сечения у ближайшего к точке коннектора (без изоляции).

    None — размер определить не удалось; вызывающий код должен об этом сказать.
    """
    conn = nearest_connector(el, point)

    if conn is not None:
        try:
            if conn.Shape == ConnectorProfileType.Round:
                return conn.Radius
        except:
            pass

        try:
            height = conn.Height

            if height and height > 0:
                return height / 2.0
        except:
            pass

    # Запасной путь: габаритные параметры самого элемента
    for name in ("RBS_CURVE_HEIGHT_PARAM",
                 "RBS_CURVE_DIAMETER_PARAM",
                 "RBS_PIPE_OUTER_DIAMETER",
                 "RBS_PIPE_DIAMETER_PARAM"):
        bip = getattr(BuiltInParameter, name, None)

        if bip is None:
            continue

        try:
            param = el.get_Parameter(bip)

            if param is None or not param.HasValue:
                continue

            value = param.AsDouble()

            if value and value > 0:
                return value / 2.0
        except:
            pass

    return None


def xyz_add(a, b):
    return XYZ(a.X + b.X, a.Y + b.Y, a.Z + b.Z)


def xyz_sub(a, b):
    return XYZ(a.X - b.X, a.Y - b.Y, a.Z - b.Z)


def xyz_mul(a, k):
    return XYZ(a.X * k, a.Y * k, a.Z * k)


def dot(a, b):
    return a.X * b.X + a.Y * b.Y + a.Z * b.Z


def normalize(v):
    length = math.sqrt(v.X * v.X + v.Y * v.Y + v.Z * v.Z)

    if length < 0.0000001:
        return None

    return XYZ(v.X / length, v.Y / length, v.Z / length)


def get_curve(el):
    try:
        loc = el.Location
        if isinstance(loc, LocationCurve):
            return loc.Curve
    except:
        pass

    return None


def project_point_to_line_param(point, start, end):
    v = xyz_sub(end, start)
    w = xyz_sub(point, start)

    denom = dot(v, v)

    if abs(denom) < 0.0000001:
        return None

    t = dot(w, v) / denom

    if t < 0:
        t = 0

    if t > 1:
        t = 1

    return t


def project_param_raw(point, start, end):
    u"""То же, что project_point_to_line_param, но БЕЗ обрезки в [0; 1].

    Нужно при работе с несколькими участками: по значению вне [0; 1] видно,
    что общая точка разрыва не попала на этот участок.
    """
    v = xyz_sub(end, start)
    w = xyz_sub(point, start)

    denom = dot(v, v)

    if abs(denom) < 0.0000001:
        return None

    return dot(w, v) / denom


def point_on_line(start, end, t):
    return xyz_add(start, xyz_mul(xyz_sub(end, start), t))


def get_level_id(el):
    try:
        if hasattr(el, "ReferenceLevel") and el.ReferenceLevel:
            return el.ReferenceLevel.Id
    except:
        pass

    for bip in [
        BuiltInParameter.RBS_START_LEVEL_PARAM,
        BuiltInParameter.RBS_REFERENCE_LEVEL_PARAM
    ]:
        try:
            p = el.get_Parameter(bip)
            if p:
                return p.AsElementId()
        except:
            pass

    return ElementId.InvalidElementId


def get_system_type_id(el):
    for bip in [
        BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM,
        BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM
    ]:
        try:
            p = el.get_Parameter(bip)
            if p:
                return p.AsElementId()
        except:
            pass

    return ElementId.InvalidElementId


def copy_param_value(source, target, bip):
    try:
        ps = source.get_Parameter(bip)
        pt = target.get_Parameter(bip)

        if ps is None or pt is None:
            return False

        if pt.IsReadOnly:
            return False

        st = ps.StorageType

        if st == StorageType.Double:
            pt.Set(ps.AsDouble())
            return True

        if st == StorageType.Integer:
            pt.Set(ps.AsInteger())
            return True

        if st == StorageType.String:
            pt.Set(ps.AsString())
            return True

        if st == StorageType.ElementId:
            pt.Set(ps.AsElementId())
            return True

    except:
        pass

    return False


def copy_size_params(source, target):
    for bip in [
        BuiltInParameter.RBS_PIPE_DIAMETER_PARAM,
        BuiltInParameter.RBS_CURVE_DIAMETER_PARAM,
        BuiltInParameter.RBS_CURVE_WIDTH_PARAM,
        BuiltInParameter.RBS_CURVE_HEIGHT_PARAM
    ]:
        copy_param_value(source, target, bip)


def create_same_mep(source, p1, p2):
    type_id = source.GetTypeId()
    level_id = get_level_id(source)
    system_type_id = get_system_type_id(source)

    if system_type_id == ElementId.InvalidElementId:
        return None, u"Не найден тип системы."

    if level_id == ElementId.InvalidElementId:
        return None, u"Не найден уровень."

    try:
        if isinstance(source, Pipe):
            new_el = Pipe.Create(
                doc,
                system_type_id,
                type_id,
                level_id,
                p1,
                p2
            )
            copy_size_params(source, new_el)
            return new_el, None

        if isinstance(source, Duct):
            new_el = Duct.Create(
                doc,
                system_type_id,
                type_id,
                level_id,
                p1,
                p2
            )
            copy_size_params(source, new_el)
            return new_el, None

    except Exception as ex:
        return None, unicode(ex)

    return None, u"Элемент не является трубой или воздуховодом."


def get_connectors(el):
    result = []

    try:
        for c in el.ConnectorManager.Connectors:
            result.append(c)
        return result
    except:
        pass

    try:
        for c in el.MEPModel.ConnectorManager.Connectors:
            result.append(c)
        return result
    except:
        pass

    return result


def nearest_connector(el, point):
    best = None
    best_dist = 999999999.0

    for c in get_connectors(el):
        try:
            d = c.Origin.DistanceTo(point)
            if d < best_dist:
                best = c
                best_dist = d
        except:
            pass

    return best


def cross(a, b):
    return a.CrossProduct(b)


def signed_angle(a, b, axis):
    u"""Угол поворота от a к b вокруг axis (радианы, правило правой руки)."""
    return math.atan2(dot(cross(a, b), axis), dot(a, b))


def profile_axes(el, point):
    u"""Оси профиля (BasisX, BasisY) у ближайшего к точке коннектора.

    None — круглое сечение, гибкий элемент или коннектор не найден:
    ориентация профиля в этих случаях роли не играет.
    """
    c = nearest_connector(el, point)

    if c is None:
        return None

    try:
        if c.Shape == ConnectorProfileType.Round:
            return None
    except:
        return None

    try:
        cs = c.CoordinateSystem
        bx = normalize(cs.BasisX)
        by = normalize(cs.BasisY)
    except:
        return None

    if bx is None or by is None:
        return None

    return bx, by


def perpendicular_part(v, axis_unit):
    u"""Составляющая вектора v, перпендикулярная оси axis_unit."""
    return normalize(
        xyz_sub(v, xyz_mul(axis_unit, dot(v, axis_unit)))
    )


def align_profile(el, p_from, p_to, use_basis_x, target_dir):
    u"""Доворачивает элемент вокруг собственной оси так, чтобы ось профиля легла на target_dir.

    Revit сам выбирает разворот прямоугольного сечения у нового участка и для
    вертикали всегда берёт свой вариант — из-за этого ширина и высота
    воздуховода на стояке меняются местами. Здесь разворот приводится
    к сечению исходной трассы.
    """
    if target_dir is None or use_basis_x is None:
        return

    axis_unit_el = normalize(xyz_sub(p_to, p_from))

    if axis_unit_el is None:
        return

    axes = profile_axes(el, p_from)

    if axes is None:
        return

    current = axes[0] if use_basis_x else axes[1]

    current = perpendicular_part(current, axis_unit_el)
    target = perpendicular_part(target_dir, axis_unit_el)

    if current is None or target is None:
        return

    ang = signed_angle(current, target, axis_unit_el)

    # Профиль симметричен: поворот на 180° ничего не меняет — берём кратчайший
    half = math.pi / 2.0

    while ang > half:
        ang -= math.pi

    while ang <= -half:
        ang += math.pi

    if abs(ang) < 0.0001:
        return

    try:
        rotation_axis = Line.CreateBound(
            p_from,
            xyz_add(p_from, axis_unit_el)
        )

        ElementTransformUtils.RotateElement(doc, el.Id, rotation_axis, ang)
    except:
        pass


def connect_with_elbow(el1, p1, el2, p2):
    c1 = nearest_connector(el1, p1)
    c2 = nearest_connector(el2, p2)

    if c1 is None or c2 is None:
        return False, u"Не найдены коннекторы."

    try:
        doc.Create.NewElbowFitting(c1, c2)
        return True, None
    except Exception as ex:
        return False, unicode(ex)


def get_category_id(el):
    try:
        if el and el.Category:
            return el.Category.Id.IntegerValue
    except:
        pass

    return None


def is_allowed_to_move(el):
    return get_category_id(el) in ALLOWED_MOVE_CATEGORIES


def connected_count(el):
    u"""Сколько коннекторов элемента реально к чему-то подключены."""
    count = 0

    for c in get_connectors(el):
        try:
            if c.IsConnected:
                count += 1
        except:
            pass

    return count


def is_equipment(el):
    u"""Концевое оборудование: решётка, диффузор или концевая арматура.

    Арматура ПОСРЕДИ трассы (два подключения) оборудованием не считается —
    иначе в перемещённой трассе останется дыра на месте клапана.
    """
    cid = get_category_id(el)

    if cid is None:
        return False

    if cid == int(BuiltInCategory.OST_DuctTerminal):
        return True

    if cid in EQUIPMENT_IF_LEAF_CATEGORIES:
        return connected_count(el) <= 1

    return False


def get_connected_owner_ids_from_connector(connector, source_id):
    result = []

    try:
        for ref in connector.AllRefs:
            try:
                owner = ref.Owner

                if owner and owner.Id.IntegerValue != source_id:
                    result.append(owner.Id)
            except:
                pass
    except:
        pass

    return result


def collect_connected_chain_from_start_ids(start_ids, blocked_ids, max_depth,
                                           move_equipment=True):
    u"""Что поедет вслед за перемещаемым концом.

    blocked_ids — сам участок и остальные выбранные участки: сквозь них
    цепочка не идёт и переносить их не надо (каждый обрабатывается сам).

    move_equipment=False — концевое оборудование (решётки, диффузоры,
    концевая арматура) остаётся на месте: в цепочку не попадает и дальше
    сквозь него не идём. Такие элементы возвращаются вторым значением.

    Возврат: (List[ElementId] для переноса, set() id оставшихся на месте).
    """
    visited = set()
    stationary = set()
    queue = []

    for eid in start_ids:
        try:
            queue.append((eid, 0))
        except:
            pass

    while queue:
        eid, depth = queue.pop(0)

        if eid.IntegerValue in visited:
            continue

        if eid.IntegerValue in blocked_ids:
            continue

        el = doc.GetElement(eid)

        if el is None:
            continue

        if not is_allowed_to_move(el):
            continue

        if not move_equipment and is_equipment(el):
            # Оборудование остаётся на месте, и цепочка сквозь него не идёт
            stationary.add(eid.IntegerValue)
            continue

        visited.add(eid.IntegerValue)

        if depth >= max_depth:
            continue

        for c in get_connectors(el):
            try:
                for ref in c.AllRefs:
                    owner = ref.Owner

                    if owner is None:
                        continue

                    if owner.Id.IntegerValue in blocked_ids:
                        continue

                    if owner.Id.IntegerValue not in visited:
                        queue.append((owner.Id, depth + 1))
            except:
                pass

    ids = List[ElementId]()

    for int_id in visited:
        ids.Add(ElementId(int_id))

    return ids, stationary


def disconnect_from_stationary(chain_ids, stationary_ids):
    u"""Разорвать стыки между уезжающей цепочкой и оставшимся оборудованием.

    Без этого Revit оставит «подключение» между разъехавшимися элементами.
    Вызывать внутри транзакции, до переноса.
    """
    if not stationary_ids:
        return 0

    count = 0

    for eid in chain_ids:
        el = doc.GetElement(eid)

        if el is None:
            continue

        for c in get_connectors(el):
            try:
                refs = [r for r in c.AllRefs]
            except:
                continue

            for ref in refs:
                try:
                    owner = ref.Owner

                    if owner is None:
                        continue

                    if owner.Id.IntegerValue not in stationary_ids:
                        continue

                    c.DisconnectFrom(ref)
                    count += 1
                except:
                    pass

    return count


def remember_external_connections(source):
    data = []
    source_id = source.Id.IntegerValue

    for c in get_connectors(source):
        try:
            origin = c.Origin

            for ref in c.AllRefs:
                try:
                    owner = ref.Owner

                    if owner is None:
                        continue

                    if owner.Id.IntegerValue == source_id:
                        continue

                    data.append({
                        "owner_id": owner.Id,
                        "origin": origin
                    })
                except:
                    pass
        except:
            pass

    return data


def restore_external_connections(connection_data, new_elements,
                                 stationary_ids=None,
                                 moved_endpoint=None, static_endpoint=None):
    u"""Вернуть подключения соседей к заново созданным участкам.

    Оборудование, оставленное на месте (stationary_ids), к перемещённому
    концу не подключаем: там теперь другая отметка. Его считаем отдельно.
    """
    restored = []
    skipped = []
    left_alone = 0

    for item in connection_data:
        try:
            owner_int_id = item["owner_id"].IntegerValue

            if stationary_ids and owner_int_id in stationary_ids \
                    and moved_endpoint is not None and static_endpoint is not None:
                origin = item["origin"]

                if origin.DistanceTo(moved_endpoint) < origin.DistanceTo(static_endpoint):
                    left_alone += 1
                    continue

            old_owner = doc.GetElement(item["owner_id"])

            if old_owner is None:
                skipped.append(u"Связанный элемент удален или не найден.")
                continue

            old_conn = nearest_connector(old_owner, item["origin"])

            if old_conn is None:
                skipped.append(u"Не найден коннектор связанного элемента.")
                continue

            best_conn = None
            best_dist = 999999999.0

            for new_el in new_elements:
                for nc in get_connectors(new_el):
                    try:
                        d = nc.Origin.DistanceTo(old_conn.Origin)

                        if d < best_dist:
                            best_dist = d
                            best_conn = nc
                    except:
                        pass

            if best_conn is None:
                skipped.append(u"Не найден новый коннектор для восстановления подключения.")
                continue

            try:
                old_conn.ConnectTo(best_conn)
                restored.append(item["owner_id"].IntegerValue)
            except:
                try:
                    doc.Create.NewElbowFitting(old_conn, best_conn)
                    restored.append(item["owner_id"].IntegerValue)
                except Exception as ex:
                    skipped.append(
                        u"Не удалось восстановить подключение: {}".format(
                            unicode(ex)
                        )
                    )

        except Exception as ex:
            skipped.append(
                u"Ошибка восстановления подключения: {}".format(
                    unicode(ex)
                )
            )

    return restored, skipped, left_alone


def analyze_element(mep, p1, p2, ctx, blocked_ids):
    u"""Геометрия одного участка: что и куда двигать. Ошибки — через Skip.

    Модель здесь не меняется: всё считается до транзакции, чтобы при
    нескольких участках проблемный можно было пропустить, а не откатывать всё.
    """
    warnings = []

    curve = get_curve(mep)

    if curve is None:
        raise Skip(u"У элемента не найдена ось LocationCurve.")

    if not isinstance(curve, Line):
        raise Skip(u"Элемент должен быть прямым участком.")

    start = curve.GetEndPoint(0)
    end = curve.GetEndPoint(1)

    axis_unit = normalize(xyz_sub(end, start))

    if axis_unit is None:
        raise Skip(u"Не удалось определить направление трассы.")

    t_split_raw = project_param_raw(p1, start, end)
    t_dir_raw = project_param_raw(p2, start, end)

    if t_split_raw is None or t_dir_raw is None:
        raise Skip(u"Не удалось спроецировать точки на ось элемента.")

    # Общая точка разрыва должна попадать на участок, а не за его конец
    if t_split_raw < 0.0 or t_split_raw > 1.0:
        raise Skip(u"Точка разрыва не попадает на этот участок.")

    t_split = min(max(t_split_raw, 0.0), 1.0)
    t_dir = min(max(t_dir_raw, 0.0), 1.0)

    split = point_on_line(start, end, t_split)

    total_len = start.DistanceTo(end)
    len_to_start = start.DistanceTo(split)
    len_to_end = split.DistanceTo(end)

    if total_len < MIN_SEGMENT_FT * 2:
        raise Skip(u"Участок слишком короткий для изменения отметки.")

    if len_to_start < MIN_SEGMENT_FT:
        raise Skip(
            u"Точка разрыва слишком близко к началу участка (минимум {} мм).".format(
                fmt(MIN_SEGMENT_MM)
            )
        )

    if len_to_end < MIN_SEGMENT_FT:
        raise Skip(
            u"Точка разрыва слишком близко к концу участка (минимум {} мм).".format(
                fmt(MIN_SEGMENT_MM)
            )
        )

    move_after_split = t_dir >= t_split
    source_id = mep.Id.IntegerValue

    angle_deg = ctx[u"angle_deg"]

    # --- сколько и куда двигать ---
    if ctx[u"by_level"]:
        level = ctx[u"level"]
        ref_kind = ctx[u"ref_kind"]
        elev_mm = ctx[u"elev_mm"]

        half_ft = half_size_ft(mep, split)

        if half_ft is None:
            if ref_kind == u"середина":
                half_ft = 0.0
            else:
                raise Skip(
                    u"Не удалось определить высоту сечения. "
                    u"Задайте отметку середины (оси) — она не зависит от габарита."
                )

        # Отметка задана для низа или верха — ось смещена на половину сечения
        if ref_kind == u"низ":
            axis_offset_ft = half_ft
        elif ref_kind == u"верх":
            axis_offset_ft = -half_ft
        else:
            axis_offset_ft = 0.0

        target_axis_z = level[u"elev_ft"] + elev_mm * MM_TO_FT + axis_offset_ft

        # Отметку меряем в точке разрыва: на наклонной трассе одного числа нет
        if abs(start.Z - end.Z) > 0.5 * MM_TO_FT:
            warnings.append(
                u"{}: участок наклонный, отметка выдержана в точке разрыва.".format(
                    element_label(mep)
                )
            )

        move_ft_signed = target_axis_z - split.Z

        if abs(move_ft_signed) < 0.5 * MM_TO_FT:
            raise Skip(
                u"Участок уже на этой отметке (сейчас {} мм от «{}»).".format(
                    fmt(round((split.Z - axis_offset_ft - level[u"elev_ft"]) / MM_TO_FT, 1)),
                    level[u"key"]
                )
            )

        direction = u"Опуск" if move_ft_signed < 0 else u"Подъем"
        move_ft = abs(move_ft_signed)
    else:
        direction = ctx[u"mode"]
        move_ft = ctx[u"value_mm"] * MM_TO_FT
        move_ft_signed = -move_ft if direction == u"Опуск" else move_ft

    move_mm = move_ft / MM_TO_FT
    move_vec = XYZ(0, 0, move_ft_signed)

    if angle_deg >= 89.9:
        horizontal_offset_ft = 0.0
    else:
        horizontal_offset_ft = move_ft / math.tan(math.radians(angle_deg))

    horizontal_offset_mm = horizontal_offset_ft / MM_TO_FT

    if move_after_split:
        available_len = len_to_end
    else:
        available_len = len_to_start

    if available_len < horizontal_offset_ft + MIN_SEGMENT_FT:
        raise Skip(
            u"Не хватает длины для угла {}°: нужно {} мм, есть примерно {} мм.".format(
                fmt(angle_deg),
                fmt(round(horizontal_offset_mm + MIN_SEGMENT_MM, 1)),
                fmt(round(available_len / MM_TO_FT, 1))
            )
        )

    start_moved = xyz_add(start, move_vec)
    end_moved = xyz_add(end, move_vec)

    if move_after_split:
        ramp_start = split
        ramp_end = xyz_add(
            xyz_add(split, xyz_mul(axis_unit, horizontal_offset_ft)),
            move_vec
        )
        moved_segment_start = ramp_end
        moved_segment_end = end_moved
    else:
        ramp_start = xyz_add(
            xyz_add(split, xyz_mul(axis_unit, -horizontal_offset_ft)),
            move_vec
        )
        ramp_end = split
        moved_segment_start = start_moved
        moved_segment_end = ramp_start

    # --- разворот прямоугольного сечения ---
    # Отвод «горизонталь → стояк» вращается вокруг горизонтальной оси,
    # перпендикулярной трассе. Значит у наклонного (вертикального) участка
    # та же ось профиля должна лежать на этой оси изгиба, что и у горизонтали.
    bend_axis = normalize(cross(axis_unit, XYZ.BasisZ))

    source_axes = profile_axes(mep, split)

    if source_axes is None or bend_axis is None:
        profile_use_x = None
    else:
        # Берём ту ось профиля исходной трассы, которая лежит горизонтально
        profile_use_x = abs(dot(source_axes[0], XYZ.BasisZ)) < abs(dot(source_axes[1], XYZ.BasisZ))

    external_connections = remember_external_connections(mep)

    if move_after_split:
        moved_endpoint = end
        static_endpoint = start
    else:
        moved_endpoint = start
        static_endpoint = end

    moving_endpoint_connector = nearest_connector(mep, moved_endpoint)

    first_connected_ids = []

    if moving_endpoint_connector:
        first_connected_ids = get_connected_owner_ids_from_connector(
            moving_endpoint_connector,
            source_id
        )

    move_chain_ids, stationary_ids = collect_connected_chain_from_start_ids(
        first_connected_ids,
        blocked_ids,
        MAX_CHAIN_DEPTH,
        ctx[u"move_equipment"]
    )

    return {
        u"mep": mep,
        u"label": element_label(mep),
        u"start": start,
        u"end": end,
        u"split": split,
        u"move_after_split": move_after_split,
        u"move_vec": move_vec,
        u"move_mm": move_mm,
        u"direction": direction,
        u"horizontal_offset_mm": horizontal_offset_mm,
        u"ramp_start": ramp_start,
        u"ramp_end": ramp_end,
        u"moved_segment_start": moved_segment_start,
        u"moved_segment_end": moved_segment_end,
        u"bend_axis": bend_axis,
        u"profile_use_x": profile_use_x,
        u"external_connections": external_connections,
        u"move_chain_ids": move_chain_ids,
        u"stationary_ids": stationary_ids,
        u"moved_endpoint": moved_endpoint,
        u"static_endpoint": static_endpoint,
        u"warnings": warnings,
    }


def apply_plan(plan):
    u"""Перестройка одного участка. Вызывать внутри открытой транзакции."""
    mep = plan[u"mep"]

    start = plan[u"start"]
    end = plan[u"end"]
    split = plan[u"split"]
    ramp_start = plan[u"ramp_start"]
    ramp_end = plan[u"ramp_end"]
    moved_segment_start = plan[u"moved_segment_start"]
    moved_segment_end = plan[u"moved_segment_end"]

    profile_use_x = plan[u"profile_use_x"]
    bend_axis = plan[u"bend_axis"]

    warnings = list(plan[u"warnings"])

    if plan[u"move_after_split"]:
        static_el, err1 = create_same_mep(mep, start, split)
        ramp_el, err2 = create_same_mep(mep, ramp_start, ramp_end)
        moved_el, err3 = create_same_mep(mep, moved_segment_start, moved_segment_end)

        if static_el is None:
            raise Exception(u"Не создан неподвижный участок: {}".format(err1))

        if ramp_el is None:
            raise Exception(u"Не создан наклонный участок: {}".format(err2))

        if moved_el is None:
            raise Exception(u"Не создан перемещенный участок: {}".format(err3))

        new_elements = [static_el, ramp_el, moved_el]

        align_profile(static_el, start, split, profile_use_x, bend_axis)
        align_profile(ramp_el, ramp_start, ramp_end, profile_use_x, bend_axis)
        align_profile(moved_el, moved_segment_start, moved_segment_end, profile_use_x, bend_axis)

        ok1, e1 = connect_with_elbow(static_el, split, ramp_el, ramp_start)
        ok2, e2 = connect_with_elbow(ramp_el, ramp_end, moved_el, moved_segment_start)

    else:
        moved_el, err1 = create_same_mep(mep, moved_segment_start, moved_segment_end)
        ramp_el, err2 = create_same_mep(mep, ramp_start, ramp_end)
        static_el, err3 = create_same_mep(mep, split, end)

        if moved_el is None:
            raise Exception(u"Не создан перемещенный участок: {}".format(err1))

        if ramp_el is None:
            raise Exception(u"Не создан наклонный участок: {}".format(err2))

        if static_el is None:
            raise Exception(u"Не создан неподвижный участок: {}".format(err3))

        new_elements = [moved_el, ramp_el, static_el]

        align_profile(moved_el, moved_segment_start, moved_segment_end, profile_use_x, bend_axis)
        align_profile(ramp_el, ramp_start, ramp_end, profile_use_x, bend_axis)
        align_profile(static_el, split, end, profile_use_x, bend_axis)

        ok1, e1 = connect_with_elbow(moved_el, moved_segment_end, ramp_el, ramp_start)
        ok2, e2 = connect_with_elbow(ramp_el, ramp_end, static_el, split)

    if not ok1:
        warnings.append(u"{}: не создан первый отвод: {}".format(plan[u"label"], e1))

    if not ok2:
        warnings.append(u"{}: не создан второй отвод: {}".format(plan[u"label"], e2))

    doc.Delete(mep.Id)

    # Оборудование остаётся на месте — стык с ним честно разрываем,
    # иначе Revit сочтёт разъехавшиеся элементы соединёнными
    disconnected = disconnect_from_stationary(
        plan[u"move_chain_ids"],
        plan[u"stationary_ids"]
    )

    moved_count = 0

    try:
        if plan[u"move_chain_ids"] and plan[u"move_chain_ids"].Count > 0:
            ElementTransformUtils.MoveElements(
                doc, plan[u"move_chain_ids"], plan[u"move_vec"]
            )
            moved_count = plan[u"move_chain_ids"].Count
    except Exception as ex:
        warnings.append(
            u"{}: не удалось переместить связанную цепочку: {}".format(
                plan[u"label"], unicode(ex)
            )
        )

    restored, restore_skipped, _left_alone = restore_external_connections(
        plan[u"external_connections"],
        new_elements,
        plan[u"stationary_ids"],
        plan[u"moved_endpoint"],
        plan[u"static_endpoint"]
    )

    for s in restore_skipped:
        warnings.append(u"{}: {}".format(plan[u"label"], s))

    return {
        u"new_count": len(new_elements),
        u"moved_count": moved_count,
        u"restored": len(restored),
        u"left_alone": len(plan[u"stationary_ids"]),
        u"disconnected": disconnected,
        u"warnings": warnings,
    }


try:
    settings = ask_move_settings()

    if settings is None:
        raise OperationCanceledException()

    mode = settings[u"mode"]
    angle_deg = settings[u"angle"]
    multi = settings[u"multi"]
    move_equipment = settings[u"move_equipment"]

    by_level = (mode == u"Отметка")

    ctx = {
        u"mode": mode,
        u"angle_deg": angle_deg,
        u"by_level": by_level,
        u"value_mm": settings[u"value_mm"],
        u"ref_kind": settings[u"ref_kind"],
        u"elev_mm": settings[u"elev_mm"],
        u"level": None,
        u"move_equipment": move_equipment,
    }

    if by_level:
        for item in settings[u"levels"]:
            if item[u"key"] == settings[u"level_key"]:
                ctx[u"level"] = item
                break

        if ctx[u"level"] is None:
            fail(u"Уровень «{}» в модели не найден.".format(settings[u"level_key"]))

        if settings[u"elev_mm"] is None:
            fail(u"Не задана отметка от уровня.")
    else:
        if ctx[u"value_mm"] is None or ctx[u"value_mm"] <= 0:
            fail(u"Значение должно быть больше 0 мм.")

    targets = pick_targets(multi)
    targets = [el for el in targets if el is not None]

    if not targets:
        fail(u"Не выбрано ни одного участка.")

    target_ids = set()

    for el in targets:
        try:
            target_ids.add(el.Id.IntegerValue)
        except:
            pass

    if len(targets) > 1:
        point_hint = u"Укажите точку разрыва на любом из выбранных участков"
        dir_hint = u"Укажите точку направления на том же участке"
    else:
        point_hint = u"Укажите точку разрыва на выбранной трубе/воздуховоде"
        dir_hint = u"Укажите точку направления на этой же трубе/воздуховоде"

    element_ids = [el.Id for el in targets]

    p1 = pick_point_on_element(element_ids, point_hint)
    p2 = pick_point_on_element(element_ids, dir_hint)

    if p1 is None or p2 is None:
        fail(u"Не удалось получить точку на элементе.")

    done = []
    skipped = []
    warnings = []

    tg = TransactionGroup(doc, u"PP: Опуск / Подъем трассы")
    tg.Start()

    for mep in targets:
        try:
            if doc.GetElement(mep.Id) is None:
                skipped.append((
                    element_label(mep),
                    u"участок исчез: он попал в перенос предыдущего."
                ))
                continue

            # Остальные выбранные участки цепочкой не двигаем: каждый едет сам
            plan = analyze_element(mep, p1, p2, ctx, target_ids)

        except Skip as ex:
            if len(targets) == 1:
                fail(unicode(ex))

            skipped.append((element_label(mep), unicode(ex)))
            continue

        t = Transaction(doc, u"PP: Опуск / Подъем участка")
        t.Start()

        try:
            stats = apply_plan(plan)
            t.Commit()
        except Exception as ex:
            try:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
            except:
                pass

            if len(targets) == 1:
                raise

            skipped.append((element_label(mep), unicode(ex)))
            continue

        stats[u"label"] = plan[u"label"]
        stats[u"direction"] = plan[u"direction"]
        stats[u"move_mm"] = plan[u"move_mm"]
        stats[u"horizontal_offset_mm"] = plan[u"horizontal_offset_mm"]

        done.append(stats)

        for w in stats[u"warnings"]:
            warnings.append(w)

    tg.Assimilate()

    if not done:
        lines = [u"Ни один участок не обработан."]

        for label, reason in skipped:
            lines.append(u"— {}: {}".format(label, reason))

        fail(u"\n".join(lines))

    # ---------- отчёт ----------
    total_left_alone = sum(s[u"left_alone"] for s in done)
    total_disconnected = sum(s[u"disconnected"] for s in done)
    total_moved = sum(s[u"moved_count"] for s in done)
    total_restored = sum(s[u"restored"] for s in done)
    total_new = sum(s[u"new_count"] for s in done)

    msg = u""

    if by_level:
        msg += u"Отметка {}: {} мм от «{}»\n".format(
            settings[u"ref_kind"],
            fmt(settings[u"elev_mm"]),
            ctx[u"level"][u"key"]
        )

    msg += u"Угол: {}°\n".format(fmt(angle_deg))

    if len(done) == 1:
        one = done[0]

        msg += u"Направление: {}\nВеличина: {} мм\nГоризонтальный отступ: {} мм\n".format(
            one[u"direction"].lower(),
            fmt(round(one[u"move_mm"], 1)),
            fmt(round(one[u"horizontal_offset_mm"], 1))
        )
    else:
        msg += u"\nУчастки:\n"

        for s in done:
            msg += u"— {}: {} на {} мм, отступ {} мм\n".format(
                s[u"label"],
                s[u"direction"].lower(),
                fmt(round(s[u"move_mm"], 1)),
                fmt(round(s[u"horizontal_offset_mm"], 1))
            )

        msg += u"\n"

    msg += u"Создано новых участков: {}\nПеремещено связанных элементов: {}\nВосстановлено подключений: {}".format(
        total_new,
        total_moved,
        total_restored
    )

    if not move_equipment and (total_left_alone or total_disconnected):
        msg += u"\nОставлено на месте (оборудование): {}".format(total_left_alone)

        if total_disconnected:
            msg += u"\nРазорвано стыков с оборудованием: {} — подводки доделайте вручную".format(
                total_disconnected
            )

    if skipped:
        msg += u"\n\nПропущено участков: {}\n".format(len(skipped))

        for label, reason in skipped[:10]:
            msg += u"— {}: {}\n".format(label, reason)

    if warnings:
        msg += u"\n\nПредупреждения:\n" + u"\n".join(warnings[:10])

    if len(done) == 1:
        one = done[0]
        done_verb = u"Опущено" if one[u"direction"] == u"Опуск" else u"Поднято"
        title = u"{} на {} мм".format(done_verb, fmt(round(one[u"move_mm"], 1)))
    else:
        title = u"Обработано участков: {}".format(len(done))

    pp_wpf.show_report(
        msg,
        title=title,
        subtitle=TITLE
    )

except OperationCanceledException:
    rollback()

except Stop as ex:
    rollback()

    pp_wpf.show_report(
        unicode(ex),
        title=u"Не выполнено",
        subtitle=TITLE,
        is_error=True
    )

except Exception as ex:
    rollback()

    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TITLE,
        is_error=True
    )
