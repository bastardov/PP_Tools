# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
# Версия 8.
#
# Исправление v8 (зонды стен мимо, перекрытия — в порядке):
# Зонд отступал W/2+150мм от ЛИНИИ стены, считая её осью. Если линия
# привязки — наружная/чистовая поверхность (типично для наружных стен),
# зонд на W/2+150 попадает в ТОЛЩУ стены, а с другой стороны — на улицу.
# Теперь зонды на двух расстояниях: W/2+150 и W+150 от линии — покрывает
# любую привязку (ось / грань / несущая поверхность).
#
# Изменения v7 (51 из 101 элементов уходили в fallback при включённых объёмах
# и пространствах на всех этажах — GetSpaceAtPoint ненадёжен в проекте):
# 1. Второй метод поиска: если GetSpaceAtPoint не нашёл — голосование тех же
#    зондов через Space.IsPointInSpace() по пространствам с пересечением Z.
#    В отчёте помечается "IPIS".
# 2. Кэш списка пространств (один сбор за запуск вместо сбора на каждый отказ).
# 3. Полный отчёт дополнительно сохраняется в файл %TEMP%\PP_перенос_отчёт.txt.
#
# Исправление: поэтажные стены получали одно помещение на все этажи.
# Причина: зонды не находили пространство (фаза / отключённый расчёт объёмов),
# все стены уходили в 2D-fallback, который игнорировал Z — стены друг
# над другом получали одно и то же ближайшее в плане помещение.
#
# Изменения v6:
# 1. GetSpaceAtPoint вызывается с ФАЗОЙ элемента (PHASE_CREATED).
# 2. nearest_space_fallback фильтрует пространства по пересечению Z-диапазона
#    с элементом. Нет пересечения — элемент пропускается (лучше пропуск,
#    чем помещение другого этажа).
# 3. Проверка "Расчёт объёмов" (AreaVolumeSettings) с предупреждением на старте.
# 4. Fallback помечается в отчёте как "ПРОВЕРИТЬ", считается в итогах.
# 5. Полный лог выводится в окно pyRevit (в alert — только итоги).
#
# v5: витраж ищет пространство через собственный Z-диапазон (правильный этаж),
# но с нормалью и отступом хост-стены; хост-стена не перезаписывается.

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

from pp_settings import load_settings


doc   = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


# ─── НАСТРОЙКИ ───────────────────────────────────────────────

_settings = load_settings()

# Определять сторону света при переносе данных (вкладка "Теплопотери" в настройках PP Tools).
# Если выключено — параметры PARAM_ORIENT/PARAM_COEFF не трогаются:
# это нужно, чтобы при повторном запуске инструмента (например, только
# для переноса имени и номера системы) не затереть вручную скорректированные
# вручную стороны света.
DETECT_ORIENTATION = _settings.get("detect_orientation", True)

# Показывать отчёт (окно pyRevit + итоговое окно) только если включена галочка
# "Показывать отчёт pyRevit после переноса данных" на вкладке "Теплопотери".
# Файл отчёта в %TEMP% пишется всегда (он не всплывает на экран).
SHOW_REPORT = _settings.get("heatloss_show_report", True)

MARGIN_FT    = 0.15 / 0.3048
FLOOR_OFFSET = 0.10 / 0.3048

PARAM_TEMP   = "ADSK_Температура в помещении"
PARAM_NUM_NM = "PP_Номер имя помещения"
PARAM_AREA   = "ADSK_Размер_Площадь"
PARAM_ORIENT = "PP_Ориентация по стороне света"
PARAM_COEFF  = "PP_Добавка на сторону света"

COPY_FROM_WALL = [PARAM_TEMP, PARAM_NUM_NM]

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


def get_space_name(space):
    for pname in [None, u"Name", u"Имя"]:
        try:
            p = space.get_Parameter(BuiltInParameter.ROOM_NAME) \
                if pname is None else space.LookupParameter(pname)
            if p:
                v = p.AsString()
                if v:
                    return v
        except:
            pass
    return u""


def get_space_number(space):
    try:
        return space.Number or u""
    except:
        return u""


def get_element_area(element):
    cid = get_category_id(element)
    skip_host_area = (
        cid in [WINDOW_CAT, DOOR_CAT]
        or (cid == WALL_CAT and is_curtain_wall(element))
    )

    if not skip_host_area:
        try:
            p = element.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
            if p and p.HasValue:
                return p.AsDouble()
        except:
            pass

    try:
        p = element.LookupParameter(u"Площадь")
        if p and p.HasValue and p.StorageType == StorageType.Double:
            return p.AsDouble()
    except:
        pass

    try:
        def get_dp(el, name):
            if el is None:
                return None
            p = el.LookupParameter(name)
            if p and p.HasValue and p.StorageType == StorageType.Double:
                return p.AsDouble()
            return None

        w = get_dp(element, u"Ширина")
        if w is None and hasattr(element, "Symbol"):
            w = get_dp(element.Symbol, u"Ширина")

        h = get_dp(element, u"Высота")
        if h is None and hasattr(element, "Symbol"):
            h = get_dp(element.Symbol, u"Высота")

        if w and h:
            return w * h
    except:
        pass

    return None


def copy_one_param(src_el, dst_el, param_name):
    src = src_el.LookupParameter(param_name)
    dst = dst_el.LookupParameter(param_name)

    if src is None:      return "WARN:нет_источника"
    if dst is None:      return "WARN:нет_приёмника"
    if dst.IsReadOnly:   return "WARN:read-only"
    if not src.HasValue: return "WARN:пусто"

    try:
        if src.StorageType == StorageType.Double:
            dst.Set(src.AsDouble());         return "OK"
        if src.StorageType == StorageType.String:
            dst.Set(src.AsString() or u"");  return "OK"
        if src.StorageType == StorageType.Integer:
            dst.Set(src.AsInteger());        return "OK"
        if src.StorageType == StorageType.ElementId:
            dst.Set(src.AsElementId());      return "OK"
    except Exception as ex:
        return "ERR:{}".format(unicode(ex))

    return "WARN:тип"


def apply_orient_and_coeff(element, compass):
    msgs = []

    if not DETECT_ORIENTATION:
        msgs.append(u"Ор=пропущено (выкл. в настройках)")
        return msgs

    # Запись сокращённой стороны света ("С", "СВ", "З", "Ю" …) в текстовый
    # параметр PP_Ориентация по стороне света.
    # Важно: Parameter.Set(str) у нетекстового параметра НЕ бросает исключение,
    # а молча возвращает False — раньше лог писал "Ор=С", а в элемент ничего
    # не попадало. Поэтому проверяем тип хранения и результат Set().
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


def get_wall_offset(wall):
    try:
        return wall.Width / 2.0 + MARGIN_FT
    except:
        return 0.5 / 0.3048


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

    Без Z-фильтра поэтажные стены друг над другом получали одно
    и то же ближайшее в плане помещение (баг v5).
    Если ни одно пространство не пересекается по Z — возвращает None:
    лучше пропустить элемент, чем записать помещение другого этажа.
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


# ─── ПОИСК ПРОСТРАНСТВА: ОБЫЧНАЯ СТЕНА ──────────────────────

def find_space_and_compass(wall):
    """
    Для обычных (несущих) стен.
    Возвращает (space, votes, total, compass, method),
    method: "pts" | "ipis" | "fallback" | None.
    """
    normal = get_wall_normal(wall)
    if normal is None:
        return None, 0, 0, None, None

    phase                          = get_element_phase(wall)
    pts                            = get_wall_probe_points(wall)
    space, votes, total, pos, neg  = vote_for_space(pts, phase)

    if space is not None:
        facing = XYZ(-normal.X, -normal.Y, 0) if pos >= neg \
                 else XYZ(normal.X, normal.Y, 0)
        return space, votes, total, vector_to_compass(facing), "pts"

    bb    = wall.get_BoundingBox(None)
    z_min = bb.Min.Z if bb else None
    z_max = bb.Max.Z if bb else None

    # Метод 2 (v7): те же зонды через IsPointInSpace
    if pts and z_min is not None:
        space, votes, total, pos, neg = vote_for_space_ipis(pts, z_min, z_max)
        if space is not None:
            facing = XYZ(-normal.X, -normal.Y, 0) if pos >= neg \
                     else XYZ(normal.X, normal.Y, 0)
            return space, votes, total, vector_to_compass(facing), "ipis"

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
            return best, 0, 0, compass, "fallback"
    except:
        pass

    return None, 0, 0, None, None


# ─── ПОИСК ПРОСТРАНСТВА: ВИТРАЖ ─────────────────────────────

def find_space_and_compass_for_curtain(curtain_wall, host_wall):
    """
    Специальная функция для витражей. Решает две проблемы:

    1. ПРАВИЛЬНЫЙ ЭТАЖ (регрессия v4):
       Z-диапазон зондов берётся из bounding box ВИТРАЖА, а не хост-стены.
       Хост-стена может быть многоэтажной — её Z-диапазон дал бы зонды
       на верхнем этаже и неверное помещение.

    2. ПРАВИЛЬНЫЙ ОТСТУП (оригинальная проблема):
       Нормаль и горизонтальный отступ зонда берётся из ХОСТ-СТЕНЫ.
       Витраж толщиной 25 мм — стандартный отступ (width/2 + 150 мм)
       почти не выходит за его пределы, нормаль хост-стены надёжнее.

    Возвращает (space, votes, total, compass, method),
    method: "pts" | "ipis" | "fallback" | None.
    """
    # Нормаль: из хост-стены (если нет — из самого витража, они совпадают)
    normal = (get_wall_normal(host_wall) if host_wall is not None
              else None) or get_wall_normal(curtain_wall)
    if normal is None:
        return None, 0, 0, None, None

    # Z-диапазон: из bounding box ВИТРАЖА
    bb = curtain_wall.get_BoundingBox(None)
    if bb is None:
        return None, 0, 0, None, None

    # Отступы: из хост-стены, на двух расстояниях (v8 — линия привязки
    # может быть гранью, а не осью). Без хоста — небольшой двойной отступ.
    if host_wall is not None:
        offsets = get_wall_probe_offsets(host_wall)
    else:
        offsets = [MARGIN_FT * 2, MARGIN_FT * 4]

    try:
        curve = curtain_wall.Location.Curve
    except:
        return None, 0, 0, None, None

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
        return space, votes, total, vector_to_compass(facing), "pts"

    # Метод 2 (v7): те же зонды через IsPointInSpace
    space, votes, total, pos, neg = vote_for_space_ipis(
        pts, bb.Min.Z, bb.Max.Z
    )
    if space is not None:
        facing = XYZ(-normal.X, -normal.Y, 0) if pos >= neg \
                 else XYZ(normal.X, normal.Y, 0)
        return space, votes, total, vector_to_compass(facing), "ipis"

    # Метод 3: ближайший центроид в 2D в Z-диапазоне витража, compass = None
    # (compass будет взят из pre-кэша хост-стены в process_curtain_wall)
    try:
        mid   = curtain_wall.Location.Curve.Evaluate(0.5, True)
        best  = nearest_space_fallback(mid, bb.Min.Z, bb.Max.Z)
        if best:
            return best, 0, 0, None, "fallback"  # compass=None → pre-кэш
    except:
        pass

    return None, 0, 0, None, None


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


# ─── ОБРАБОТКА: ОБЫЧНАЯ СТЕНА ────────────────────────────────

def method_label(method, votes, total):
    if method == "pts":
        return u"{}/{}pts".format(votes, total)
    if method == "ipis":
        return u"IPIS {}/{}".format(votes, total)
    return u"fallback-2D ПРОВЕРИТЬ"


def process_wall(wall):
    """Возвращает (ok, msg, compass)."""
    space, votes, total, compass, mtd = find_space_and_compass(wall)

    if space is None:
        return False, u"пространство не найдено", None

    method = method_label(mtd, votes, total)
    msgs   = []

    src_t = space.LookupParameter(PARAM_TEMP)
    dst_t = wall.LookupParameter(PARAM_TEMP)
    if src_t and dst_t and not dst_t.IsReadOnly and src_t.HasValue:
        try:
            dst_t.Set(src_t.AsDouble())
            msgs.append(u"T={:.1f}".format(src_t.AsDouble()))
        except:
            msgs.append(u"WARN:T")
    else:
        msgs.append(u"WARN:T")

    num    = get_space_number(space)
    name   = get_space_name(space)
    num_nm = u"{} {}".format(num, name).strip()
    dst_nn = wall.LookupParameter(PARAM_NUM_NM)
    if dst_nn and not dst_nn.IsReadOnly:
        try:
            dst_nn.Set(num_nm)
            msgs.append(u'Пом="{}"'.format(num_nm))
        except:
            msgs.append(u"WARN:НомерИмя")
    else:
        msgs.append(u"WARN:НомерИмя")

    area_val = get_element_area(wall)
    dst_a    = wall.LookupParameter(PARAM_AREA)
    if area_val is not None and dst_a and not dst_a.IsReadOnly:
        try:
            dst_a.Set(area_val)
            msgs.append(u"S={:.3f}".format(area_val))
        except:
            msgs.append(u"WARN:Площадь")
    else:
        msgs.append(u"WARN:Площадь")

    msgs.extend(apply_orient_and_coeff(wall, compass))

    return (
        True,
        u"[{}] → {} | {}".format(method, num_nm, u", ".join(msgs)),
        compass
    )


