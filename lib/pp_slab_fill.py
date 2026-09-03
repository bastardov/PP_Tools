# -*- coding: utf-8 -*-
u"""Заполнение вырезов под колонны в контуре плиты (полы и кровли).

Ядро кнопки «Заполнить плиту» панели «Теплопотери». Про WPF не знает: окно
получает от скрипта готовые строки разбора.

    items, rejects = plan(doc, elements, options)
    apply_item(doc, item)                # правка эскиза одной плиты

`options` — словарь:
    by_column   bool    искать колонны (в модели и в RVT-связях)
    by_size     bool    заполнять вырезы мельче max_mm
    max_mm      float   порог размера выреза, мм
    fill_holes  bool    заполнять отверстия внутри плиты

Термины:
    вырез     — вогнутая выемка ВНЕШНЕГО контура: обход колонны сбоку;
    отверстие — внутренний контур, целиком лежащий внутри плиты.

Правка идёт через SketchEditScope: id плиты, тип, параметры и зависимости
сохраняются. Кривые контура, не попавшие в вырез, не трогаются — у кровли
на них остаются уклон и смещения.
"""

import math

from Autodesk.Revit.DB import (
    BoundingBoxIntersectsFilter,
    BuiltInCategory,
    CurveElement,
    ElementClassFilter,
    ElementId,
    ElementMulticategoryFilter,
    FailureProcessingResult,
    FilteredElementCollector,
    IFailuresPreprocessor,
    Line,
    Outline,
    RevitLinkInstance,
    Sketch,
    Transaction,
    XYZ
)

from System.Collections.Generic import List

try:
    from Autodesk.Revit.DB import SketchEditScope
    SKETCH_EDIT_AVAILABLE = True
except ImportError:
    SketchEditScope = None
    SKETCH_EDIT_AVAILABLE = False


# ─────────────────────────────────────────────
# ЕДИНИЦЫ И ДОПУСКИ
# ─────────────────────────────────────────────

FEET_TO_MM = 304.8
MM_TO_FEET = 1.0 / FEET_TO_MM

POINT_TOL = 0.5 * MM_TO_FEET        # склейка концов кривых, 0.5 мм
TURN_EPS = 0.01                     # синус поворота: меньше — считаем прямой
MIN_POCKET_MM2 = 400.0              # мельче — шум контура, не вырез
MAX_RUN = 6                         # сколько кривых может занимать вырез
COLUMN_MARGIN_MM = 250.0            # запас к габариту колонны
COLUMN_Z_TOL_MM = 600.0             # запас по высоте при поиске колонн
SEARCH_MARGIN_FT = 3.0              # ~1 м вокруг плит при поиске колонн


class PlanError(Exception):
    u"""Плиту обработать нельзя — причина в тексте."""
    pass


# ─────────────────────────────────────────────
# ПЛОСКАЯ ГЕОМЕТРИЯ (про Revit не знает)
# ─────────────────────────────────────────────

def signed_area(points):
    u"""Площадь замкнутого контура со знаком: >0 — обход против часовой."""
    total = 0.0
    count = len(points)

    for index in range(count):
        first = points[index]
        second = points[(index + 1) % count]
        total += first.X * second.Y - second.X * first.Y

    return total / 2.0


def point_in_polygon(point, polygon):
    u"""Луч вправо: нечётное число пересечений — точка внутри."""
    inside = False
    count = len(polygon)
    previous = count - 1

    for current in range(count):
        first = polygon[current]
        second = polygon[previous]

        if (first.Y > point.Y) != (second.Y > point.Y):
            edge_x = first.X + (point.Y - first.Y) * (second.X - first.X) / (second.Y - first.Y)

            if point.X < edge_x:
                inside = not inside

        previous = current

    return inside


def _orient(first, second, third):
    return ((second.X - first.X) * (third.Y - first.Y) -
            (second.Y - first.Y) * (third.X - first.X))


def segments_cross(a1, a2, b1, b2):
    u"""Настоящее пересечение отрезков; касание концами не считается."""
    eps = 1.0e-9

    d1 = _orient(a1, a2, b1)
    d2 = _orient(a1, a2, b2)
    d3 = _orient(b1, b2, a1)
    d4 = _orient(b1, b2, a2)

    first_side = (d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)
    second_side = (d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)

    return first_side and second_side


