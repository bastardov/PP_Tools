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


class MepCurveSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return isinstance(elem, Pipe) or isinstance(elem, Duct)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


class SameElementPointSelectionFilter(ISelectionFilter):
    def __init__(self, element_id):
        self.element_id = element_id

    def AllowElement(self, elem):
        try:
            return elem.Id == self.element_id
        except:
            return False

    def AllowReference(self, reference, point):
        try:
            return reference.ElementId == self.element_id
        except:
            return False


def pick_point_on_element(element_id, message):
    ref = uidoc.Selection.PickObject(
        ObjectType.PointOnElement,
        SameElementPointSelectionFilter(element_id),
        message
    )

    try:
        return ref.GlobalPoint
    except:
        return None


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
        saved[u"elev_mm"]
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


def collect_connected_chain_from_start_ids(start_ids, source_id, max_depth):
    visited = set()
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

        if eid.IntegerValue == source_id:
            continue

        el = doc.GetElement(eid)

        if el is None:
            continue

        if not is_allowed_to_move(el):
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

                    if owner.Id.IntegerValue == source_id:
                        continue

                    if owner.Id.IntegerValue not in visited:
                        queue.append((owner.Id, depth + 1))
            except:
                pass

    ids = List[ElementId]()

    for int_id in visited:
        ids.Add(ElementId(int_id))

    return ids


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


def restore_external_connections(connection_data, new_elements):
    restored = []
    skipped = []

    for item in connection_data:
        try:
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

    return restored, skipped


try:
    settings = ask_move_settings()

    if settings is None:
        raise OperationCanceledException()

    mode = settings[u"mode"]
    angle_deg = settings[u"angle"]

    by_level = (mode == u"Отметка")

    # Предупреждения, накопленные до старта транзакции
    pre_warnings = []

    if by_level:
        # Величину и направление посчитаем после выбора участка:
        # они зависят от текущей отметки и габарита сечения.
        direction = None
        move_mm = None
        move_ft = None
        move_vec = None
    else:
        direction = mode
        move_mm = settings[u"value_mm"]

        if move_mm is None or move_mm <= 0:
            fail(
                u"Значение должно быть больше 0 мм."
            )

        move_ft = move_mm * MM_TO_FT

        if direction == u"Опуск":
            move_vec = XYZ(0, 0, -move_ft)
        else:
            move_vec = XYZ(0, 0, move_ft)

    ref = uidoc.Selection.PickObject(
        ObjectType.Element,
        MepCurveSelectionFilter(),
        u"Выберите трубу или воздуховод"
    )

    mep = doc.GetElement(ref.ElementId)

    curve = get_curve(mep)

    if curve is None:
        fail(
            u"У элемента не найдена ось LocationCurve."
        )

    if not isinstance(curve, Line):
        fail(
            u"Выбранный элемент должен быть прямым участком."
        )

    start = curve.GetEndPoint(0)
    end = curve.GetEndPoint(1)

    axis_vec = xyz_sub(end, start)
    axis_unit = normalize(axis_vec)

    if axis_unit is None:
        fail(
            u"Не удалось определить направление трассы."
        )

    p1 = pick_point_on_element(
        mep.Id,
        u"Укажите точку разрыва на выбранной трубе/воздуховоде"
    )

    p2 = pick_point_on_element(
        mep.Id,
        u"Укажите точку направления на этой же трубе/воздуховоде"
    )

    if p1 is None or p2 is None:
        fail(
            u"Не удалось получить точку на элементе."
        )

    t_split = project_point_to_line_param(p1, start, end)
    t_dir = project_point_to_line_param(p2, start, end)

    if t_split is None or t_dir is None:
        fail(
            u"Не удалось спроецировать точки на ось элемента."
        )

    split = point_on_line(start, end, t_split)

    total_len = start.DistanceTo(end)
    len_to_start = start.DistanceTo(split)
    len_to_end = split.DistanceTo(end)

    if total_len < MIN_SEGMENT_FT * 2:
        fail(
            u"Элемент слишком короткий для изменения отметки."
        )

    if len_to_start < MIN_SEGMENT_FT:
        fail(
            u"Точка разрыва слишком близко к началу элемента.\n\nМинимум: {} мм.".format(
                fmt(MIN_SEGMENT_MM)
            )
        )

    if len_to_end < MIN_SEGMENT_FT:
        fail(
            u"Точка разрыва слишком близко к концу элемента.\n\nМинимум: {} мм.".format(
                fmt(MIN_SEGMENT_MM)
            )
        )

    move_after_split = t_dir >= t_split
    source_id = mep.Id.IntegerValue

    # --- режим «По отметке»: считаем смещение от текущей отметки ---
    if by_level:
        level_key = settings[u"level_key"]
        level = None

        for item in settings[u"levels"]:
            if item[u"key"] == level_key:
                level = item
                break

        if level is None:
            fail(
                u"Уровень «{}» в модели не найден.".format(level_key)
            )

        elev_mm = settings[u"elev_mm"]

        if elev_mm is None:
            fail(
                u"Не задана отметка от уровня."
            )

        ref_kind = settings[u"ref_kind"]

        half_ft = half_size_ft(mep, split)

        if half_ft is None:
            if ref_kind == u"середина":
                half_ft = 0.0
            else:
                fail(
                    u"Не удалось определить высоту сечения участка.\n\n"
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
            pre_warnings.append(
                u"Участок наклонный: отметка выдержана в точке разрыва, "
                u"уклон перемещённой части сохранён."
            )

        move_ft_signed = target_axis_z - split.Z

        if abs(move_ft_signed) < 0.5 * MM_TO_FT:
            fail(
                u"Участок уже на этой отметке.\n\n"
                u"Текущая отметка {}: {} мм от «{}».".format(
                    ref_kind,
                    fmt(round((split.Z - axis_offset_ft - level[u"elev_ft"]) / MM_TO_FT, 1)),
                    level[u"key"]
                )
            )

        direction = u"Опуск" if move_ft_signed < 0 else u"Подъем"
        move_ft = abs(move_ft_signed)
        move_mm = move_ft / MM_TO_FT
        move_vec = XYZ(0, 0, move_ft_signed)

    if angle_deg >= 89.9:
        horizontal_offset_ft = 0.0
    else:
        horizontal_offset_ft = abs(move_ft) / math.tan(math.radians(angle_deg))

    horizontal_offset_mm = horizontal_offset_ft / MM_TO_FT

    if move_after_split:
        available_len = len_to_end
    else:
        available_len = len_to_start

    if available_len < horizontal_offset_ft + MIN_SEGMENT_FT:
        fail(
            u"Недостаточно длины выбранной стороны для угла {}°.\n\nНужно минимум: {} мм\nДоступно примерно: {} мм".format(
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
        moving_endpoint_connector = nearest_connector(mep, end)
    else:
        moving_endpoint_connector = nearest_connector(mep, start)

    first_connected_ids = []

    if moving_endpoint_connector:
        first_connected_ids = get_connected_owner_ids_from_connector(
            moving_endpoint_connector,
            source_id
        )

    move_chain_ids = collect_connected_chain_from_start_ids(
        first_connected_ids,
        source_id,
        MAX_CHAIN_DEPTH
    )

    warnings = list(pre_warnings)

    t = Transaction(doc, u"PP: Опуск / Подъем трассы")
    t.Start()

    if move_after_split:
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
        warnings.append(u"Не создан первый отвод: {}".format(e1))

    if not ok2:
        warnings.append(u"Не создан второй отвод: {}".format(e2))

    doc.Delete(mep.Id)

    moved_count = 0

    try:
        if move_chain_ids and move_chain_ids.Count > 0:
            ElementTransformUtils.MoveElements(doc, move_chain_ids, move_vec)
            moved_count = move_chain_ids.Count
    except Exception as ex:
        warnings.append(
            u"Не удалось переместить связанную цепочку: {}".format(
                unicode(ex)
            )
        )

    restored, restore_skipped = restore_external_connections(
        external_connections,
        new_elements
    )

    for s in restore_skipped:
        warnings.append(s)

    t.Commit()

    msg = u""

    if by_level:
        msg += u"Отметка {}: {} мм от «{}»\nНаправление: {}\n".format(
            settings[u"ref_kind"],
            fmt(settings[u"elev_mm"]),
            level[u"key"],
            direction.lower()
        )

    msg += u"Величина: {} мм\nУгол: {}°\nГоризонтальный отступ: {} мм\nСоздано новых участков: {}\nПеремещено связанных элементов: {}\nВосстановлено подключений: {}".format(
        fmt(round(move_mm, 1)),
        fmt(angle_deg),
        fmt(round(horizontal_offset_mm, 1)),
        len(new_elements),
        moved_count,
        len(restored)
    )

    if warnings:
        msg += u"\n\nПредупреждения:\n" + u"\n".join(warnings[:10])

    done_verb = u"Опущено" if direction == u"Опуск" else u"Поднято"

    pp_wpf.show_report(
        msg,
        title=u"{} на {} мм".format(done_verb, fmt(round(move_mm, 1))),
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