# ─── ОБРАБОТКА: ВИТРАЖ ───────────────────────────────────────

def process_curtain_wall(curtain_wall, host, precache_compass):
    """
    Обрабатывает витраж.

    host            — хост-стена (или None)
    precache_compass — compass хост-стены, вычисленный ДО транзакции
                       (чтение-только, без записи параметров хост-стены)

    Пространство и compass ищутся через find_space_and_compass_for_curtain:
    - Z-диапазон зондов = из витража (правильный этаж)
    - нормаль/отступ    = из хост-стены (надёжнее тонкого витража)

    Возвращает (ok, msg, compass).
    """
    space, votes, total, compass, mtd = find_space_and_compass_for_curtain(
        curtain_wall, host
    )

    # Если compass не определён зондами — берём из pre-кэша хост-стены
    if compass is None and precache_compass is not None:
        compass = precache_compass
        notes   = u"Ор:precache"
        if mtd == "fallback":
            notes = u"fallback-2D ПРОВЕРИТЬ, " + notes
    elif compass is not None:
        notes   = method_label(mtd, votes, total)
    else:
        notes   = u"Ор:не_найдена"

    if space is None:
        return False, u"пространство не найдено [{}]".format(notes), compass

    msgs   = [notes]
    num    = get_space_number(space)
    name   = get_space_name(space)
    num_nm = u"{} {}".format(num, name).strip()

    # Температура — из найденного пространства напрямую
    src_t = space.LookupParameter(PARAM_TEMP)
    dst_t = curtain_wall.LookupParameter(PARAM_TEMP)
    if src_t and dst_t and not dst_t.IsReadOnly and src_t.HasValue:
        try:
            dst_t.Set(src_t.AsDouble())
            msgs.append(u"T={:.1f}".format(src_t.AsDouble()))
        except:
            msgs.append(u"WARN:T")
    else:
        msgs.append(u"WARN:T")

    # Номер + имя помещения — из найденного пространства напрямую
    dst_nn = curtain_wall.LookupParameter(PARAM_NUM_NM)
    if dst_nn and not dst_nn.IsReadOnly:
        try:
            dst_nn.Set(num_nm)
            msgs.append(u'Пом="{}"'.format(num_nm))
        except:
            msgs.append(u"WARN:НомерИмя")
    else:
        msgs.append(u"WARN:НомерИмя")

    # Площадь витража
    area_val = get_element_area(curtain_wall)
    dst_a    = curtain_wall.LookupParameter(PARAM_AREA)
    if area_val is not None and dst_a and not dst_a.IsReadOnly:
        try:
            dst_a.Set(area_val)
            msgs.append(u"S={:.3f}".format(area_val))
        except:
            msgs.append(u"WARN:Площадь")
    else:
        msgs.append(u"WARN:Площадь")

    # Ориентация + коэффициент
    msgs.extend(apply_orient_and_coeff(curtain_wall, compass))

    host_info = u"хост={}".format(host.Id.IntegerValue) if host else u"хост=нет"
    return (
        True,
        u"[{}] {} → {} | {}".format(
            host_info, compass or u"?", num_nm, u", ".join(msgs)
        ),
        compass
    )


# ─── ОБРАБОТКА: ОКНО / ДВЕРЬ ─────────────────────────────────