def turn_sine(previous_point, point, next_point):
    u"""Синус поворота контура в вершине: >0 — влево (выпуклая при обходе CCW)."""
    ax = point.X - previous_point.X
    ay = point.Y - previous_point.Y
    bx = next_point.X - point.X
    by = next_point.Y - point.Y

    first_length = math.sqrt(ax * ax + ay * ay)
    second_length = math.sqrt(bx * bx + by * by)

    if first_length < 1.0e-9 or second_length < 1.0e-9:
        return 0.0

    return (ax * by - ay * bx) / (first_length * second_length)


def rect_of(points):
    u"""Габаритный прямоугольник в плане: (min_x, min_y, max_x, max_y)."""
    xs = [point.X for point in points]
    ys = [point.Y for point in points]

    return (min(xs), min(ys), max(xs), max(ys))


def rect_overlap(first, second):
    dx = min(first[2], second[2]) - max(first[0], second[0])
    dy = min(first[3], second[3]) - max(first[1], second[1])

    if dx <= 0.0 or dy <= 0.0:
        return 0.0

    return dx * dy


def extent_mm(points, start, end):
    u"""Габарит набора точек в осях хорды: (вдоль хорды, поперёк), мм."""
    ux = end.X - start.X
    uy = end.Y - start.Y
    length = math.sqrt(ux * ux + uy * uy)

    if length < 1.0e-9:
        return 0.0, 0.0

    ux = ux / length
    uy = uy / length

    along = []
    across = []

    for point in points:
        dx = point.X - start.X
        dy = point.Y - start.Y
        along.append(dx * ux + dy * uy)
        across.append(-dx * uy + dy * ux)

    return ((max(along) - min(along)) * FEET_TO_MM,
            (max(across) - min(across)) * FEET_TO_MM)


def pocket_size_mm(points, start, end):
    u"""Габарит выреза: меньшее из измерений по хорде и по осям плана, мм.

    Вырез в углу закрывается диагональю, и по осям хорды он всегда выходит
    крупнее, чем есть на самом деле. Поэтому берём то измерение, которое
    описывает вырез теснее.
    """
    along, across = extent_mm(points, start, end)

    rect = rect_of(points)
    width = (rect[2] - rect[0]) * FEET_TO_MM
    depth = (rect[3] - rect[1]) * FEET_TO_MM

    if max(width, depth) < max(along, across):
        return width, depth

    return along, across


def lines_cross_point(first_point, first_dir, second_point, second_dir):
    u"""Точка пересечения двух прямых в плане; None — почти параллельны."""
    denominator = first_dir.X * second_dir.Y - first_dir.Y * second_dir.X

    if abs(denominator) < 1.0e-9:
        return None

    dx = second_point.X - first_point.X
    dy = second_point.Y - first_point.Y
    step = (dx * second_dir.Y - dy * second_dir.X) / denominator

    return XYZ(first_point.X + first_dir.X * step,
               first_point.Y + first_dir.Y * step,
               first_point.Z)


def _direction(curve):
    start = curve.GetEndPoint(0)
    end = curve.GetEndPoint(1)
    dx = end.X - start.X
    dy = end.Y - start.Y
    length = math.sqrt(dx * dx + dy * dy)

    if length < 1.0e-9:
        return None

    return XYZ(dx / length, dy / length, 0.0)


# ─────────────────────────────────────────────
# СЛОВА
# ─────────────────────────────────────────────

def _plural(count, one, few, many):
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return one

    if count % 10 in (2, 3, 4) and not (11 <= count % 100 <= 14):
        return few

    return many


def plural_pockets(count):
    return _plural(count, u"вырез", u"выреза", u"вырезов")


def plural_slabs(count):
    return _plural(count, u"плита", u"плиты", u"плит")


def plural_slabs_in(count):
    return _plural(count, u"плите", u"плитах", u"плитах")


# ─────────────────────────────────────────────
# КОЛОННЫ
# ─────────────────────────────────────────────

class ColumnRef(object):
    u"""Колонна как прямоугольник в плане плюс диапазон отметок."""

    def __init__(self, name, rect, min_z, max_z):
        self.name = name
        self.rect = rect
        self.min_z = min_z
        self.max_z = max_z

    @property
    def width(self):
        return (self.rect[2] - self.rect[0]) * FEET_TO_MM

    @property
    def depth(self):
        return (self.rect[3] - self.rect[1]) * FEET_TO_MM

    def covers_z(self, min_z, max_z):
        tolerance = COLUMN_Z_TOL_MM * MM_TO_FEET

        if self.max_z + tolerance < min_z:
            return False

        if self.min_z - tolerance > max_z:
            return False

        return True


def _bbox_corners(bbox):
    transform = None

    try:
        transform = bbox.Transform
    except Exception:
        transform = None

    points = []

    for x in (bbox.Min.X, bbox.Max.X):
        for y in (bbox.Min.Y, bbox.Max.Y):
            for z in (bbox.Min.Z, bbox.Max.Z):
                point = XYZ(x, y, z)

                if transform is not None:
                    point = transform.OfPoint(point)

                points.append(point)

    return points


def _outline_of(elements, margin):
    u"""Общий габарит выбранных плит с запасом — рамка поиска колонн."""
    points = []

    for element in elements:
        bbox = element.get_BoundingBox(None)

        if bbox is None:
            continue

        points.extend(_bbox_corners(bbox))

    if not points:
        return None

    xs = [point.X for point in points]
    ys = [point.Y for point in points]
    zs = [point.Z for point in points]

    return Outline(
        XYZ(min(xs) - margin, min(ys) - margin, min(zs) - margin),
        XYZ(max(xs) + margin, max(ys) + margin, max(zs) + margin)
    )


def _outline_by_transform(outline, transform):
    points = []

    for x in (outline.MinimumPoint.X, outline.MaximumPoint.X):
        for y in (outline.MinimumPoint.Y, outline.MaximumPoint.Y):
            for z in (outline.MinimumPoint.Z, outline.MaximumPoint.Z):
                points.append(transform.OfPoint(XYZ(x, y, z)))

    xs = [point.X for point in points]
    ys = [point.Y for point in points]
    zs = [point.Z for point in points]

    return Outline(XYZ(min(xs), min(ys), min(zs)),
                   XYZ(max(xs), max(ys), max(zs)))


def _column_name(source_doc, element):
    try:
        element_type = source_doc.GetElement(element.GetTypeId())

        if element_type is not None:
            return unicode(element_type.Name)
    except Exception:
        pass

    try:
        return unicode(element.Name)
    except Exception:
        return u"колонна"


def _columns_from(source_doc, outline, transform):
    categories = List[BuiltInCategory]()
    categories.Add(BuiltInCategory.OST_Columns)
    categories.Add(BuiltInCategory.OST_StructuralColumns)

    result = []

    collector = (FilteredElementCollector(source_doc)
                 .WherePasses(ElementMulticategoryFilter(categories))
                 .WhereElementIsNotElementType()
                 .WherePasses(BoundingBoxIntersectsFilter(outline)))

    for element in collector:
        try:
            bbox = element.get_BoundingBox(None)
        except Exception:
            bbox = None

        if bbox is None:
            continue

        corners = _bbox_corners(bbox)

        if transform is not None:
            corners = [transform.OfPoint(point) for point in corners]

        zs = [point.Z for point in corners]

        result.append(ColumnRef(
            _column_name(source_doc, element),
            rect_of(corners),
            min(zs),
            max(zs)
        ))

    return result


def collect_columns(doc, elements):
    u"""Колонны модели и RVT-связей вокруг выбранных плит."""
    outline = _outline_of(elements, SEARCH_MARGIN_FT)

    if outline is None:
        return []

    columns = []

    try:
        columns.extend(_columns_from(doc, outline, None))
    except Exception:
        pass

    for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        try:
            link_doc = link.GetLinkDocument()

            if link_doc is None:
                continue

            transform = link.GetTotalTransform()
            columns.extend(_columns_from(
                link_doc,
                _outline_by_transform(outline, transform.Inverse),
                transform
            ))
        except Exception:
            continue

    return columns


def column_at(points, columns, min_z, max_z):
    u"""Колонна, которая занимает вырез: наибольшее перекрытие в плане."""
    if not columns:
        return None

    rect = rect_of(points)
    area = (rect[2] - rect[0]) * (rect[3] - rect[1])

    if area <= 0.0:
        return None

    best = None
    best_overlap = 0.0

    for column in columns:
        if not column.covers_z(min_z, max_z):
            continue

        overlap = rect_overlap(rect, column.rect)

        if overlap < area * 0.3:
            continue

        if overlap > best_overlap:
            best = column
            best_overlap = overlap

    return best


# ─────────────────────────────────────────────
# КОНТУР ЭСКИЗА
# ─────────────────────────────────────────────

def find_sketch(doc, element):
    u"""Эскиз контура плиты: сначала свойство SketchId, потом зависимые элементы."""
    try:
        sketch_id = element.SketchId
    except Exception:
        sketch_id = None

    if sketch_id is not None and sketch_id != ElementId.InvalidElementId:
        sketch = doc.GetElement(sketch_id)

        if isinstance(sketch, Sketch):
            return sketch

    try:
        found = element.GetDependentElements(ElementClassFilter(Sketch))
    except Exception:
        found = None

    if found:
        for element_id in found:
            sketch = doc.GetElement(element_id)

            if isinstance(sketch, Sketch):
                return sketch

    return None


def _sketch_curves(doc, sketch):
    element_ids = []

    try:
        element_ids = list(sketch.GetAllElements())
    except Exception:
        element_ids = []

    if not element_ids:
        try:
            element_ids = list(sketch.GetDependentElements(ElementClassFilter(CurveElement)))
        except Exception:
            element_ids = []

    result = []

    for element_id in element_ids:
        element = doc.GetElement(element_id)

        if not isinstance(element, CurveElement):
            continue

        curve = element.GeometryCurve

        if curve is None:
            continue

        result.append({u"id": element.Id, u"curve": curve})

    return result


def _chain_loops(entries):
    u"""Разложить кривые эскиза на замкнутые контуры по совпадению концов."""
    rest = list(entries)
    loops = []

    while rest:
        first = rest.pop(0)
        chain = [{u"id": first[u"id"], u"curve": first[u"curve"]}]
        start = first[u"curve"].GetEndPoint(0)
        end = first[u"curve"].GetEndPoint(1)
        closed = end.DistanceTo(start) <= POINT_TOL

        while not closed:
            found = None

            for position, item in enumerate(rest):
                curve = item[u"curve"]

                if curve.GetEndPoint(0).DistanceTo(end) <= POINT_TOL:
                    found = (position, curve)
                    break

                if curve.GetEndPoint(1).DistanceTo(end) <= POINT_TOL:
                    found = (position, curve.CreateReversed())
                    break

            if found is None:
                break

            item = rest.pop(found[0])
            chain.append({u"id": item[u"id"], u"curve": found[1]})
            end = found[1].GetEndPoint(1)
            closed = end.DistanceTo(start) <= POINT_TOL

        loops.append({u"curves": chain, u"closed": closed})

    return loops


class Loop(object):
    u"""Замкнутый контур эскиза: кривые в порядке обхода против часовой стрелки."""

    def __init__(self, curves):
        self.curves = curves
        self._rebuild()

        if signed_area(self.polygon) < 0.0:
            self._flip()

        self._rotate_to_extreme()

    # -- построение --------------------------------------------------
    def _rebuild(self):
        self.points = [item[u"curve"].GetEndPoint(0) for item in self.curves]
        self.polygon = self._tessellate()

    def _tessellate(self):
        points = []

        for item in self.curves:
            parts = list(item[u"curve"].Tessellate())

            if len(parts) < 2:
                continue

            for point in parts[:-1]:
                points.append(point)

        if not points:
            points = list(self.points)

        return points

    def _flip(self):
        flipped = []

        for item in reversed(self.curves):
            flipped.append({
                u"id": item[u"id"],
                u"curve": item[u"curve"].CreateReversed()
            })

        self.curves = flipped
        self._rebuild()

    def _rotate_to_extreme(self):
        u"""Начать обход с крайней вершины — она заведомо не внутри выреза."""
        if not self.points:
            return

        best = 0

        for index in range(1, len(self.points)):
            point = self.points[index]
            current = self.points[best]

            if point.X < current.X - POINT_TOL:
                best = index
            elif abs(point.X - current.X) <= POINT_TOL and point.Y < current.Y:
                best = index

        if best:
            self.curves = self.curves[best:] + self.curves[:best]
            self._rebuild()

    # -- доступ ------------------------------------------------------
    @property
    def count(self):
        return len(self.curves)

    @property
    def area(self):
        return signed_area(self.polygon)

    def curve_at(self, index):
        return self.curves[index % self.count]

    def point_at(self, index):
        return self.points[index % self.count]

    def turn_at(self, index):
        count = self.count

        return turn_sine(
            self.points[(index - 1) % count],
            self.points[index % count],
            self.points[(index + 1) % count]
        )


# ─────────────────────────────────────────────
# ПОИСК ВЫРЕЗОВ
# ─────────────────────────────────────────────

class Pocket(object):
    u"""Найденный вырез внешнего контура."""

    def __init__(self, loop, start, count, points, width_mm, depth_mm, column, limit_mm):
        self.loop = loop
        self.start = start
        self.count = count
        self.points = points
        self.width_mm = width_mm
        self.depth_mm = depth_mm
        self.column = column
        self.limit_mm = limit_mm

    def describe(self):
        if self.column is not None:
            reason = u"колонна «{}»".format(self.column.name)
        else:
            reason = u"мельче порога"

        return u"вырез {:.0f}×{:.0f} мм — {}".format(
            self.width_mm, self.depth_mm, reason
        )


class Hole(object):
    u"""Отверстие внутри плиты — отдельный внутренний контур."""

    def __init__(self, loop, width_mm, depth_mm, column):
        self.loop = loop
        self.width_mm = width_mm
        self.depth_mm = depth_mm
        self.column = column

    def describe(self):
        if self.column is not None:
            reason = u"колонна «{}»".format(self.column.name)
        else:
            reason = u"мельче порога"

        return u"отверстие {:.0f}×{:.0f} мм — {}".format(
            self.width_mm, self.depth_mm, reason
        )


def _run_is_straight(loop, start, length):
    for step in range(length):
        if not isinstance(loop.curve_at(start + step)[u"curve"], Line):
            return False

    return True


def _chord_crosses(loop, start, length, start_point, end_point):
    count = loop.count

    for index in range(count):
        if start <= index < start + length:
            continue

        first = loop.point_at(index)
        second = loop.point_at(index + 1)

        if segments_cross(start_point, end_point, first, second):
            return True

    return False


def find_pockets(loop, options, columns, min_z, max_z):
    u"""Вырезы внешнего контура: по колоннам и по размеру."""
    result = []
    count = loop.count

    if count < 4:
        return result

    max_mm = float(options.get(u"max_mm") or 0.0)
    by_size = bool(options.get(u"by_size")) and max_mm > 0.0
    by_column = bool(options.get(u"by_column")) and bool(columns)

    if not by_size and not by_column:
        return result

    min_area = MIN_POCKET_MM2 / (FEET_TO_MM * FEET_TO_MM)

    index = 0

    while index < count:
        chosen = None

        for length in range(2, MAX_RUN + 1):
            if index + length > count:
                break

            if not _run_is_straight(loop, index, length):
                break

            if loop.turn_at(index) < -TURN_EPS:
                break

            if loop.turn_at(index + length) < -TURN_EPS:
                continue

            inner = [loop.turn_at(index + step) for step in range(1, length)]

            if not [value for value in inner if value < -TURN_EPS]:
                continue

            start_point = loop.point_at(index)
            end_point = loop.point_at(index + length)

            points = [loop.point_at(index + step) for step in range(length)]
            points.append(end_point)

            if signed_area(points) > -min_area:
                continue

            if _chord_crosses(loop, index, length, start_point, end_point):
                continue

            width_mm, depth_mm = pocket_size_mm(points, start_point, end_point)

            if depth_mm < 1.0:
                continue

            column = None

            if by_column:
                column = column_at(points, columns, min_z, max_z)

            limit_mm = 0.0

            if column is not None:
                if width_mm <= column.width + COLUMN_MARGIN_MM and \
                        depth_mm <= column.depth + COLUMN_MARGIN_MM:
                    limit_mm = max(column.width, column.depth) + COLUMN_MARGIN_MM
                else:
                    column = None

            if limit_mm <= 0.0 and by_size:
                if width_mm <= max_mm and depth_mm <= max_mm:
                    limit_mm = max_mm

            if limit_mm <= 0.0:
                continue

            chosen = Pocket(loop, index, length, points,
                            width_mm, depth_mm, column, limit_mm)
            break

        if chosen is None:
            index += 1
        else:
            result.append(chosen)
            index += chosen.count

    return result


def check_hole(loop, options, columns, min_z, max_z):
    u"""Годится ли отверстие под заполнение; None — оставляем как есть."""
    max_mm = float(options.get(u"max_mm") or 0.0)
    by_size = bool(options.get(u"by_size")) and max_mm > 0.0
    by_column = bool(options.get(u"by_column")) and bool(columns)

    rect = rect_of(loop.polygon)
    width_mm = (rect[2] - rect[0]) * FEET_TO_MM
    depth_mm = (rect[3] - rect[1]) * FEET_TO_MM

    column = None

    if by_column:
        column = column_at(loop.polygon, columns, min_z, max_z)

    if column is not None:
        if width_mm <= column.width + COLUMN_MARGIN_MM and \
                depth_mm <= column.depth + COLUMN_MARGIN_MM:
            return Hole(loop, width_mm, depth_mm, column)

    if by_size and width_mm <= max_mm and depth_mm <= max_mm:
        return Hole(loop, width_mm, depth_mm, None)

    return None


# ─────────────────────────────────────────────
# ПЛАН ПРАВКИ КОНТУРА
# ─────────────────────────────────────────────

def _safe_line(first, second):
    if first.DistanceTo(second) < 1.5 * MM_TO_FEET:
        return None

    try:
        return Line.CreateBound(first, second)
    except Exception:
        return None


def _corner_fits(pocket, corner, start_point, end_point, previous_dir, next_dir):
    u"""Годится ли доведение соседних рёбер до их общего угла."""
    tolerance = 1.0 * MM_TO_FEET

    forward = ((corner.X - start_point.X) * previous_dir.X +
               (corner.Y - start_point.Y) * previous_dir.Y)

    if forward < tolerance:
        return False

    backward = ((end_point.X - corner.X) * next_dir.X +
                (end_point.Y - corner.Y) * next_dir.Y)

    if backward < tolerance:
        return False

    points = list(pocket.points)
    points.append(corner)
    width_mm, depth_mm = pocket_size_mm(points, start_point, end_point)

    return width_mm <= pocket.limit_mm and depth_mm <= pocket.limit_mm


