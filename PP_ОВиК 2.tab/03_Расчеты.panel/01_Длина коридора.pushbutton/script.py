# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    SpatialElementBoundaryOptions,
    BuiltInParameter,
    Transaction,
    Line,
    XYZ,
    ViewType,
    RevitLinkInstance
)
from Autodesk.Revit.DB.Mechanical import Space as MEPSpace
from Autodesk.Revit.DB.Architecture import Room as ArchRoom
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException

import System
import pp_wpf
import pp_paint_dialog
import pp_settings

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TOOL_TITLE = u"Длина коридора"


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта."""
    pass


def fail(message):
    raise Stop(message)


def rollback():
    try:
        if "t" in globals():
            _t = globals()["t"]
            if hasattr(_t, "HasStarted") and _t.HasStarted() and not _t.HasEnded():
                _t.RollBack()
    except Exception:
        pass


FT_TO_M = 0.3048
TOL = 0.0001

ALLOWED_VIEW_TYPES = (
    ViewType.FloorPlan,
    ViewType.CeilingPlan,
    ViewType.AreaPlan,
    ViewType.EngineeringPlan,
    ViewType.Detail,
    ViewType.DraftingView
)


class SpaceRoomFilter(ISelectionFilter):
    u"""Пространства и помещения текущего документа."""
    def AllowElement(self, elem):
        try:
            return isinstance(elem, MEPSpace) or isinstance(elem, ArchRoom)
        except:
            return False

    def AllowReference(self, ref, point):
        return False


class LinkRoomFilter(ISelectionFilter):
    u"""Помещения из RVT-связей.

    AllowElement пропускает любой RevitLinkInstance (иначе ничего не
    подсвечивается). AllowReference уточняет, что внутри связи должно
    лежать именно помещение (ArchRoom).
    """
    def AllowElement(self, elem):
        try:
            return isinstance(elem, RevitLinkInstance)
        except:
            return False

    def AllowReference(self, ref, point):
        try:
            link_inst = doc.GetElement(ref.ElementId)
            if not isinstance(link_inst, RevitLinkInstance):
                return False
            linked_doc = link_inst.GetLinkDocument()
            if linked_doc is None:
                return False
            linked_elem = linked_doc.GetElement(ref.LinkedElementId)
            return isinstance(linked_elem, ArchRoom)
        except:
            return False


def get_spatial_number(elem):
    try:
        if elem.Number:
            return elem.Number
    except:
        pass
    try:
        p = elem.get_Parameter(BuiltInParameter.ROOM_NUMBER)
        if p and p.HasValue:
            return p.AsString() or u""
    except:
        pass
    return u""


def get_spatial_name(elem):
    try:
        if elem.Name:
            return elem.Name
    except:
        pass
    try:
        p = elem.get_Parameter(BuiltInParameter.ROOM_NAME)
        if p and p.HasValue:
            return p.AsString() or u""
    except:
        pass
    return u""


def get_spatial_kind(elem):
    if isinstance(elem, MEPSpace):
        return u"Пространство"
    if isinstance(elem, ArchRoom):
        return u"Помещение"
    return u"Элемент"


def get_spatial_label(elem):
    number = get_spatial_number(elem)
    name = get_spatial_name(elem)
    label = u"{} {}".format(number, name).strip()
    if not label:
        label = u"Id {}".format(elem.Id.IntegerValue)
    return label


def get_boundary_points_xyz(elem):
    u"""Контур из текущего документа (мировые координаты)."""
    opts = SpatialElementBoundaryOptions()
    loops = elem.GetBoundarySegments(opts)
    points = []
    if loops is None:
        return points
    best_loop = None
    best_count = 0
    for loop in loops:
        if len(loop) > best_count:
            best_loop = loop
            best_count = len(loop)
    if best_loop is None:
        return points
    for seg in best_loop:
        crv = seg.GetCurve()
        p1 = crv.GetEndPoint(0)
        p2 = crv.GetEndPoint(1)
        points.append((p1.X, p1.Y, p1.Z))
        points.append((p2.X, p2.Y, p2.Z))
    return points


def get_linked_boundary_points_xyz(link_inst, linked_room):
    u"""Контур помещения из RVT-связи в координатах хост-документа.

    Геометрия связанного помещения живёт в локальной СК связанного файла.
    GetTotalTransform() пересчитывает её в мировые координаты хоста.
    """
    transform = link_inst.GetTotalTransform()
    opts = SpatialElementBoundaryOptions()
    loops = linked_room.GetBoundarySegments(opts)
    points = []
    if loops is None:
        return points
    best_loop = None
    best_count = 0
    for loop in loops:
        if len(loop) > best_count:
            best_loop = loop
            best_count = len(loop)
    if best_loop is None:
        return points
    for seg in best_loop:
        crv = seg.GetCurve()
        p1 = transform.OfPoint(crv.GetEndPoint(0))
        p2 = transform.OfPoint(crv.GetEndPoint(1))
        points.append((p1.X, p1.Y, p1.Z))
        points.append((p2.X, p2.Y, p2.Z))
    return points


def unique_sorted(values):
    result = []
    for value in sorted(values):
        if not result:
            result.append(value)
        elif abs(value - result[-1]) > TOL:
            result.append(value)
    return result


def is_full_rectangle(cells, x_count, y_count):
    return len(cells) == x_count * y_count


def _ray_cast_inside(poly, x, y):
    inside = False
    count = len(poly)
    last_index = count - 1
    for index in range(count):
        xi, yi = poly[index]
        xj, yj = poly[last_index]
        if ((yi > y) != (yj > y)):
            denom = yj - yi
            if abs(denom) < TOL:
                denom = TOL
            x_cross = (xj - xi) * (y - yi) / denom + xi
            if x < x_cross:
                inside = not inside
        last_index = index
    return inside


def collect_polygons(spatial_elements):
    u"""Контуры пространств/помещений текущего документа.

    Возвращает (polygons, elem_infos, failed_infos, z_values).
    """
    polygons = []
    elem_infos = []
    failed_infos = []
    z_values = []
    for elem in spatial_elements:
        info = {
            "kind": get_spatial_kind(elem),
            "label": get_spatial_label(elem),
            "id": elem.Id.IntegerValue
        }
        pts = get_boundary_points_xyz(elem)
        if len(pts) < 4:
            info["message"] = u"Не удалось определить границы."
            failed_infos.append(info)
            continue
        polygons.append([(p[0], p[1]) for p in pts])
        z_values.extend([p[2] for p in pts])
        elem_infos.append(info)
    return polygons, elem_infos, failed_infos, z_values


def collect_polygons_from_links(linked_refs):
    u"""Контуры помещений из RVT-связей, трансформированные в хост-координаты.

    Принимает список Reference из PickObjects(ObjectType.LinkedElement).
      ref.ElementId      — RevitLinkInstance в хосте
      ref.LinkedElementId — помещение внутри связанного документа
    """
    polygons = []
    elem_infos = []
    failed_infos = []
    z_values = []
    for ref in linked_refs:
        link_inst = doc.GetElement(ref.ElementId)
        if not isinstance(link_inst, RevitLinkInstance):
            continue
        linked_doc = link_inst.GetLinkDocument()
        if linked_doc is None:
            continue
        linked_room = linked_doc.GetElement(ref.LinkedElementId)
        if linked_room is None:
            continue
        info = {
            "kind": u"Помещение из связи «{}»".format(linked_doc.Title),
            "label": get_spatial_label(linked_room),
            "id": linked_room.Id.IntegerValue
        }
        pts = get_linked_boundary_points_xyz(link_inst, linked_room)
        if len(pts) < 4:
            info["message"] = u"Не удалось определить границы."
            failed_infos.append(info)
            continue
        polygons.append([(p[0], p[1]) for p in pts])
        z_values.extend([p[2] for p in pts])
        elem_infos.append(info)
    return polygons, elem_infos, failed_infos, z_values


def build_centerlines(polygons):
    u"""Осевые отрезки для объединения контуров.

    Точка считается внутри объединения, если она внутри хотя бы одного
    из контуров. Возвращает (segments, total_len, main_len, branch_len, method).
    """
    if not polygons:
        return None, None, None, None, None
    all_points = []
    for poly in polygons:
        all_points.extend(poly)
    if len(all_points) < 4:
        return None, None, None, None, None
    xs = unique_sorted([p[0] for p in all_points])
    ys = unique_sorted([p[1] for p in all_points])
    if len(xs) < 2 or len(ys) < 2:
        return None, None, None, None, None

    def point_inside(x, y):
        for poly in polygons:
            if _ray_cast_inside(poly, x, y):
                return True
        return False

    cells = []
    for ix in range(len(xs) - 1):
        for iy in range(len(ys) - 1):
            x1, x2 = xs[ix], xs[ix + 1]
            y1, y2 = ys[iy], ys[iy + 1]
            if abs(x2 - x1) < TOL or abs(y2 - y1) < TOL:
                continue
            if point_inside((x1 + x2) / 2.0, (y1 + y2) / 2.0):
                cells.append({"ix": ix, "iy": iy,
                               "x1": x1, "x2": x2, "y1": y1, "y2": y2})
    if not cells:
        return None, None, None, None, None

    total_x = xs[-1] - xs[0]
    total_y = ys[-1] - ys[0]
    x_mid = (xs[0] + xs[-1]) / 2.0
    y_mid = (ys[0] + ys[-1]) / 2.0

    def straight_result():
        if total_x >= total_y:
            return [((xs[0], y_mid), (xs[-1], y_mid))], total_x, total_x, 0.0, u"Прямой коридор"
        return [((x_mid, ys[0]), (x_mid, ys[-1]))], total_y, total_y, 0.0, u"Прямой коридор"

    if is_full_rectangle(cells, len(xs) - 1, len(ys) - 1):
        return straight_result()

    def build_runs_h():
        runs = []
        for iy in range(len(ys) - 1):
            row = sorted([c for c in cells if c["iy"] == iy], key=lambda c: c["ix"])
            if not row:
                continue
            rs = re = row[0]
            for cell in row[1:]:
                if cell["ix"] == re["ix"] + 1:
                    re = cell
                else:
                    runs.append({"x1": rs["x1"], "x2": re["x2"],
                                 "y1": rs["y1"], "y2": rs["y2"],
                                 "length": re["x2"] - rs["x1"]})
                    rs = re = cell
            runs.append({"x1": rs["x1"], "x2": re["x2"],
                         "y1": rs["y1"], "y2": rs["y2"],
                         "length": re["x2"] - rs["x1"]})
        return runs

    def build_runs_v():
        runs = []
        for ix in range(len(xs) - 1):
            col = sorted([c for c in cells if c["ix"] == ix], key=lambda c: c["iy"])
            if not col:
                continue
            rs = re = col[0]
            for cell in col[1:]:
                if cell["iy"] == re["iy"] + 1:
                    re = cell
                else:
                    runs.append({"x1": rs["x1"], "x2": rs["x2"],
                                 "y1": rs["y1"], "y2": re["y2"],
                                 "length": re["y2"] - rs["y1"]})
                    rs = re = cell
            runs.append({"x1": rs["x1"], "x2": rs["x2"],
                         "y1": rs["y1"], "y2": re["y2"],
                         "length": re["y2"] - rs["y1"]})
        return runs

    h_runs = build_runs_h()
    v_runs = build_runs_v()

    if not h_runs or not v_runs:
        return straight_result()

    main_h = max(h_runs, key=lambda r: r["length"])
    main_v = max(v_runs, key=lambda r: r["length"])
    h_cy = (main_h["y1"] + main_h["y2"]) / 2.0
    v_cx = (main_v["x1"] + main_v["x2"]) / 2.0

    if main_h["length"] >= main_v["length"]:
        main_seg = ((main_h["x1"], h_cy), (main_h["x2"], h_cy))
        far_y = main_v["y1"] if abs(main_v["y1"] - h_cy) > abs(main_v["y2"] - h_cy) else main_v["y2"]
        branch_len = abs(far_y - h_cy)
        branch_seg = ((v_cx, h_cy), (v_cx, far_y))
        total = main_h["length"] + branch_len
        method = u"Г-образный коридор: горизонтальная ось + вертикальная ветка"
    else:
        main_seg = ((v_cx, main_v["y1"]), (v_cx, main_v["y2"]))
        far_x = main_h["x1"] if abs(main_h["x1"] - v_cx) > abs(main_h["x2"] - v_cx) else main_h["x2"]
        branch_len = abs(far_x - v_cx)
        branch_seg = ((far_x, h_cy), (v_cx, h_cy))
        total = main_v["length"] + branch_len
        method = u"Г-образный коридор: вертикальная ось + горизонтальная ветка"

    return [main_seg, branch_seg], total, max(main_h["length"], main_v["length"]), branch_len, method


def create_detail_lines(view, segments, z):
    created = 0
    for (p1, p2) in segments:
        start = XYZ(p1[0], p1[1], z)
        end = XYZ(p2[0], p2[1], z)
        if start.DistanceTo(end) < TOL:
            continue
        doc.Create.NewDetailCurve(view, Line.CreateBound(start, end))
        created += 1
    return created


def build_report(elem_infos, failed_infos, result, created_total, view_title):
    lines = []
    lines.append(TOOL_TITLE)
    lines.append(u"")
    lines.append(u"Вид: {}".format(view_title))
    lines.append(u"Выбрано элементов: {}".format(len(elem_infos) + len(failed_infos)))
    lines.append(u"Объединено в один коридор: {}".format(len(elem_infos)))
    lines.append(u"")
    lines.append(u"Состав объединённого коридора:")
    if elem_infos:
        for i, info in enumerate(elem_infos, 1):
            lines.append(u"  {}. {} | {} | Id {}".format(
                i, info["kind"], info["label"], info["id"]))
    else:
        lines.append(u"  (нет элементов с распознанными границами)")
    lines.append(u"")
    if result is not None and result["ok"]:
        lines.append(u"Создано линий детализации: {}".format(created_total))
        lines.append(u"Общая длина: {:.2f} м".format(result["length_m"]))
        lines.append(u"  Основной отрезок: {:.2f} м".format(result["main_m"]))
        lines.append(u"  Ветка: {:.2f} м".format(result["branch_m"]))
        lines.append(u"  Метод: {}".format(result["method"]))
    else:
        lines.append(u"Не удалось рассчитать длину — элементы не образуют связную фигуру.")
    if failed_infos:
        lines.append(u"")
        lines.append(u"Не удалось обработать:")
        for i, info in enumerate(failed_infos, 1):
            lines.append(u"  {}. {} | {} | Id {} | {}".format(
                i, info["kind"], info["label"], info["id"], info["message"]))
    lines.append(u"")
    lines.append(
        u"Примечание: все выбранные элементы объединяются в одну фигуру "
        u"и рассматриваются как единый коридор. На виде нарисован «скелет» — "
        u"отрезки, по которым выполнен расчёт (1 — прямой коридор, "
        u"2 — основной + ветка для Г-образного). "
        u"Линии создаются в модели, можно удалить или отменить (Ctrl+Z). "
        u"Расчёт только в плоскости XY."
    )
    return u"\r\n".join(lines)


try:
    active_view = doc.ActiveView

    if active_view is None or active_view.ViewType not in ALLOWED_VIEW_TYPES:
        fail(
            u"Запускайте на плоском виде (план этажа, план потолков и т.п.) — "
            u"на 3D-видах и спецификациях линии детализации создавать нельзя."
        )

    # --- Откуда брать помещения: спрашиваем ОДИН раз, до выбора на виде.
    # Так не нужно "пропускать" лишний шаг: инструмент попросит выбрать
    # ровно те источники, которые отмечены галочками.
    # Галочки открываются в том же положении, что и в прошлый запуск.
    settings = pp_settings.load_settings()
    saved = settings.get("corridor_length_sources") or {}

    answer = pp_paint_dialog.ask({
        u"title": TOOL_TITLE,
        u"subtitle": u"Откуда брать помещения, образующие коридор",
        u"switches": [
            {u"key": u"host",
             u"label": u"Пространства и помещения текущего файла",
             u"hint": u"Пространства ОВ и помещения, созданные в этом файле.",
             u"value": bool(saved.get("host", False))},
            {u"key": u"link",
             u"label": u"Помещения из RVT-связи",
             u"hint": u"Нужно в шаблоне ОВ, где помещения приходят из связи АР.",
             u"value": bool(saved.get("link", True))},
        ],
        u"reset": False,
        u"require_switch": True,
        u"require_switch_text": u"Отметьте, откуда брать помещения.",
        u"ready_label": u"Дальше — выбор помещений на виде",
        u"run_label": u"Выбрать на виде",
    })

    if answer is None:
        raise SystemExit

    use_host = bool(answer[u"switches"].get(u"host", False))
    use_link = bool(answer[u"switches"].get(u"link", False))

    # Запоминаем выбор до выбора на виде: даже если дальше нажать Esc,
    # в следующий раз галочки встанут так, как их оставили.
    try:
        settings["corridor_length_sources"] = {"host": use_host, "link": use_link}
        pp_settings.save_settings(settings)
    except Exception:
        pass

    both = use_host and use_link
    tail = u" · завершить выбор — «Готово» в панели параметров · Esc — отмена"

    refs_host = []
    refs_link = []

    if use_host:
        prompt = u"ПОМЕЩЕНИЯ И ПРОСТРАНСТВА ТЕКУЩЕГО ФАЙЛА"
        if both:
            prompt = u"ШАГ 1 из 2 · " + prompt
        refs_host = uidoc.Selection.PickObjects(
            ObjectType.Element,
            SpaceRoomFilter(),
            prompt + tail
        )

    if use_link:
        prompt = u"ПОМЕЩЕНИЯ ИЗ RVT-СВЯЗИ"
        if both:
            prompt = u"ШАГ 2 из 2 · " + prompt
        refs_link = uidoc.Selection.PickObjects(
            ObjectType.LinkedElement,
            LinkRoomFilter(),
            prompt + tail
        )

    if not refs_host and not refs_link:
        fail(u"Ничего не выбрано.")

    # Собираем полигоны из обоих источников
    all_polygons = []
    all_elem_infos = []
    all_failed_infos = []
    all_z_values = []

    if refs_host:
        host_elems = [doc.GetElement(r.ElementId) for r in refs_host
                      if doc.GetElement(r.ElementId) is not None]
        polys, infos, fails, zs = collect_polygons(host_elems)
        all_polygons.extend(polys)
        all_elem_infos.extend(infos)
        all_failed_infos.extend(fails)
        all_z_values.extend(zs)

    if refs_link:
        polys, infos, fails, zs = collect_polygons_from_links(refs_link)
        all_polygons.extend(polys)
        all_elem_infos.extend(infos)
        all_failed_infos.extend(fails)
        all_z_values.extend(zs)

    if not all_polygons:
        fail(u"Не удалось определить границы ни одного из выбранных элементов.")

    avg_z = sum(all_z_values) / float(len(all_z_values)) if all_z_values else 0.0

    segments, total_len, main_len, branch_len, method = build_centerlines(all_polygons)

    if total_len is None:
        result = {"ok": False}
    else:
        result = {
            "ok": True,
            "segments": segments,
            "length_m": total_len * FT_TO_M,
            "main_m": main_len * FT_TO_M,
            "branch_m": branch_len * FT_TO_M,
            "method": method
        }

    created_total = 0

    if result["ok"]:
        t = Transaction(doc, u"Линии детализации: длина коридора")
        t.Start()
        try:
            created_total = create_detail_lines(active_view, result["segments"], avg_z)
            t.Commit()
        except Exception:
            t.RollBack()
            raise

    pp_wpf.show_report(
        build_report(all_elem_infos, all_failed_infos, result, created_total, active_view.Name),
        title=u"Готово",
        subtitle=TOOL_TITLE,
        monospace=True,
        width=820
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
        subtitle=TOOL_TITLE,
        is_error=True
    )

except Exception as ex:
    rollback()
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )
