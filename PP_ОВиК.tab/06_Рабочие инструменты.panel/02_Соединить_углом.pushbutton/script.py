# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

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

import pp_wpf
import pp_params_dialog


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TITLE = u"Соединить под углом"

MM_TO_FT = 1.0 / 304.8
MIN_SEGMENT_MM = 100.0
MIN_SEGMENT_FT = MIN_SEGMENT_MM * MM_TO_FT
# допустимое боковое смещение осей в плане (мм)
MAX_LATERAL_MM = 50.0
MAX_LATERAL_FT = MAX_LATERAL_MM * MM_TO_FT
# минимальная разница отметок между элементами (мм)
MIN_DZ_MM = 5.0
MIN_DZ_FT = MIN_DZ_MM * MM_TO_FT
# допуск на "горизонтальность" самого элемента (мм)
FLAT_TOL_FT = 1.0 * MM_TO_FT

PRESETS = [30.0, 45.0, 60.0, 90.0]


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта, транзакция откатывается."""
    pass


def fail(message):
    raise Stop(message)


def rollback():
    u"""Откат незакрытой транзакции. Вызывается из каждой ветки except."""
    try:
        if "t" in globals():
            _t = globals()["t"]
            if hasattr(_t, "HasStarted") and _t.HasStarted() and not _t.HasEnded():
                _t.RollBack()
    except Exception:
        pass


class MepCurveSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return isinstance(elem, Pipe) or isinstance(elem, Duct)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


# ----------------------------------------------------------------------
# Сохранение / загрузка последнего угла
# ----------------------------------------------------------------------

def load_last_angle():
    try:
        cfg = script.get_config()
        val = cfg.get_option("last_angle", 45.0)
        return float(val)
    except:
        return 45.0


def save_last_angle(angle):
    try:
        cfg = script.get_config()
        cfg.last_angle = float(angle)
        script.save_config()
    except:
        pass


# ----------------------------------------------------------------------
# Диалог выбора угла
# ----------------------------------------------------------------------

# Схема: два горизонтальных участка на разных отметках и наклонная вставка
DIAGRAM = u"""
<Canvas xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Width="300" Height="96">
  <Path Data="M 14,72 L 120,72" Stroke="#0E6E8C" StrokeThickness="3" StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
  <Path Data="M 120,72 L 186,26" Stroke="#0E6E8C" StrokeThickness="3" StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
  <Path Data="M 186,26 L 288,26" Stroke="#0E6E8C" StrokeThickness="3" StrokeStartLineCap="Round" StrokeEndLineCap="Round"/>
  <Path Data="M 120,72 L 186,72" Stroke="#6B7C85" StrokeThickness="1.2" StrokeDashArray="4 3"/>
  <Path Data="M 150,72 A 30,30 0 0 0 138,54" Stroke="#B3541E" StrokeThickness="1.6"/>
  <TextBlock Canvas.Left="154" Canvas.Top="52" FontSize="10.5" Foreground="#B3541E" Text="угол"/>
  <TextBlock Canvas.Left="14" Canvas.Top="76" FontSize="10.5" Foreground="#6B7C85" Text="нижний элемент"/>
  <TextBlock Canvas.Left="196" Canvas.Top="6" FontSize="10.5" Foreground="#6B7C85" Text="верхний элемент"/>
</Canvas>
"""


def ask_angle(default_angle):
    u"""Общий диалог параметров. Возврат прежний: угол в градусах или None."""
    values = pp_params_dialog.ask({
        u"title": TITLE,
        u"subtitle": u"Два горизонтальных участка на разных отметках соединяются "
                     u"наклонной вставкой. Угол отсчитывается от горизонтали.",
        u"diagram": DIAGRAM,
        u"fields": [
            {
                u"key": u"angle",
                u"label": u"УГОЛ ОТ ГОРИЗОНТАЛИ",
                u"short": u"угол",
                u"hint": u"90° — вертикальный стояк, 45° — наклон, 0° — недопустимо.",
                u"unit": u"°",
                u"value": default_angle,
                u"min": 1.0,
                u"max": 90.0,
                u"presets": PRESETS,
            },
        ],
        u"run_label": u"Выбрать элементы",
    })

    if values is None:
        return None

    return values[u"angle"]


# ----------------------------------------------------------------------
# Векторные помощники
# ----------------------------------------------------------------------

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


def horizontal_dir(v):
    return normalize(XYZ(v.X, v.Y, 0.0))


def get_curve(el):
    try:
        loc = el.Location
        if isinstance(loc, LocationCurve):
            return loc.Curve
    except:
        pass
    return None


def project_on_line_unclamped(point, start, end):
    v = xyz_sub(end, start)
    denom = dot(v, v)
    if abs(denom) < 0.0000001:
        return None
    t = dot(xyz_sub(point, start), v) / denom
    return xyz_add(start, xyz_mul(v, t))


# ----------------------------------------------------------------------
# Параметры элемента
# ----------------------------------------------------------------------

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
            new_el = Pipe.Create(doc, system_type_id, type_id, level_id, p1, p2)
            copy_size_params(source, new_el)
            return new_el, None
        if isinstance(source, Duct):
            new_el = Duct.Create(doc, system_type_id, type_id, level_id, p1, p2)
            copy_size_params(source, new_el)
            return new_el, None
    except Exception as ex:
        return None, unicode(ex)

    return None, u"Элемент не является трубой или воздуховодом."


# ----------------------------------------------------------------------
# Коннекторы / отводы / внешние связи
# ----------------------------------------------------------------------

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

    None — круглое сечение или коннектор не найден: ориентация профиля
    в этих случаях роли не играет.
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
    return normalize(xyz_sub(v, xyz_mul(axis_unit, dot(v, axis_unit))))


def align_profile(el, p_from, p_to, use_basis_x, target_dir):
    u"""Доворачивает элемент вокруг собственной оси, чтобы ось профиля легла на target_dir.

    Revit сам выбирает разворот прямоугольного сечения у нового участка и для
    наклона/вертикали берёт свой вариант (ширина вдоль глобального X) — из-за
    этого ширина и высота воздуховода на скате меняются местами. Здесь разворот
    приводится к сечению исходной горизонтали.
    """
    if target_dir is None or use_basis_x is None:
        return

    axis_unit_el = normalize(xyz_sub(p_to, p_from))

    if axis_unit_el is None:
        return

    axes = profile_axes(el, p_from)

    if axes is None:
        return

    current = perpendicular_part(axes[0] if use_basis_x else axes[1], axis_unit_el)
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
        rotation_axis = Line.CreateBound(p_from, xyz_add(p_from, axis_unit_el))
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
                    data.append({"owner_id": owner.Id, "origin": origin})
                except:
                    pass
        except:
            pass
    return data


def restore_external_connections(connection_data, new_elements, deleted_ids):
    restored = []
    skipped = []
    for item in connection_data:
        try:
            if item["owner_id"].IntegerValue in deleted_ids:
                continue
            old_owner = doc.GetElement(item["owner_id"])
            if old_owner is None:
                continue
            old_conn = nearest_connector(old_owner, item["origin"])
            if old_conn is None:
                continue
            if old_conn.Origin.DistanceTo(item["origin"]) > MIN_SEGMENT_FT:
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

            if best_conn is None or best_dist > MIN_SEGMENT_FT:
                continue

            try:
                old_conn.ConnectTo(best_conn)
                restored.append(item["owner_id"].IntegerValue)
            except:
                try:
                    doc.Create.NewElbowFitting(old_conn, best_conn)
                    restored.append(item["owner_id"].IntegerValue)
                except Exception as ex:
                    skipped.append(u"Не удалось восстановить подключение: {}".format(unicode(ex)))
        except Exception as ex:
            skipped.append(u"Ошибка восстановления подключения: {}".format(unicode(ex)))
    return restored, skipped


# ----------------------------------------------------------------------
# Основной сценарий
# ----------------------------------------------------------------------

try:
    angle_deg = ask_angle(load_last_angle())

    if angle_deg is None:
        raise OperationCanceledException()

    if angle_deg <= 0.1 or angle_deg > 90.0:
        fail(
            u"Угол должен быть в диапазоне 1...90°.\n\n"
            u"Угол отсчитывается от горизонтали:\n"
            u"90° — вертикальный стояк, 45° — наклон, 0° — горизонталь (недопустимо)."
        )

    save_last_angle(angle_deg)

    # --- выбор двух элементов ---
    ref1 = uidoc.Selection.PickObject(
        ObjectType.Element, MepCurveSelectionFilter(),
        u"Выберите первый элемент (труба/воздуховод)"
    )
    ref2 = uidoc.Selection.PickObject(
        ObjectType.Element, MepCurveSelectionFilter(),
        u"Выберите второй элемент на другой высоте"
    )

    el1 = doc.GetElement(ref1.ElementId)
    el2 = doc.GetElement(ref2.ElementId)

    if el1.Id.IntegerValue == el2.Id.IntegerValue:
        fail(
            u"Выбран один и тот же элемент."
        )

    c1 = get_curve(el1)
    c2 = get_curve(el2)

    if c1 is None or c2 is None:
        fail(
            u"У элемента не найдена ось LocationCurve."
        )

    if not isinstance(c1, Line) or not isinstance(c2, Line):
        fail(
            u"Оба элемента должны быть прямыми участками."
        )

    for c in (c1, c2):
        if abs(c.GetEndPoint(0).Z - c.GetEndPoint(1).Z) > FLAT_TOL_FT:
            fail(
                u"Оба элемента должны быть горизонтальными."
            )

    z1 = (c1.GetEndPoint(0).Z + c1.GetEndPoint(1).Z) / 2.0
    z2 = (c2.GetEndPoint(0).Z + c2.GetEndPoint(1).Z) / 2.0

    dz = abs(z2 - z1)
    if dz < MIN_DZ_FT:
        fail(
            u"Элементы почти на одной отметке.\n\n"
            u"Инструмент соединяет элементы на разных высотах."
        )

    # нижний / верхний
    if z1 <= z2:
        low_el, low_c = el1, c1
        high_el, high_c = el2, c2
    else:
        low_el, low_c = el2, c2
        high_el, high_c = el1, c1

    z_low = (low_c.GetEndPoint(0).Z + low_c.GetEndPoint(1).Z) / 2.0
    z_high = (high_c.GetEndPoint(0).Z + high_c.GetEndPoint(1).Z) / 2.0
    dz = z_high - z_low

    # горизонтальное направление нижнего элемента
    base_u = horizontal_dir(xyz_sub(low_c.GetEndPoint(1), low_c.GetEndPoint(0)))
    if base_u is None:
        fail(
            u"Не удалось определить направление нижнего элемента."
        )

    u_high = horizontal_dir(xyz_sub(high_c.GetEndPoint(1), high_c.GetEndPoint(0)))
    if u_high is None:
        fail(
            u"Не удалось определить направление верхнего элемента."
        )

    # параллельность в плане (допуск ~10°)
    if abs(dot(base_u, u_high)) < 0.985:
        fail(
            u"Элементы не параллельны в плане.\n\n"
            u"Инструмент соединяет элементы, идущие в одном направлении."
        )

    l_pts = [low_c.GetEndPoint(0), low_c.GetEndPoint(1)]
    h_pts = [high_c.GetEndPoint(0), high_c.GetEndPoint(1)]

    low_center = xyz_mul(xyz_add(l_pts[0], l_pts[1]), 0.5)
    high_center = xyz_mul(xyz_add(h_pts[0], h_pts[1]), 0.5)

    # ось u ориентируем от нижнего к верхнему
    u = base_u if dot(xyz_sub(high_center, low_center), base_u) >= 0 else xyz_mul(base_u, -1.0)

    O = l_pts[0]

    def s_of(p):
        return dot(xyz_sub(p, O), u)

    # торцы нижнего элемента
    ls = [s_of(l_pts[0]), s_of(l_pts[1])]
    if ls[0] <= ls[1]:
        low_far_pt, low_far_s = l_pts[0], ls[0]
        low_facing_s = ls[1]
    else:
        low_far_pt, low_far_s = l_pts[1], ls[1]
        low_facing_s = ls[0]

    # торец верхнего элемента, обращённый к нижнему (минимальный s)
    hs = [s_of(h_pts[0]), s_of(h_pts[1])]
    if hs[0] <= hs[1]:
        upper_facing_pt, upper_facing_s = h_pts[0], hs[0]
    else:
        upper_facing_pt, upper_facing_s = h_pts[1], hs[1]

    # боковое смещение осей в плане
    proj = project_on_line_unclamped(high_center, O, xyz_add(O, u))
    lateral = XYZ(high_center.X - proj.X, high_center.Y - proj.Y, 0.0).GetLength()
    lateral_warning = None
    if lateral > MAX_LATERAL_FT:
        lateral_warning = u"Оси смещены в плане на {} мм — элементы не строго на одной линии.".format(
            round(lateral / MM_TO_FT, 1)
        )

    # горизонтальный отступ наклонного участка (угол от горизонтали)
    # 90° -> вертикальный стояк (отступ 0), 45° -> отступ = dz
    if angle_deg >= 89.9:
        offset = 0.0
    else:
        offset = dz / math.tan(math.radians(angle_deg))

    # наклонный участок приходит в торец верхнего; основание ската — на отметке нижнего
    p_high = upper_facing_pt
    p1_s = upper_facing_s - offset
    low_new_len = p1_s - low_far_s

    if low_new_len < MIN_SEGMENT_FT:
        gap_plan = upper_facing_s - low_facing_s
        fail(
            u"Угол слишком пологий для взаимного положения элементов.\n\n"
            u"Разница отметок: {} мм\n"
            u"Нужен горизонтальный отступ ската: {} мм\n"
            u"Расстояние между торцами в плане: {} мм\n\n"
            u"Увеличьте угол (ближе к 90°) или раздвиньте элементы.".format(
            round(dz / MM_TO_FT, 1),
            round(offset / MM_TO_FT, 1),
            round(gap_plan / MM_TO_FT, 1)
            )
        )

    # основание ската на оси нижнего, на его отметке
    p1 = XYZ(O.X + u.X * p1_s, O.Y + u.Y * p1_s, z_low)
    # снап торца верхнего на его ось
    p_high_snapped = project_on_line_unclamped(p_high, high_c.GetEndPoint(0), high_c.GetEndPoint(1))
    if p_high_snapped is not None:
        p_high = p_high_snapped

    # --- разворот прямоугольного сечения ---
    # Отвод «горизонталь → скат» вращается вокруг горизонтальной оси,
    # перпендикулярной трассе. Значит у наклонного участка та же ось профиля
    # должна лежать на этой оси изгиба, что и у горизонталей.
    bend_axis = normalize(cross(u, XYZ.BasisZ))

    source_axes = profile_axes(low_el, low_center)

    if source_axes is None or bend_axis is None:
        profile_use_x = None
    else:
        # Берём ту ось профиля нижнего элемента, которая лежит горизонтально
        profile_use_x = abs(dot(source_axes[0], XYZ.BasisZ)) < abs(dot(source_axes[1], XYZ.BasisZ))

    # запоминаем внешние связи только нижнего (его пересоздаём)
    external_connections = remember_external_connections(low_el)
    deleted_ids = set([low_el.Id.IntegerValue])

    was_extend = p1_s > low_facing_s

    warnings = []
    if lateral_warning:
        warnings.append(lateral_warning)

    t = Transaction(doc, u"PP: Соединить под углом")
    t.Start()

    # нижний воздуховод, достроенный/подрезанный до основания ската
    new_low, err1 = create_same_mep(low_el, low_far_pt, p1)
    # наклонный соединительный участок
    ramp, err2 = create_same_mep(low_el, p1, p_high)

    if new_low is None:
        raise Exception(u"Не создан нижний участок: {}".format(err1))
    if ramp is None:
        raise Exception(u"Не создан наклонный участок: {}".format(err2))

    new_elements = [new_low, ramp]

    align_profile(new_low, low_far_pt, p1, profile_use_x, bend_axis)
    align_profile(ramp, p1, p_high, profile_use_x, bend_axis)

    ok1, e1 = connect_with_elbow(new_low, p1, ramp, p1)
    ok2, e2 = connect_with_elbow(ramp, p_high, high_el, p_high)

    if not ok1:
        warnings.append(u"Не создан нижний отвод: {}".format(e1))
    if not ok2:
        warnings.append(u"Не создан отвод к верхнему элементу: {}".format(e2))

    doc.Delete(low_el.Id)

    restored, restore_skipped = restore_external_connections(
        external_connections, new_elements, deleted_ids
    )
    for s in restore_skipped:
        warnings.append(s)

    t.Commit()

    msg = u"Готово.\n\n" \
          u"Угол наклона (от горизонтали): {}°\n" \
          u"Разница отметок: {} мм\n" \
          u"Горизонтальный отступ ската: {} мм\n" \
          u"Нижний элемент: {} до {} мм\n" \
          u"Восстановлено подключений: {}".format(
              round(angle_deg, 1),
              round(dz / MM_TO_FT, 1),
              round(offset / MM_TO_FT, 1),
              u"удлинён" if was_extend else u"подрезан",
              round(low_new_len / MM_TO_FT, 1),
              len(restored)
          )

    if warnings:
        msg += u"\n\nПредупреждения:\n" + u"\n".join(warnings[:10])

    pp_wpf.show_report(
        msg,
        title=u"Готово",
        subtitle=TITLE
    )

except OperationCanceledException:
    rollback()

except SystemExit:
    pass

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
