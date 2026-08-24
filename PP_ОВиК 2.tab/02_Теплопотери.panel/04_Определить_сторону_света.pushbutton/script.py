# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
# Опр. стор. света — версия 1.
#
# Определяет ТОЛЬКО сторону света для выбранных стен, витражей, окон,
# дверей и перекрытий и записывает её в параметры:
#   PP_Ориентация по стороне света  ("С", "СВ", "В" … )
#   PP_Добавка на сторону света     (коэффициент)
#
# Логика поиска пространства и определения нормали полностью взята из
# инструмента "Перенос данных из пространств" (v8). Отличие: НИЧЕГО,
# кроме стороны света, не пишется (температура, номер/имя, площадь не
# трогаются).
#
# Перекрытие горизонтально — у него стороны света нет: пишется пустая
# ориентация и коэффициент 1.0.

import clr
import math
import os
import codecs

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Mechanical import *
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import forms, script


doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


# ─── НАСТРОЙКИ ───────────────────────────────────────────────

MARGIN_FT    = 0.15 / 0.3048
FLOOR_OFFSET = 0.10 / 0.3048

PARAM_ORIENT = "PP_Ориентация по стороне света"
PARAM_COEFF  = "PP_Добавка на сторону света"

ORIENT_COEFF = {
    "С":  1.1,  "СВ": 1.1,  "В":  1.1,  "ЮВ": 1.05,
    "Ю":  1.0,  "ЮЗ": 1.0,  "З":  1.05, "СЗ": 1.1
}

WALL_CAT   = int(BuiltInCategory.OST_Walls)
WINDOW_CAT = int(BuiltInCategory.OST_Windows)
DOOR_CAT   = int(BuiltInCategory.OST_Doors)
FLOOR_CAT  = int(BuiltInCategory.OST_Floors)


# ─── ФИЛЬТР ВЫБОРА ───────────────────────────────────────────

class BuildingFilter(ISelectionFilter):
    def AllowElement(self, el):
        try:
            if el.Category is None:
                return False
            return el.Category.Id.IntegerValue in [
                WALL_CAT, WINDOW_CAT, DOOR_CAT, FLOOR_CAT
            ]
        except:
            return False

    def AllowReference(self, ref, pt):
        return False


# ─── ОБЩИЕ УТИЛИТЫ ───────────────────────────────────────────

def get_category_id(element):
    try:
        return element.Category.Id.IntegerValue
    except:
        return None


def el_label(element):
    cid = get_category_id(element)
    if cid == WALL_CAT:   return u"Стена"
    if cid == WINDOW_CAT: return u"Окно"
    if cid == DOOR_CAT:   return u"Дверь"
    if cid == FLOOR_CAT:  return u"Перекрытие"
    return u"?"


def is_curtain_wall(wall):
    try:
        return wall.WallType.Kind == WallKind.Curtain
    except:
        return False


def apply_orient_and_coeff(element, compass):
    """Пишет ТОЛЬКО сторону света и коэффициент. Возвращает список сообщений."""
    msgs = []

    # Запись сокращённой стороны света ("С", "СВ", "З", "Ю" …) в текстовый
    # параметр PP_Ориентация по стороне света.
    # Важно: Parameter.Set(str) у нетекстового параметра НЕ бросает исключение,
    # а молча возвращает False — поэтому проверяем тип хранения и результат Set().
    dst_or = element.LookupParameter(PARAM_ORIENT)
    if not compass:
        msgs.append(u"Ор=не_определена")
    elif dst_or is None:
        msgs.append(u"WARN:Ор_нет_параметра")
    elif dst_or.IsReadOnly:
        msgs.append(u"WARN:Ор_read-only")
    elif dst_or.StorageType != StorageType.String:
        msgs.append(u"WARN:Ор_не_текстовый")
    else:
        try:
            if dst_or.Set(compass):
                msgs.append(u"Ор={}".format(compass))
            else:
                msgs.append(u"WARN:Ор_не_записана")
        except:
            msgs.append(u"WARN:Ориентация")

    dst_co = element.LookupParameter(PARAM_COEFF)
    if compass and dst_co and not dst_co.IsReadOnly:
        try:
            k = ORIENT_COEFF.get(compass, 1.0)
            dst_co.Set(k)
            msgs.append(u"K={}".format(k))
        except:
            msgs.append(u"WARN:Коэфф")
    else:
        msgs.append(u"WARN:Коэфф")

    return msgs


# ─── ФАЗА И ПОИСК ПРОСТРАНСТВА ПО ТОЧКЕ ─────────────────────

def get_element_phase(element):
    """Фаза создания элемента; если нет — последняя фаза проекта."""
    try:
        p = element.get_Parameter(BuiltInParameter.PHASE_CREATED)
        if p and p.HasValue:
            ph = doc.GetElement(p.AsElementId())
            if ph is not None:
                return ph
    except:
        pass
    try:
        phases = doc.Phases
        return phases.get_Item(phases.Size - 1)
    except:
        return None


def get_space_at_point(pt, phase):
    """GetSpaceAtPoint с фазой; без фазы — как раньше."""
    try:
        if phase is not None:
            sp = doc.GetSpaceAtPoint(pt, phase)
            if sp is not None:
                return sp
        return doc.GetSpaceAtPoint(pt)
    except:
        try:
            return doc.GetSpaceAtPoint(pt)
        except:
            return None


# ─── ГЕОМЕТРИЯ ───────────────────────────────────────────────

def get_wall_normal(wall):
    try:
        loc = wall.Location
        if not isinstance(loc, LocationCurve):
            return None
        d = loc.Curve.Direction
        return XYZ(-d.Y, d.X, 0).Normalize()
    except:
        return None


def vector_to_compass(vec):
    angle   = math.degrees(math.atan2(vec.X, vec.Y)) % 360
    sectors = [u"С", u"СВ", u"В", u"ЮВ", u"Ю", u"ЮЗ", u"З", u"СЗ"]
    return sectors[int((angle + 22.5) / 45) % 8]


def get_wall_probe_offsets(wall):
    """
    Расстояния зондов от линии стены (v8).
    Линия привязки может быть осью ИЛИ гранью — зондируем на двух
    расстояниях, чтобы хотя бы одно вышло за грань в помещение:
      W/2+150 (линия = ось), W+150 (линия = грань).
    """
    try:
        w = wall.Width
    except:
        w = 0.5 / 0.3048
    return [w / 2.0 + MARGIN_FT, w + MARGIN_FT]


def get_wall_probe_points(wall):
    normal = get_wall_normal(wall)
    if normal is None:
        return []

    bb = wall.get_BoundingBox(None)
    if bb is None:
        return []

    try:
        curve = wall.Location.Curve
    except:
        return []

    offsets = get_wall_probe_offsets(wall)
    h       = bb.Max.Z - bb.Min.Z
    pts     = []

    for t in [0.25, 0.50, 0.75]:
        pt = curve.Evaluate(t, True)
        for z_r in [1.0/3.0, 2.0/3.0]:
            z = bb.Min.Z + h * z_r
            for offset in offsets:
                for sign in [1, -1]:
                    pts.append((
                        XYZ(
                            pt.X + normal.X * offset * sign,
                            pt.Y + normal.Y * offset * sign,
                            z
                        ),
                        sign
                    ))

    return pts


def vote_for_space(pts, phase=None):
    """
    Общая функция голосования: принимает список (XYZ, sign),
    возвращает (best_space, votes, total, pos_votes, neg_votes).
    """
    votes      = {}
    space_refs = {}
    side_votes = {}

    for pt, sign in pts:
        try:
            sp = get_space_at_point(pt, phase)
            if sp is not None and sp.Area > 0:
                key = sp.Id.IntegerValue
                votes[key]      = votes.get(key, 0) + 1
                space_refs[key] = sp
                if key not in side_votes:
                    side_votes[key] = {1: 0, -1: 0}
                side_votes[key][sign] += 1
        except:
            pass

    if not votes:
        return None, 0, len(pts), 0, 0

    best_key   = max(votes, key=lambda k: votes[k])
    pos        = side_votes[best_key].get(1,  0)
    neg        = side_votes[best_key].get(-1, 0)
    return space_refs[best_key], votes[best_key], len(pts), pos, neg


_ALL_SPACES_CACHE = [None]

def get_all_spaces():
    """Все размещённые пространства документа (кэш на один запуск)."""
    if _ALL_SPACES_CACHE[0] is None:
        spaces = []
        for sp in FilteredElementCollector(doc) \
                .OfCategory(BuiltInCategory.OST_MEPSpaces) \
                .WhereElementIsNotElementType() \
                .ToElements():
            try:
                if sp.Area > 0:
                    spaces.append(sp)
            except:
                pass
        _ALL_SPACES_CACHE[0] = spaces
    return _ALL_SPACES_CACHE[0]


def spaces_in_z_range(z_min, z_max, z_tol=1.5):
    """Пространства, пересекающиеся с диапазоном [z_min, z_max] по Z."""
    result = []
    for sp in get_all_spaces():
        try:
            bb = sp.get_BoundingBox(None)
            if bb is not None:
                if bb.Max.Z < z_min - z_tol or bb.Min.Z > z_max + z_tol:
                    continue
            else:
                try:
                    lvl_z = sp.Level.Elevation
                    if not ((z_min - z_tol) <= lvl_z <= (z_max + z_tol)):
                        continue
                except:
                    pass
            result.append(sp)
        except:
            pass
    return result


def vote_for_space_ipis(pts, z_min, z_max):
    """
    Второй метод поиска (v7): голосование зондов через Space.IsPointInSpace()
    по пространствам, пересекающимся с элементом по Z.
    Работает там, где doc.GetSpaceAtPoint() молчит.
    Возвращает (best_space, votes, total, pos_votes, neg_votes).
    """
    candidates = spaces_in_z_range(z_min, z_max)
    if not candidates:
        return None, 0, len(pts), 0, 0

    votes      = {}
    space_refs = {}
    side_votes = {}

    for pt, sign in pts:
        for sp in candidates:
            try:
                if sp.IsPointInSpace(pt):
                    key = sp.Id.IntegerValue
                    votes[key]      = votes.get(key, 0) + 1
                    space_refs[key] = sp
                    if key not in side_votes:
                        side_votes[key] = {1: 0, -1: 0}
                    side_votes[key][sign] += 1
                    break
            except:
                pass

    if not votes:
        return None, 0, len(pts), 0, 0

    best_key = max(votes, key=lambda k: votes[k])
    pos      = side_votes[best_key].get(1,  0)
    neg      = side_votes[best_key].get(-1, 0)
    return space_refs[best_key], votes[best_key], len(pts), pos, neg


def nearest_space_fallback(ref_point, z_min=None, z_max=None):
    """
    Ближайшее пространство по 2D-расстоянию от ref_point,
    НО только среди пространств, пересекающихся с элементом по Z.
    Если ни одно пространство не пересекается по Z — возвращает None.
    """
    if z_min is not None and z_max is not None:
        candidates = spaces_in_z_range(z_min, z_max)
    else:
        candidates = get_all_spaces()

    best = None
    best_dist = float("inf")
    for sp in candidates:
        try:
            if sp.Location is None:
                continue

            sp_pt = sp.Location.Point
            dx = ref_point.X - sp_pt.X
            dy = ref_point.Y - sp_pt.Y
            d = math.sqrt(dx*dx + dy*dy)
            if d < best_dist:
                best_dist = d
                best = sp
        except:
            pass
    return best


# ─── ОПРЕДЕЛЕНИЕ СТОРОНЫ СВЕТА: ОБЫЧНАЯ СТЕНА ───────────────

def find_compass(wall):
    """
    Для обычных (несущих) стен.
    Возвращает (compass, method), method: "pts" | "ipis" | "fallback" | None.
    """
    normal = get_wall_normal(wall)
    if normal is None:
        return None, None

    phase                          = get_element_phase(wall)
    pts                            = get_wall_probe_points(wall)
    space, votes, total, pos, neg  = vote_for_space(pts, phase)

    if space is not None:
        facing = XYZ(-normal.X, -normal.Y, 0) if pos >= neg \
                 else XYZ(normal.X, normal.Y, 0)
        return vector_to_compass(facing), "pts"

    bb    = wall.get_BoundingBox(None)
    z_min = bb.Min.Z if bb else None
    z_max = bb.Max.Z if bb else None

    # Метод 2 (v7): те же зонды через IsPointInSpace
    if pts and z_min is not None:
        space, votes, total, pos, neg = vote_for_space_ipis(pts, z_min, z_max)
        if space is not None:
            facing = XYZ(-normal.X, -normal.Y, 0) if pos >= neg \
                     else XYZ(normal.X, normal.Y, 0)
            return vector_to_compass(facing), "ipis"

    # Метод 3: 2D-ближайшее, но только в Z-диапазоне стены
    try:
        mid    = wall.Location.Curve.Evaluate(0.5, True)
        best   = nearest_space_fallback(mid, z_min, z_max)
        if best and best.Location:
            sp_pt  = best.Location.Point
            dx     = mid.X - sp_pt.X
            dy     = mid.Y - sp_pt.Y
            length = math.sqrt(dx*dx + dy*dy)
            compass = vector_to_compass(XYZ(dx/length, dy/length, 0)) \
                      if length > 0.01 else None
            return compass, "fallback"
    except:
        pass

    return None, None


# ─── ОПРЕДЕЛЕНИЕ СТОРОНЫ СВЕТА: ВИТРАЖ ──────────────────────

def find_compass_for_curtain(curtain_wall, host_wall):
    """
    Специальная функция для витражей:
      - Z-диапазон зондов из bounding box ВИТРАЖА (правильный этаж).
      - нормаль/отступ из ХОСТ-СТЕНЫ (надёжнее тонкого витража).
    Возвращает (compass, method), method: "pts" | "ipis" | "fallback" | None.
    """
    normal = (get_wall_normal(host_wall) if host_wall is not None
              else None) or get_wall_normal(curtain_wall)
    if normal is None:
        return None, None

    bb = curtain_wall.get_BoundingBox(None)
    if bb is None:
        return None, None

    if host_wall is not None:
        offsets = get_wall_probe_offsets(host_wall)
    else:
        offsets = [MARGIN_FT * 2, MARGIN_FT * 4]

    try:
        curve = curtain_wall.Location.Curve
    except:
        return None, None

    h   = bb.Max.Z - bb.Min.Z
    pts = []

    for t in [0.25, 0.50, 0.75]:
        pt = curve.Evaluate(t, True)
        for z_r in [1.0/3.0, 2.0/3.0]:
            z = bb.Min.Z + h * z_r
            for offset in offsets:
                for sign in [1, -1]:
                    pts.append((
                        XYZ(
                            pt.X + normal.X * offset * sign,
                            pt.Y + normal.Y * offset * sign,
                            z
                        ),
                        sign
                    ))

    space, votes, total, pos, neg = vote_for_space(
        pts, get_element_phase(curtain_wall)
    )

    if space is not None:
        facing = XYZ(-normal.X, -normal.Y, 0) if pos >= neg \
                 else XYZ(normal.X, normal.Y, 0)
        return vector_to_compass(facing), "pts"

    # Метод 2 (v7): те же зонды через IsPointInSpace
    space, votes, total, pos, neg = vote_for_space_ipis(
        pts, bb.Min.Z, bb.Max.Z
    )
    if space is not None:
        facing = XYZ(-normal.X, -normal.Y, 0) if pos >= neg \
                 else XYZ(normal.X, normal.Y, 0)
        return vector_to_compass(facing), "ipis"

    # Метод 3: ближайший центроид в 2D в Z-диапазоне витража, compass = None
    # (compass будет взят из pre-кэша хост-стены)
    try:
        mid   = curtain_wall.Location.Curve.Evaluate(0.5, True)
        best  = nearest_space_fallback(mid, bb.Min.Z, bb.Max.Z)
        if best:
            return None, "fallback"  # compass=None → pre-кэш
    except:
        pass

    return None, None


# ─── ПОИСК ХОСТ-СТЕНЫ ДЛЯ ВИТРАЖА ───────────────────────────

