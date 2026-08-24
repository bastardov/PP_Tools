# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
# ИИ Агент → Выгрузить.
#
# Собирает всю модель в один JSON-файл для анализа ИИ-агентом расчёта
# теплопотерь. Выгружаются:
#   - пространства (MEP Spaces): номер, имя, уровень, площадь, температура,
#     точка вставки, габариты, контур (полигон в плане);
#   - стены (обычные и витражи): линия, ширина, высота, нормаль, параметры;
#   - окна и двери: точка, хост-стена, габариты, площадь, параметры;
#   - перекрытия: габариты, уровень, площадь, параметры.
#
# Координаты — в мм, площади — в м². Инструмент только читает модель.
# Файл: <корень плагина>\AI_обмен\model_export.json

import clr
import os
import json
import codecs
import math
import datetime

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    BuiltInParameter,
    StorageType,
    LocationCurve,
    LocationPoint,
    WallKind,
    SpatialElementBoundaryOptions,
    AreaVolumeSettings,
    XYZ,
)

import pp_wpf

from pp_settings import get_extension_root


doc = __revit__.ActiveUIDocument.Document

FT2MM  = 304.8
FT2_M2 = 0.09290304   # 1 фут² в м²

# Параметры теплопотерь, которые выгружаем у ограждающих элементов.
ENV_PARAMS = [
    u"ADSK_Температура в помещении",
    u"PP_Номер имя помещения",
    u"ADSK_Размер_Площадь",
    u"PP_Ориентация по стороне света",
    u"PP_Добавка на сторону света",
]

# Параметры, которые выгружаем у пространств (источник данных).
SPACE_PARAMS = [
    u"ADSK_Температура в помещении",
]


# ─── УТИЛИТЫ ─────────────────────────────────────────────────

def mm(v):
    try:
        return round(v * FT2MM, 1)
    except:
        return None


def m2(v):
    try:
        return round(v * FT2_M2, 4)
    except:
        return None


def pt3(p):
    try:
        return [mm(p.X), mm(p.Y), mm(p.Z)]
    except:
        return None


def bbox_of(el):
    try:
        bb = el.get_BoundingBox(None)
        if bb is None:
            return None
        return {"min": pt3(bb.Min), "max": pt3(bb.Max)}
    except:
        return None


def type_name(el):
    try:
        t = doc.GetElement(el.GetTypeId())
        if t is not None:
            return t.Name
    except:
        pass
    return None


def family_name(el):
    # FamilyInstance
    try:
        return el.Symbol.Family.Name
    except:
        pass
    # Стены / перекрытия
    try:
        t = doc.GetElement(el.GetTypeId())
        if t is not None and t.Family is not None:
            return t.Family.Name
    except:
        pass
    return None


def level_name(el):
    try:
        lv = doc.GetElement(el.LevelId)
        if lv is not None:
            return lv.Name
    except:
        pass
    return None


def read_param(el, name):
    u"""Возвращает {storage, value, text} или None, если параметра нет."""
    try:
        p = el.LookupParameter(name)
    except:
        p = None
    if p is None:
        return None
    try:
        st = p.StorageType
        if st == StorageType.Double:
            val = p.AsDouble() if p.HasValue else None
            return {"storage": u"Double", "value": val, "text": p.AsValueString()}
        if st == StorageType.String:
            v = p.AsString()
            return {"storage": u"String", "value": v, "text": v}
        if st == StorageType.Integer:
            return {"storage": u"Integer", "value": p.AsInteger(), "text": p.AsValueString()}
        if st == StorageType.ElementId:
            return {"storage": u"ElementId", "value": p.AsElementId().IntegerValue,
                    "text": p.AsValueString()}
    except:
        return None
    return None


def read_params(el, names):
    out = {}
    for n in names:
        r = read_param(el, n)
        if r is not None:
            out[n] = r
    return out


# ─── СБОР ЭЛЕМЕНТОВ ──────────────────────────────────────────

def collect_spaces():
    result = []
    col = FilteredElementCollector(doc) \
        .OfCategory(BuiltInCategory.OST_MEPSpaces) \
        .WhereElementIsNotElementType() \
        .ToElements()

    opts = SpatialElementBoundaryOptions()

    for sp in col:
        try:
            if sp.Area <= 0:
                continue
        except:
            continue

        # Имя пространства
        name = u""
        try:
            pn = sp.get_Parameter(BuiltInParameter.ROOM_NAME)
            if pn is not None:
                name = pn.AsString() or u""
        except:
            pass

        # Точка вставки
        loc_pt = None
        try:
            if isinstance(sp.Location, LocationPoint):
                loc_pt = pt3(sp.Location.Point)
        except:
            pass

        # Контур пространства (внешний цикл), полигон в плане
        poly = []
        try:
            loops = sp.GetBoundarySegments(opts)
            if loops is not None and loops.Count > 0:
                for seg in loops[0]:
                    c = seg.GetCurve()
                    q = c.GetEndPoint(0)
                    poly.append([mm(q.X), mm(q.Y)])
        except:
            poly = []

        item = {
            "id":     sp.Id.IntegerValue,
            "number": (sp.Number or u""),
            "name":   name,
            "level":  level_name(sp),
            "area_m2": m2(sp.Area),
            "point":  loc_pt,
            "bbox":   bbox_of(sp),
            "polygon": poly,
            "params": read_params(sp, SPACE_PARAMS),
        }
        result.append(item)
    return result