def process_window_door(element, wall_compass_cache):
    try:
        host = element.Host
    except:
        host = None

    if host is None or get_category_id(host) != WALL_CAT:
        return False, u"хост-стена не найдена"

    msgs    = []
    host_id = host.Id.IntegerValue
    compass = wall_compass_cache.get(host_id)

    if compass is None:
        _, _, _, compass, _ = find_space_and_compass(host)

    for pname in COPY_FROM_WALL:
        result     = copy_one_param(host, element, pname)
        short_name = pname.split(u"_")[-1][:6]
        msgs.append(u"{}:{}".format(short_name, result))

    area_val = get_element_area(element)
    dst_a    = element.LookupParameter(PARAM_AREA)
    if area_val is not None and dst_a and not dst_a.IsReadOnly:
        try:
            dst_a.Set(area_val)
            msgs.append(u"S={:.3f}".format(area_val))
        except:
            msgs.append(u"WARN:Площадь")
    else:
        msgs.append(u"WARN:Площадь")

    msgs.extend(apply_orient_and_coeff(element, compass))

    return (
        True,
        u"← хост {} [{}] | {}".format(host_id, compass or u"?", u", ".join(msgs))
    )


# ─── ОБРАБОТКА: ПЕРЕКРЫТИЕ ───────────────────────────────────

def get_floor_probe_points(floor):
    bb = floor.get_BoundingBox(None)
    if bb is None:
        return []

    pts = []
    for tx in [0.25, 0.50, 0.75]:
        for ty in [0.25, 0.50, 0.75]:
            x = bb.Min.X + (bb.Max.X - bb.Min.X) * tx
            y = bb.Min.Y + (bb.Max.Y - bb.Min.Y) * ty
            pts.append((XYZ(x, y, bb.Min.Z - FLOOR_OFFSET), -1))
            pts.append((XYZ(x, y, bb.Max.Z + FLOOR_OFFSET),  1))

    return pts


def find_space_for_floor(floor):
    probes     = get_floor_probe_points(floor)
    phase      = get_element_phase(floor)
    votes      = {}
    space_refs = {}

    for pt, sign in probes:
        try:
            sp = get_space_at_point(pt, phase)
            if sp is not None and sp.Area > 0:
                key = sp.Id.IntegerValue
                votes[key]      = votes.get(key, 0) + 1
                space_refs[key] = sp
        except:
            pass

    if votes:
        best_key = max(votes, key=lambda k: votes[k])
        return space_refs[best_key], votes[best_key], len(probes), "pts"

    bb = floor.get_BoundingBox(None)
    if bb is None:
        return None, 0, 0, None

    # Метод 2 (v7): те же зонды через IsPointInSpace
    # (Z-диапазон с запасом на этаж выше/ниже перекрытия)
    sp, v, tot, _, _ = vote_for_space_ipis(
        probes, bb.Min.Z - 10.0, bb.Max.Z + 10.0
    )
    if sp is not None:
        return sp, v, tot, "ipis"

    # Метод 3: ближайший центроид
    center = XYZ(
        (bb.Min.X + bb.Max.X) / 2.0,
        (bb.Min.Y + bb.Max.Y) / 2.0,
        (bb.Min.Z + bb.Max.Z) / 2.0
    )
    best = nearest_space_fallback(
        center, bb.Min.Z - 10.0, bb.Max.Z + 10.0
    )
    if best:
        return best, 0, 0, "fallback"

    return None, 0, 0, None


def process_floor(floor):
    space, votes, total, mtd = find_space_for_floor(floor)

    if space is None:
        return False, u"пространство не найдено"

    method = method_label(mtd, votes, total)
    msgs   = []

    src_t = space.LookupParameter(PARAM_TEMP)
    dst_t = floor.LookupParameter(PARAM_TEMP)
    if src_t and dst_t and not dst_t.IsReadOnly and src_t.HasValue:
        try:
            dst_t.Set(src_t.AsDouble())
            msgs.append(u"T={:.1f}".format(src_t.AsDouble()))
        except:
            msgs.append(u"WARN:T")
    else:
        msgs.append(u"WARN:T")

    num    = get_space_number(space)
    name   = get_space_name(space)
    num_nm = u"{} {}".format(num, name).strip()
    dst_nn = floor.LookupParameter(PARAM_NUM_NM)
    if dst_nn and not dst_nn.IsReadOnly:
        try:
            dst_nn.Set(num_nm)
            msgs.append(u'Пом="{}"'.format(num_nm))
        except:
            msgs.append(u"WARN:НомерИмя")
    else:
        msgs.append(u"WARN:НомерИмя")

    area_val = get_element_area(floor)
    dst_a    = floor.LookupParameter(PARAM_AREA)
    if area_val is not None and dst_a and not dst_a.IsReadOnly:
        try:
            dst_a.Set(area_val)
            msgs.append(u"S={:.3f}".format(area_val))
        except:
            msgs.append(u"WARN:Площадь")
    else:
        msgs.append(u"WARN:Площадь")

    dst_or = floor.LookupParameter(PARAM_ORIENT)
    if dst_or and not dst_or.IsReadOnly:
        try:
            dst_or.Set(u"")
        except:
            pass

    dst_co = floor.LookupParameter(PARAM_COEFF)
    if dst_co and not dst_co.IsReadOnly:
        try:
            dst_co.Set(1.0)
            msgs.append(u"K=1.0")
        except:
            msgs.append(u"WARN:Коэфф")
    else:
        msgs.append(u"WARN:Коэфф")

    return True, u"[{}] → {} | {}".format(method, num_nm, u", ".join(msgs))