def build_edits(loop, pockets):
    u"""Что именно сделать с кривыми контура ради найденных вырезов.

    Ребро рядом с вырезом продлевается (и сохраняет свои параметры), а новая
    линия создаётся только там, где продлить нельзя.
    """
    delete_ids = []
    modify = []
    new_curves = []
    applied = []

    count = loop.count
    locked = set()

    for pocket in pockets:
        for step in range(pocket.count):
            locked.add((pocket.start + step) % count)

    touched = set()

    for pocket in pockets:
        start_point = loop.point_at(pocket.start)
        end_point = loop.point_at(pocket.start + pocket.count)

        previous_index = (pocket.start - 1) % count
        next_index = (pocket.start + pocket.count) % count

        previous_item = loop.curve_at(previous_index)
        next_item = loop.curve_at(next_index)

        free = (previous_index not in locked and next_index not in locked and
                previous_index not in touched and next_index not in touched and
                previous_index != next_index)

        pocket_modify = []
        pocket_new = []
        solved = False

        if free and isinstance(previous_item[u"curve"], Line) \
                and isinstance(next_item[u"curve"], Line):
            previous_curve = previous_item[u"curve"]
            next_curve = next_item[u"curve"]
            previous_dir = _direction(previous_curve)
            next_dir = _direction(next_curve)

            if previous_dir is not None and next_dir is not None:
                cross = previous_dir.X * next_dir.Y - previous_dir.Y * next_dir.X
                dot = previous_dir.X * next_dir.X + previous_dir.Y * next_dir.Y
                previous_start = previous_curve.GetEndPoint(0)

                offset = abs((end_point.X - previous_start.X) * previous_dir.Y -
                             (end_point.Y - previous_start.Y) * previous_dir.X)

                if abs(cross) < TURN_EPS and dot > 0.0 and offset <= 2.0 * MM_TO_FEET:
                    # соседние рёбра на одной прямой — просто продлеваем левое
                    merged = _safe_line(previous_start, end_point)

                    if merged is not None:
                        pocket_modify.append((previous_item[u"id"], merged, previous_index))
                        solved = True

                elif abs(cross) >= TURN_EPS:
                    corner = lines_cross_point(
                        previous_start, previous_dir,
                        next_curve.GetEndPoint(0), next_dir
                    )

                    if corner is not None and _corner_fits(
                            pocket, corner, start_point, end_point,
                            previous_dir, next_dir):
                        first = _safe_line(previous_start, corner)
                        second = _safe_line(corner, next_curve.GetEndPoint(1))

                        if first is not None and second is not None:
                            pocket_modify.append((previous_item[u"id"], first, previous_index))
                            pocket_modify.append((next_item[u"id"], second, next_index))
                            solved = True

        if not solved:
            chord = _safe_line(start_point, end_point)

            if chord is None:
                continue

            pocket_new.append(chord)

        for step in range(pocket.count):
            delete_ids.append(loop.curve_at(pocket.start + step)[u"id"])

        for element_id, curve, index in pocket_modify:
            modify.append((element_id, curve))
            touched.add(index)

        new_curves.extend(pocket_new)
        applied.append(pocket)

    return {
        u"delete": delete_ids,
        u"modify": modify,
        u"new": new_curves,
        u"pockets": applied
    }


def describe(doc, element):
    u"""Строка вида «Перекрытия · Плита 200 · id 1234567»."""
    parts = []

    try:
        if element.Category is not None:
            parts.append(unicode(element.Category.Name))
    except Exception:
        pass

    try:
        element_type = doc.GetElement(element.GetTypeId())

        if element_type is not None:
            parts.append(unicode(element_type.Name))
    except Exception:
        pass

    parts.append(u"id {}".format(element.Id.IntegerValue))

    return u" · ".join(parts)


class SlabPlan(object):
    u"""Разбор одной плиты: что нашли и что будем править."""

    def __init__(self, doc, element, sketch):
        self.element = element
        self.sketch = sketch
        self.title = describe(doc, element)
        self.pockets = []
        self.holes = []
        self.delete_ids = []
        self.modify = []
        self.new_curves = []
        self.notes = []

    @property
    def total(self):
        return len(self.pockets) + len(self.holes)

    def has_edits(self):
        return bool(self.delete_ids or self.modify or self.new_curves)

    def describe(self):
        if not self.total:
            return u"{} · заполнять нечего".format(self.title)

        parts = []

        if self.pockets:
            parts.append(u"{} {}".format(
                len(self.pockets), plural_pockets(len(self.pockets))
            ))

        if self.holes:
            parts.append(u"отверстий: {}".format(len(self.holes)))

        return u"{} · {}".format(self.title, u", ".join(parts))

    def detail_rows(self):
        rows = []

        for pocket in self.pockets:
            rows.append(pocket.describe())

        for hole in self.holes:
            rows.append(hole.describe())

        rows.extend(self.notes)

        return rows


def _z_range(element):
    try:
        bbox = element.get_BoundingBox(None)
    except Exception:
        bbox = None

    if bbox is None:
        return -1.0e9, 1.0e9

    corners = _bbox_corners(bbox)
    zs = [point.Z for point in corners]

    return min(zs), max(zs)


def _split_loops(loops):
    u"""Внешние контуры и отверстия: отверстие лежит внутри другого контура."""
    outers = []
    holes = []

    for loop in loops:
        inside = False

        for other in loops:
            if other is loop:
                continue

            if point_in_polygon(loop.polygon[0], other.polygon):
                inside = True
                break

        if inside:
            holes.append(loop)
        else:
            outers.append(loop)

    return outers, holes