def collect_walls():
    walls = []
    col = FilteredElementCollector(doc) \
        .OfCategory(BuiltInCategory.OST_Walls) \
        .WhereElementIsNotElementType() \
        .ToElements()

    for w in col:
        try:
            is_curtain = (w.WallType.Kind == WallKind.Curtain)
        except:
            is_curtain = False

        start = None
        end   = None
        normal = None
        try:
            loc = w.Location
            if isinstance(loc, LocationCurve):
                crv = loc.Curve
                start = pt3(crv.GetEndPoint(0))
                end   = pt3(crv.GetEndPoint(1))
                try:
                    d = crv.Direction
                    n = XYZ(-d.Y, d.X, 0).Normalize()
                    normal = [round(n.X, 4), round(n.Y, 4)]
                except:
                    normal = None
        except:
            pass

        width = None
        try:
            width = mm(w.Width)
        except:
            pass

        area = None
        try:
            p = w.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
            if p is not None and p.HasValue:
                area = m2(p.AsDouble())
        except:
            pass

        walls.append({
            "id":        w.Id.IntegerValue,
            "is_curtain": is_curtain,
            "type":      type_name(w),
            "family":    family_name(w),
            "level":     level_name(w),
            "width_mm":  width,
            "start":     start,
            "end":       end,
            "normal":    normal,
            "area_m2":   area,
            "bbox":      bbox_of(w),
            "params":    read_params(w, ENV_PARAMS),
        })
    return walls


def collect_openings():
    u"""Окна и двери."""
    result = []
    for bic, label in [(BuiltInCategory.OST_Windows, u"Окно"),
                       (BuiltInCategory.OST_Doors, u"Дверь")]:
        col = FilteredElementCollector(doc) \
            .OfCategory(bic) \
            .WhereElementIsNotElementType() \
            .ToElements()

        for el in col:
            host_id = None
            try:
                if el.Host is not None:
                    host_id = el.Host.Id.IntegerValue
            except:
                pass

            loc_pt = None
            try:
                if isinstance(el.Location, LocationPoint):
                    loc_pt = pt3(el.Location.Point)
            except:
                pass

            area = None
            try:
                p = el.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
                if p is not None and p.HasValue:
                    area = m2(p.AsDouble())
            except:
                pass

            result.append({
                "id":       el.Id.IntegerValue,
                "kind":     label,
                "type":     type_name(el),
                "family":   family_name(el),
                "level":    level_name(el),
                "host_id":  host_id,
                "point":    loc_pt,
                "area_m2":  area,
                "bbox":     bbox_of(el),
                "params":   read_params(el, ENV_PARAMS),
            })
    return result


def collect_floors():
    result = []
    col = FilteredElementCollector(doc) \
        .OfCategory(BuiltInCategory.OST_Floors) \
        .WhereElementIsNotElementType() \
        .ToElements()

    for fl in col:
        area = None
        try:
            p = fl.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
            if p is not None and p.HasValue:
                area = m2(p.AsDouble())
        except:
            pass

        result.append({
            "id":      fl.Id.IntegerValue,
            "type":    type_name(fl),
            "family":  family_name(fl),
            "level":   level_name(fl),
            "area_m2": area,
            "bbox":    bbox_of(fl),
            "params":  read_params(fl, ENV_PARAMS),
        })
    return result


# ─── ГЛАВНЫЙ ЗАПУСК ──────────────────────────────────────────

TOOL_TITLE = u"Выгрузить"


try:
    # Предупреждение, если отключён расчёт объёмов (пространства могут быть
    # без площади/геометрии).
    try:
        avs = AreaVolumeSettings.GetAreaVolumeSettings(doc)
        if not avs.ComputeVolumes:
            pp_wpf.show_report(
                u"В проекте ОТКЛЮЧЁН расчёт объёмов.\n\n"
                u"Пространства могут выгрузиться без корректной площади и контура.\n"
                u"Рекомендуется включить расчёт объёмов и выгрузить заново.",
                title=u"Расчёт объёмов отключён",
                subtitle=TOOL_TITLE
            )
    except:
        pass

    spaces   = collect_spaces()
    walls    = collect_walls()
    openings = collect_openings()
    floors   = collect_floors()

    # Истинный север (угол поворота проекта), справочно.
    true_north_deg = None
    try:
        pl = doc.ActiveProjectLocation
        pos = pl.GetProjectPosition(XYZ(0, 0, 0))
        true_north_deg = round(math.degrees(pos.Angle), 3)
    except:
        pass

    data = {
        "meta": {
            "schema_version": 1,
            "document":  doc.Title,
            "exported":  datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "units":     {"length": "mm", "area": "m2"},
            "true_north_deg": true_north_deg,
            "env_params": ENV_PARAMS,
            "counts": {
                "spaces":   len(spaces),
                "walls":    len(walls),
                "openings": len(openings),
                "floors":   len(floors),
            },
        },
        "spaces":   spaces,
        "walls":    walls,
        "openings": openings,
        "floors":   floors,
    }

    # Папка обмена рядом с плагином.
    data_dir = os.path.join(get_extension_root(), u"AI_обмен")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)

    out_path = os.path.join(data_dir, u"model_export.json")
    with codecs.open(out_path, "w", "utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)

    message = (
        u"Пространства: {}\n"
        u"Стены:        {}\n"
        u"Окна/двери:   {}\n"
        u"Перекрытия:   {}\n\n"
        u"Файл:\n{}"
    ).format(len(spaces), len(walls), len(openings), len(floors), out_path)

    pp_wpf.show_report(
        message,
        title=u"Модель выгружена",
        subtitle=TOOL_TITLE
    )

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка выгрузки",
        subtitle=TOOL_TITLE,
        is_error=True
    )