# ─── ОСНОВНОЙ ЗАПУСК ─────────────────────────────────────────

try:
    # ── Проверка: включён ли расчёт объёмов ───────────────────
    # Без него GetSpaceAtPoint не находит пространства по 3D-точке,
    # все элементы уходят в fallback.
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
                title=u"Перенос данных из пространств"
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
            title=u"Перенос данных из пространств",
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
    #
    # Для окон/дверей: хост-стена добавляется в basic_walls (как раньше),
    # обрабатывается в пасс 1а, compass кладётся в кэш.
    #
    # Для витражей: хост-стена НЕ добавляется в basic_walls.
    # Её compass вычисляется READ-ONLY (без записи параметров),
    # сохраняется в curtain_precache — используется только как fallback,
    # если зонды витража не нашли пространство.
    # Это избегает записи данных другого этажа в хост-стену.

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
    curtain_host_map    = {}   # curtain_id → host_wall
    curtain_precache    = {}   # curtain_id → precache_compass (может быть None)

    for cwall in curtain_walls:
        host = find_host_wall_for_curtain(cwall, all_basic_walls_doc)
        curtain_host_map[cwall.Id.IntegerValue] = host

        if host is not None:
            # Читаем compass хост-стены без транзакции (только для fallback)
            _, _, _, pre_compass, _ = find_space_and_compass(host)
            curtain_precache[cwall.Id.IntegerValue] = pre_compass
        else:
            curtain_precache[cwall.Id.IntegerValue] = None

    # ── Транзакция ────────────────────────────────────────────
    log           = []
    success_count = 0
    skipped_count = 0

    wall_compass_cache = {}   # wall_id_int → compass

    t = Transaction(doc, u"PP: Перенос данных из пространств")
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
    # Каждый витраж сам зондирует пространство через find_space_and_compass_for_curtain:
    # - Z из витража (правильный этаж)
    # - нормаль/отступ из хост-стены
    # Если compass не нашли — используется precache_compass (fallback)
    for cwall in curtain_walls:
        host            = curtain_host_map.get(cwall.Id.IntegerValue)
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

    # Полный лог в файл пишем всегда (он не всплывает на экран — тихая страховка)
    try:
        log_path = os.path.join(
            os.getenv("TEMP") or u".", u"PP_перенос_отчёт.txt"
        )
        with codecs.open(log_path, "w", "utf-8") as fh:
            fh.write(u"\n".join(log))
        message += u"\nФайл отчёта: {}".format(log_path)
    except:
        pass

    # Технический отчёт pyRevit (серое окно-терминал с построчным логом) —
    # только если включена галочка на вкладке "Теплопотери".
    if SHOW_REPORT:
        message += u"\n\nПолный отчёт — в окне pyRevit."

        # Полный лог в окно pyRevit (alert обрезал до 15 строк)
        try:
            output = script.get_output()
            output.print_md(u"### Перенос данных из пространств — отчёт")
            for line in log:
                print(line)
        except:
            pass

    # Итоговое белое окно — показываем всегда по завершении.
    forms.alert(message, title=u"Перенос данных из пространств")

except OperationCanceledException:
    pass

except Exception as ex:
    try:
        t.RollBack()
    except:
        pass
    forms.alert(
        u"Ошибка:\n\n{}".format(unicode(ex)),
        title=u"Перенос данных из пространств"
    )