def _pocket_holds_hole(pocket, holes):
    for loop in holes:
        if point_in_polygon(loop.polygon[0], pocket.points):
            return True

    return False


def _plan_one(doc, element, options, columns):
    sketch = find_sketch(doc, element)

    if sketch is None:
        raise PlanError(
            u"не удалось прочитать эскиз контура — так бывает у кровли "
            u"выдавливанием и у элементов, построенных не по эскизу"
        )

    entries = _sketch_curves(doc, sketch)

    if not entries:
        raise PlanError(u"в эскизе не нашлось линий контура")

    loops = []

    for raw in _chain_loops(entries):
        if not raw[u"closed"]:
            raise PlanError(u"контур эскиза не замкнут — поправьте его вручную")

        loops.append(Loop(raw[u"curves"]))

    item = SlabPlan(doc, element, sketch)
    outers, holes = _split_loops(loops)
    min_z, max_z = _z_range(element)

    for loop in outers:
        found = find_pockets(loop, options, columns, min_z, max_z)
        found = [pocket for pocket in found if not _pocket_holds_hole(pocket, holes)]
        edits = build_edits(loop, found)

        item.pockets.extend(edits[u"pockets"])
        item.delete_ids.extend(edits[u"delete"])
        item.modify.extend(edits[u"modify"])
        item.new_curves.extend(edits[u"new"])

    if options.get(u"fill_holes"):
        for loop in holes:
            hole = check_hole(loop, options, columns, min_z, max_z)

            if hole is None:
                continue

            item.holes.append(hole)

            for entry in loop.curves:
                item.delete_ids.append(entry[u"id"])

    return item


def plan(doc, elements, options, columns=None):
    u"""Разбор выбранных плит. Возврат: (список SlabPlan, список отказов).

    `columns` можно передать готовым списком: окно пересчитывает разбор на
    каждую галочку, а колонны за это время не меняются.
    """
    if columns is None:
        columns = []

        if options.get(u"by_column"):
            try:
                columns = collect_columns(doc, elements)
            except Exception:
                columns = []

    items = []
    rejects = []

    for element in elements:
        try:
            items.append(_plan_one(doc, element, options, columns))
        except PlanError as error:
            rejects.append({
                u"title": describe(doc, element),
                u"reason": unicode(error)
            })
        except Exception as error:
            rejects.append({
                u"title": describe(doc, element),
                u"reason": unicode(error)
            })

    return items, rejects


# ─────────────────────────────────────────────
# ПРАВКА ЭСКИЗА
# ─────────────────────────────────────────────

class _WarningSwallower(IFailuresPreprocessor):
    u"""Гасит предупреждения Revit, чтобы не всплывали модальные окна."""

    def PreprocessFailures(self, accessor):
        try:
            accessor.DeleteAllWarnings()
        except Exception:
            pass

        return FailureProcessingResult.Continue


def apply_item(doc, item):
    u"""Заполнить вырезы одной плиты. Открытой транзакции быть не должно."""
    if not item.has_edits():
        return False

    if not SKETCH_EDIT_AVAILABLE:
        raise PlanError(
            u"этой версии Revit недоступна правка эскиза (нет SketchEditScope)"
        )

    scope = SketchEditScope(doc, u"PP: заполнить плиту")
    scope.Start(item.sketch.Id)

    transaction = Transaction(doc, u"PP: контур плиты")
    transaction.Start()

    try:
        seen = set()

        for element_id in item.delete_ids:
            key = element_id.IntegerValue

            if key in seen:
                continue

            seen.add(key)

            if doc.GetElement(element_id) is None:
                continue

            doc.Delete(element_id)

        for element_id, curve in item.modify:
            model_curve = doc.GetElement(element_id)

            if model_curve is None:
                continue

            model_curve.GeometryCurve = curve

        plane = item.sketch.SketchPlane

        for curve in item.new_curves:
            doc.Create.NewModelCurve(curve, plane)

        transaction.Commit()
    except Exception:
        try:
            if transaction.HasStarted():
                transaction.RollBack()
        except Exception:
            pass

        try:
            scope.Cancel()
        except Exception:
            pass

        raise

    scope.Commit(_WarningSwallower())

    return True