def find_host_wall_for_curtain(curtain_wall, basic_walls):
    """
    Ищет несущую стену, в которую вставлен витраж, по трём критериям в 2D:
      1. Параллельность осей (dot >= 0.98)
      2. Перп. расстояние <= ширина_стены/2 + 10 мм
      3. Перекрытие проекций по длине
    """
    try:
        cw_curve  = curtain_wall.Location.Curve
        cw_dir    = XYZ(cw_curve.Direction.X, cw_curve.Direction.Y, 0).Normalize()
        cw_mid2d  = XYZ(cw_curve.Evaluate(0.5, True).X,
                        cw_curve.Evaluate(0.5, True).Y, 0)
        cw_s2d    = XYZ(cw_curve.GetEndPoint(0).X,
                        cw_curve.GetEndPoint(0).Y, 0)
        cw_e2d    = XYZ(cw_curve.GetEndPoint(1).X,
                        cw_curve.GetEndPoint(1).Y, 0)
    except:
        return None

    best_wall = None
    best_dist = float("inf")

    for wall in basic_walls:
        try:
            w_loc = wall.Location
            if not isinstance(w_loc, LocationCurve):
                continue

            w_curve = w_loc.Curve
            w_dir   = XYZ(w_curve.Direction.X, w_curve.Direction.Y, 0).Normalize()

            if abs(cw_dir.DotProduct(w_dir)) < 0.98:
                continue

            w_s2d      = XYZ(w_curve.GetEndPoint(0).X,
                             w_curve.GetEndPoint(0).Y, 0)
            v          = cw_mid2d - w_s2d
            dist_along = v.DotProduct(w_dir)
            proj       = w_s2d + w_dir.Multiply(dist_along)
            dist_perp  = cw_mid2d.DistanceTo(proj)

            tolerance  = wall.Width / 2.0 + (10.0 / 304.8)
            if dist_perp > tolerance:
                continue

            w_e2d    = XYZ(w_curve.GetEndPoint(1).X,
                           w_curve.GetEndPoint(1).Y, 0)
            w_length = w_s2d.DistanceTo(w_e2d)
            proj_s   = (cw_s2d - w_s2d).DotProduct(w_dir)
            proj_e   = (cw_e2d - w_s2d).DotProduct(w_dir)
            ov_start = max(min(proj_s, proj_e), 0.0)
            ov_end   = min(max(proj_s, proj_e), w_length)

            if ov_end <= ov_start:
                continue

            if dist_perp < best_dist:
                best_dist = dist_perp
                best_wall = wall

        except:
            pass

    return best_wall


# ─── ОБРАБОТКА ───────────────────────────────────────────────

def method_label(method):
    if method == "pts":
        return u"pts"
    if method == "ipis":
        return u"IPIS"
    if method == "fallback":
        return u"fallback-2D ПРОВЕРИТЬ"
    return u"?"


def process_wall(wall):
    """Возвращает (ok, msg, compass)."""
    compass, mtd = find_compass(wall)

    if compass is None:
        return False, u"сторона света не определена", None

    msgs = apply_orient_and_coeff(wall, compass)
    return (
        True,
        u"[{}] {} | {}".format(method_label(mtd), compass, u", ".join(msgs)),
        compass
    )


def process_curtain_wall(curtain_wall, host, precache_compass):
    """Обрабатывает витраж. Возвращает (ok, msg, compass)."""
    compass, mtd = find_compass_for_curtain(curtain_wall, host)

    # Если compass не определён зондами — берём из pre-кэша хост-стены
    if compass is None and precache_compass is not None:
        compass = precache_compass
        notes   = u"Ор:precache"
        if mtd == "fallback":
            notes = u"fallback-2D ПРОВЕРИТЬ, " + notes
    elif compass is not None:
        notes   = method_label(mtd)
    else:
        notes   = u"Ор:не_найдена"

    if compass is None:
        return False, u"сторона света не определена [{}]".format(notes), None

    msgs = [notes]
    msgs.extend(apply_orient_and_coeff(curtain_wall, compass))

    host_info = u"хост={}".format(host.Id.IntegerValue) if host else u"хост=нет"
    return (
        True,
        u"[{}] {} | {}".format(host_info, compass, u", ".join(msgs)),
        compass
    )


def process_window_door(element, wall_compass_cache):
    """Окно/дверь берёт сторону света от своей хост-стены."""
    try:
        host = element.Host
    except:
        host = None

    if host is None or get_category_id(host) != WALL_CAT:
        return False, u"хост-стена не найдена"

    host_id = host.Id.IntegerValue
    compass = wall_compass_cache.get(host_id)

    if compass is None:
        compass, _ = find_compass(host)

    if compass is None:
        return False, u"сторона света не определена (хост {})".format(host_id)

    msgs = apply_orient_and_coeff(element, compass)
    return (
        True,
        u"← хост {} [{}] | {}".format(host_id, compass, u", ".join(msgs))
    )


def process_floor(floor):
    """Перекрытие горизонтально — стороны света нет: пусто + K=1.0."""
    msgs = []

    dst_or = floor.LookupParameter(PARAM_ORIENT)
    if dst_or and not dst_or.IsReadOnly:
        try:
            dst_or.Set(u"")
            msgs.append(u"Ор=(пусто)")
        except:
            msgs.append(u"WARN:Ор")
    else:
        msgs.append(u"WARN:Ор")

    dst_co = floor.LookupParameter(PARAM_COEFF)
    if dst_co and not dst_co.IsReadOnly:
        try:
            dst_co.Set(1.0)
            msgs.append(u"K=1.0")
        except:
            msgs.append(u"WARN:Коэфф")
    else:
        msgs.append(u"WARN:Коэфф")

    return True, u"горизонтальное | {}".format(u", ".join(msgs))


# ─── ОСНОВНОЙ ЗАПУСК ─────────────────────────────────────────

try:
    # ── Проверка: включён ли расчёт объёмов ───────────────────
    try:
        avs = AreaVolumeSettings.GetAreaVolumeSettings(doc)
        if not avs.ComputeVolumes:
            forms.alert(
                u"В проекте ОТКЛЮЧЁН расчёт объёмов!\n\n"
                u"Поиск пространств по точкам работать не будет — "
                u"все элементы пойдут через неточный fallback.\n\n"
                u"Включите: вкладка «Анализ» → панель «Пространства и зоны» → "
                u"«Параметры площадей и объёмов» → «Площади и объёмы».\n\n"
                u"Рекомендуется включить и запустить инструмент заново.",
                title=u"Определение стороны света"
            )
    except:
        pass

    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        BuildingFilter(),
        u"Выберите стены / витражи / окна / двери / перекрытия → Готово"
    )

    elements = [doc.GetElement(r.ElementId) for r in refs
                if doc.GetElement(r.ElementId) is not None]

    if not elements:
        forms.alert(
            u"Элементы не выбраны.",
            title=u"Определение стороны света",
            exitscript=True
        )

    # ── Разбивка выбранных элементов по типам ─────────────────
    basic_walls   = []
    curtain_walls = []
    hosted        = []
    floors        = []

    for e in elements:
        cid = get_category_id(e)
        if cid == WALL_CAT:
            if is_curtain_wall(e):
                curtain_walls.append(e)
            else:
                basic_walls.append(e)
        elif cid in [WINDOW_CAT, DOOR_CAT]:
            hosted.append(e)
        elif cid == FLOOR_CAT:
            floors.append(e)

    # ── Сбор всех обычных стен документа (для поиска хостов витражей) ─
    all_basic_walls_doc = []
    if curtain_walls:
        for w in FilteredElementCollector(doc) \
                 .OfCategory(BuiltInCategory.OST_Walls) \
                 .WhereElementIsNotElementType() \
                 .ToElements():
            try:
                if not is_curtain_wall(w):
                    all_basic_walls_doc.append(w)
            except:
                pass

    # ── Preprocessing: хост-стены ─────────────────────────────
    wall_ids_seen       = set(w.Id.IntegerValue for w in basic_walls)
    auto_added_wall_ids = set()

    # Хост-стены окон/дверей → добавляем в basic_walls
    for el in hosted:
        try:
            host = el.Host
            if host and get_category_id(host) == WALL_CAT \
                    and not is_curtain_wall(host):
                hid = host.Id.IntegerValue
                if hid not in wall_ids_seen:
                    basic_walls.append(host)
                    wall_ids_seen.add(hid)
                    auto_added_wall_ids.add(hid)
        except:
            pass

    # Хосты витражей → только read-only pre-кэш compass
    curtain_host_map = {}   # curtain_id → host_wall
    curtain_precache = {}   # curtain_id → precache_compass (может быть None)

    for cwall in curtain_walls:
        host = find_host_wall_for_curtain(cwall, all_basic_walls_doc)
        curtain_host_map[cwall.Id.IntegerValue] = host

        if host is not None:
            pre_compass, _ = find_compass(host)
            curtain_precache[cwall.Id.IntegerValue] = pre_compass
        else:
            curtain_precache[cwall.Id.IntegerValue] = None

    # ── Транзакция ────────────────────────────────────────────
    log           = []
    success_count = 0
    skipped_count = 0

    wall_compass_cache = {}   # wall_id_int → compass

    t = Transaction(doc, u"PP: Определение стороны света")
    t.Start()

    # Пасс 1а: обычные стены (+ хост-стены окон/дверей)
    for wall in basic_walls:
        ok, msg, compass = process_wall(wall)
        wall_compass_cache[wall.Id.IntegerValue] = compass

        if ok: success_count += 1
        else:  skipped_count += 1

        mark = u" [авто-хост]" \
               if wall.Id.IntegerValue in auto_added_wall_ids else u""
        log.append(u"Стена {}{}: {}".format(wall.Id.IntegerValue, mark, msg))

    # Пасс 1б: витражи
    for cwall in curtain_walls:
        host             = curtain_host_map.get(cwall.Id.IntegerValue)
        precache_compass = curtain_precache.get(cwall.Id.IntegerValue)

        ok, msg, compass = process_curtain_wall(cwall, host, precache_compass)
        wall_compass_cache[cwall.Id.IntegerValue] = compass

        if ok: success_count += 1
        else:  skipped_count += 1

        log.append(u"Витраж {}: {}".format(cwall.Id.IntegerValue, msg))

    # Пасс 2: окна/двери
    for el in hosted:
        ok, msg = process_window_door(el, wall_compass_cache)

        if ok: success_count += 1
        else:  skipped_count += 1

        log.append(u"{} {}: {}".format(el_label(el), el.Id.IntegerValue, msg))

    # Пасс 3: перекрытия
    for floor in floors:
        ok, msg = process_floor(floor)

        if ok: success_count += 1
        else:  skipped_count += 1

        log.append(u"Перекрытие {}: {}".format(floor.Id.IntegerValue, msg))

    t.Commit()

    total_processed = (
        len(basic_walls) + len(curtain_walls) + len(hosted) + len(floors)
    )

    fallback_count = sum(1 for line in log if u"fallback" in line)
    ipis_count     = sum(1 for line in log if u"IPIS" in line)

    message = (
        u"Готово.\n\n"
        u"Выбрано:          {}\n"
        u"Обработано всего: {}\n"
        u"  авто-хост стен: {}\n"
        u"  витражей:       {}\n"
        u"Успешно:  {}\n"
        u"Пропущено: {}"
    ).format(
        len(elements),
        total_processed,
        len(auto_added_wall_ids),
        len(curtain_walls),
        success_count,
        skipped_count
    )

    if ipis_count > 0:
        message += (
            u"\n\nЧерез IsPointInSpace (IPIS): {} — GetSpaceAtPoint их "
            u"не нашёл, результат надёжный."
        ).format(ipis_count)

    if fallback_count > 0:
        message += (
            u"\n\nВНИМАНИЕ: {} элемент(ов) обработано через fallback — "
            u"зонды не нашли пространство. Проверьте результат!\n"
            u"Если таких много — проверьте расчёт объёмов и фазы пространств."
        ).format(fallback_count)

    # Полный лог в файл пишем всегда (тихая страховка)
    try:
        log_path = os.path.join(
            os.getenv("TEMP") or u".", u"PP_сторона_света_отчёт.txt"
        )
        with codecs.open(log_path, "w", "utf-8") as fh:
            fh.write(u"\n".join(log))
        message += u"\nФайл отчёта: {}".format(log_path)
    except:
        pass

    # Полный лог в окно pyRevit
    try:
        output = script.get_output()
        output.print_md(u"### Определение стороны света — отчёт")
        for line in log:
            print(line)
    except:
        pass

    forms.alert(message, title=u"Определение стороны света")

except OperationCanceledException:
    pass

except Exception as ex:
    try:
        t.RollBack()
    except:
        pass
    forms.alert(
        u"Ошибка:\n\n{}".format(unicode(ex)),
        title=u"Определение стороны света"
    )
