# -*- coding: utf-8 -*-
u"""PP_Tools — объединение кусков одной стены в одну стену.

Задача: архитектор рисует одну стену несколькими кусками (простенок, перемычка
над проёмом, сегменты в ряд). В расчёте теплопотерь каждый кусок даёт отдельную
позицию. Модуль собирает куски, лежащие в одной плоскости и соприкасающиеся
между собой, в один замкнутый контур и создаёт по нему одну стену.

Про WPF модуль не знает: окно получает готовые описания наборов и отказов.

    import pp_wall_merge

    sets, rejects = pp_wall_merge.plan(doc, walls)

    for mset in sets:
        mset.describe()            # u"3 стены · Стена АР_380 · 12,4 м²"
        new_wall = pp_wall_merge.merge(doc, mset, wall_type)   # внутри транзакции

`rejects` — список `Reject` с полями `title` и `reason`: почему кусок или набор
объединить нельзя.

Система координат набора: u — вдоль оси стены в плане от точки `origin`,
v — абсолютная отметка Z. Оба значения в футах Revit.
"""

import math

from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List


FT_MM = 304.8                 # миллиметров в футе
TOL = 1.0 / FT_MM             # 1 мм в футах — общий допуск склейки
FT2_M2 = 0.09290304           # фут² -> м²
FT_M = 0.3048                 # фут -> м
V_TOL = 5.0 / 304.8           # 5 мм — расхождение по высоте, которое терпим
V_FAIL = 50.0 / 304.8         # 50 мм — дальше набор считаем непостроенным


# ======================================================================
#  Мелкие утилиты
# ======================================================================

def plural_walls(count):
    u"""1 стена / 2 стены / 5 стен."""
    count = abs(int(count))

    if count % 10 == 1 and count % 100 != 11:
        return u"стена"

    if count % 10 in (2, 3, 4) and not (11 <= count % 100 <= 14):
        return u"стены"

    return u"стен"


def _num(value):
    u"""12.4 -> «12,4» — русская запятая без хвостов."""
    text = u"{:.2f}".format(value)
    return text.replace(u".", u",")


def _param_id(element, bip):
    try:
        p = element.get_Parameter(bip)
        if p is not None and p.HasValue:
            return p.AsElementId()
    except Exception:
        pass

    return None


def element_name(element):
    u"""Имя элемента: сначала параметр, `.Name` — в последнюю очередь.

    Прямое `element.Name` на системных типах Revit (`WallType`, `PipeType`
    и подобные) бросает `AttributeError: Name`. Порядок чтения — как принято
    в плагине: встроенные параметры имени типа, затем дескриптор базового
    `Element`, и только потом само свойство.
    """
    if element is None:
        return u""

    for name in (u"ALL_MODEL_TYPE_NAME", u"SYMBOL_NAME_PARAM"):
        try:
            param = element.get_Parameter(getattr(BuiltInParameter, name))

            if param is not None and param.HasValue:
                value = param.AsString()

                if value:
                    return value
        except Exception:
            pass

    for reader in (u"GetValue", u"__get__"):
        try:
            func = getattr(Element.Name, reader)
        except Exception:
            continue

        for args in ((element,), (element, type(element))):
            try:
                value = func(*args)

                if value:
                    return value
            except Exception:
                pass

    try:
        return element.Name
    except Exception:
        return u""


def wall_title(wall):
    u"""«Стена АР_380 · id 1234567» — как кусок называется в отчёте."""
    name = u"Стена"

    try:
        name = element_name(wall.WallType) or name
    except Exception:
        pass

    try:
        return u"{} · id {}".format(name, wall.Id.IntegerValue)
    except Exception:
        return name


def type_title(wall_type):
    return element_name(wall_type) or u"Тип стены"


# ======================================================================
#  Данные
# ======================================================================

class Piece(object):
    u"""Кусок стены: контуры его грани в системе координат набора.

    `faces` — список граней. Каждая грань это список контуров: первый внешний,
    остальные, если есть, — вырезы. Так кусок описывается честно, даже когда
    Revit разбил грань стены на несколько частей или в ней есть отверстие.
    """

    def __init__(self, wall, base_level, faces, patches=None):
        self.wall = wall
        self.id = wall.Id
        self.base_level = base_level
        self.patch_count = len(patches or [])
        self.patch_rects = []

        for loops in (patches or []):
            for loop in loops:
                rect_us = [u for u, _v in loop]
                rect_vs = [v for _u, v in loop]
                self.patch_rects.append(
                    (min(rect_us), max(rect_us), min(rect_vs), max(rect_vs))
                )
        self.faces = faces + list(patches or [])

        us = []
        vs = []
        area = 0.0

        for loops in self.faces:
            signed = 0.0

            for loop in loops:
                for u, v in loop:
                    us.append(u)
                    vs.append(v)

                signed += _signed_area(loop)

            if loops in faces:
                area += abs(signed)

        self.u0 = min(us)
        self.u1 = max(us)
        self.v0 = min(vs)
        self.v1 = max(vs)
        self.area = area

    @property
    def type_id(self):
        try:
            return self.wall.WallType.Id
        except Exception:
            return None

    def contains(self, x, y):
        u"""Точка внутри куска: чётность попаданий в контуры одной грани."""
        for loops in self.faces:
            inside = False

            for loop in loops:
                if _point_in(loop, x, y):
                    inside = not inside

            if inside:
                return True

        return False


class Reject(object):
    u"""Кусок или набор, который объединить нельзя."""

    def __init__(self, title, reason):
        self.title = title
        self.reason = reason

    def describe(self):
        return u"{} — {}".format(self.title, self.reason)


class MergeSet(object):
    u"""Набор кусков, которые станут одной стеной."""

    def __init__(self, pieces, origin, direction, outline, area):
        self.pieces = pieces
        self.origin = origin            # XYZ — начало отсчёта u
        self.direction = direction      # XYZ — единичный вектор оси в плане
        self.outline = outline          # [(u, v), ...] замкнутый контур
        self.area = area                # фут²
        self.master = max(pieces, key=lambda p: p.area)
        self.moved_inserts = 0
        self.embedded_cut = 0
        self.embedded_notes = []

        # Имя типа запоминаем сразу: в отчёте описание набора читается уже
        # после того, как исходные куски удалены.
        self.type_name = type_title(self.master.wall.WallType)

    @property
    def normal(self):
        u"""Единичная нормаль к плоскости стены в плане."""
        return XYZ(-self.direction.Y, self.direction.X, 0.0)

    @property
    def mixed_types(self):
        ids = set()

        for piece in self.pieces:
            tid = piece.type_id

            if tid is not None:
                ids.add(tid.IntegerValue)

        return len(ids) > 1

    @property
    def is_rectangle(self):
        return len(self.outline) == 4

    def point(self, u, v):
        return XYZ(
            self.origin.X + self.direction.X * u,
            self.origin.Y + self.direction.Y * u,
            v
        )

    def piece_rows(self):
        u"""Строки по каждому куску — видно, из чего собрался контур."""
        rows = []

        for piece in sorted(self.pieces, key=lambda p: (p.v0, p.u0)):
            rows.append(u"{} · длина {} м · низ {}, верх {}".format(
                wall_title(piece.wall),
                _num((piece.u1 - piece.u0) * FT_M),
                _num(piece.v0 * FT_M),
                _num(piece.v1 * FT_M)
            ))

        return rows

    def describe(self):
        us = [u for u, _v in self.outline]
        vs = [v for _u, v in self.outline]

        parts = [
            u"{} {}".format(len(self.pieces), plural_walls(len(self.pieces))),
            self.type_name,
            u"{} м²".format(_num(self.area * FT2_M2)),
            u"длина {} м".format(_num((max(us) - min(us)) * FT_M)),
            u"низ {}, верх {}".format(
                _num(min(vs) * FT_M), _num(max(vs) * FT_M)
            )
        ]

        if self.mixed_types:
            parts.append(u"типы разные")

        if not self.is_rectangle:
            parts.append(u"сложный контур")

        return u" · ".join(parts)


# ======================================================================
#  Пригодность куска
# ======================================================================

def _reason_not_mergeable(doc, wall, faces, move_inserts):
    u"""Почему стену нельзя объединять. None — можно."""
    try:
        if wall.CurtainGrid is not None:
            return u"это витраж, а не обычная стена"
    except Exception:
        pass

    try:
        if wall.WallType.Kind != WallKind.Basic:
            return u"не обычная стена (витраж или составная)"
    except Exception:
        pass

    try:
        if wall.IsStackedWallMember:
            return u"часть составной стены"
    except Exception:
        pass

    try:
        if wall.GroupId is not None and wall.GroupId != ElementId.InvalidElementId:
            return u"стена в группе — сначала распустите группу"
    except Exception:
        pass

    try:
        inserts = wall.FindInserts(True, True, True, True)

        if inserts is not None and inserts.Count > 0:
            for insert_id in inserts:
                element = doc.GetElement(insert_id)

                # Встроенная стена (витраж) — самостоятельный элемент: он не
                # удаляется вместе с host-стеной. Оставляем его на месте и
                # врезаем в объединённую стену после создания.
                if isinstance(element, Wall):
                    continue

                if not isinstance(element, FamilyInstance):
                    return (u"переносить умею только окна и двери, а в стене "
                            u"есть {} — её вставить заново нечем".format(
                                _insert_label(element, insert_id)))

                if not move_inserts:
                    return (u"в стене есть проёмы — включите «Переносить проёмы» "
                            u"в окне инструмента")

                if not isinstance(element.Location, LocationPoint):
                    return u"у проёма {} нет точки вставки".format(
                        _insert_label(element, insert_id))
    except Exception:
        pass

    for bip, text in (
        (BuiltInParameter.WALL_TOP_IS_ATTACHED, u"верх стены привязан к другому элементу"),
        (BuiltInParameter.WALL_BOTTOM_IS_ATTACHED, u"низ стены привязан к другому элементу"),
    ):
        try:
            p = wall.get_Parameter(bip)

            if p is not None and p.HasValue and p.AsInteger() == 1:
                return text
        except Exception:
            pass

    # Контур куска должен быть «ступенчатым»: только горизонтали и вертикали.
    # Прямоугольник, Г, П и всё, что делает сам инструмент, сюда попадают;
    # наклонные и скруглённые профили — нет.
    if faces is not None and not _is_rectilinear(faces):
        return u"контур грани не ступенчатый (наклонный или скруглённый профиль)"

    try:
        loc = wall.Location

        if not isinstance(loc, LocationCurve):
            return u"у стены нет линии привязки"

        if not isinstance(loc.Curve, Line):
            return u"стена не прямая (дуга или сплайн)"
    except Exception:
        return u"не удалось прочитать линию стены"

    return None


def _solids(geometry, depth=0):
    u"""Тела из GeometryElement, разворачивая вложенные экземпляры."""
    found = []

    if geometry is None or depth > 2:
        return found

    for item in geometry:
        if isinstance(item, Solid):
            try:
                if item.Faces.Size > 0:
                    found.append(item)
            except Exception:
                pass
        elif isinstance(item, GeometryInstance):
            try:
                found.extend(_solids(item.GetInstanceGeometry(), depth + 1))
            except Exception:
                pass

    return found


def _same_point(a, b):
    return (abs(a.X - b.X) <= TOL and
            abs(a.Y - b.Y) <= TOL and
            abs(a.Z - b.Z) <= TOL)


def _clean_loop(points):
    u"""Убрать повторы и замыкающую точку."""
    while len(points) > 1 and _same_point(points[0], points[-1]):
        points.pop()

    return points if len(points) >= 3 else None


def _loops_by_curves(face):
    u"""Контуры через GetEdgesAsCurveLoops — основной путь."""
    try:
        result = []

        for loop in face.GetEdgesAsCurveLoops():
            points = []

            for curve in loop:
                if not isinstance(curve, Line):
                    return None, u"в контуре грани есть кривая"

                point = curve.GetEndPoint(0)

                if points and _same_point(points[-1], point):
                    continue

                points.append(point)

            cleaned = _clean_loop(points)

            if cleaned:
                result.append(cleaned)

        if result:
            return result, None

        return None, u"GetEdgesAsCurveLoops не дал контуров"
    except Exception as ex:
        return None, u"GetEdgesAsCurveLoops: {}".format(ex)


def _loops_by_edges(face):
    u"""Контуры через EdgeLoops — запасной путь."""
    try:
        result = []

        for loop in face.EdgeLoops:
            points = []

            for edge in loop:
                try:
                    chunk = edge.TessellateOnFace(face)
                except Exception:
                    chunk = edge.Tessellate()

                for point in chunk:
                    if points and _same_point(points[-1], point):
                        continue

                    points.append(point)

            cleaned = _clean_loop(points)

            if cleaned:
                result.append(cleaned)

        if result:
            return result, None

        return None, u"EdgeLoops не дал контуров"
    except Exception as ex:
        return None, u"EdgeLoops: {}".format(ex)


def _face_loops(face):
    u"""Контуры грани. Возврат: (контуры, причина отказа).

    Причина возвращается текстом, а не глотается: на этом уже потеряли два
    круга отладки.
    """
    problem = u"неизвестно"

    for reader in (_loops_by_curves, _loops_by_edges):
        loops, trouble = reader(face)

        if loops:
            return loops, None

        problem = trouble or problem

    return None, problem


def wall_contours(wall):
    u"""Контуры наружной грани стены. Возврат: (грани, причина отказа).

    Грань стены может быть разбита Revit на несколько компланарных кусков
    (примыкания, объединённая геометрия) и может содержать вырезы. Поэтому
    берём не одну самую большую грань, а ВСЕ грани, лежащие в одной плоскости,
    и каждую — со всеми её контурами.
    """
    try:
        options = Options()
        options.ComputeReferences = False
        options.IncludeNonVisibleObjects = False
        options.DetailLevel = ViewDetailLevel.Medium

        facing = wall.Orientation
        groups = {}

        for solid in _solids(wall.get_Geometry(options)):
            for face in solid.Faces:
                if not isinstance(face, PlanarFace):
                    continue

                normal = face.FaceNormal

                if abs(normal.Z) > 0.01:
                    continue

                if normal.DotProduct(facing) < 0.99:
                    continue

                origin = face.Origin
                offset = origin.X * facing.X + origin.Y * facing.Y

                groups.setdefault(round(offset * FT_MM, 0), []).append(face)

        if not groups:
            return None, u"у стены не нашлось вертикальной грани"

        best = None

        for faces in groups.values():
            weight = 0.0

            for face in faces:
                weight += face.Area

            if best is None or weight > best[0]:
                best = (weight, faces)

        result = []

        for face in best[1]:
            loops, problem = _face_loops(face)

            if loops is None:
                return None, u"не удалось разобрать контур грани ({})".format(problem)

            result.append(loops)

        return result, None
    except Exception as ex:
        return None, u"ошибка чтения геометрии: {}".format(ex)


def _box_corners(element):
    u"""Восемь углов габарита элемента в мировых координатах.

    У BoundingBoxXYZ есть собственный Transform, и он не всегда единичный —
    без него углы уезжают, заращивание промахивается мимо проёма, и Revit
    потом ругается «Экземпляры ничего не вырезают».
    """
    try:
        box = element.get_BoundingBox(None)

        if box is None:
            return None

        transform = None

        try:
            transform = box.Transform
        except Exception:
            transform = None

        corners = []

        for x in (box.Min.X, box.Max.X):
            for y in (box.Min.Y, box.Max.Y):
                for z in (box.Min.Z, box.Max.Z):
                    point = XYZ(x, y, z)

                    if transform is not None:
                        try:
                            point = transform.OfPoint(point)
                        except Exception:
                            pass

                    corners.append(point)

        return corners
    except Exception:
        return None


def _insert_label(element, insert_id):
    u"""Назвать вставку, которую нельзя перенести, — чтобы было видно, что мешает."""
    kind = u"вставка"

    try:
        if isinstance(element, Wall):
            kind = u"встроенная стена или витраж"
        elif isinstance(element, Opening):
            kind = u"прямоугольный проём (вырез)"
        elif element.Category is not None:
            kind = element.Category.Name
    except Exception:
        pass

    try:
        return u"{} · id {}".format(kind, insert_id.IntegerValue)
    except Exception:
        return kind


def _insert_boxes(doc, wall, move_inserts):
    u"""Габариты проёмов стены — по ним контур зарастает обратно.

    Контур грани приходит УЖЕ с вырезом под окно или дверь. Если объединять
    как есть, в новой стене на месте проёма не будет материала, и вставленная
    заново дверь ничего не вырежет — Revit ругается «Экземпляры ничего не
    вырезают». Поэтому проёмы заращиваем: они будут врезаны заново.
    """
    boxes = []

    try:
        inserts = wall.FindInserts(True, True, True, True)

        if inserts is None:
            return boxes

        for insert_id in inserts:
            element = doc.GetElement(insert_id)

            if element is None:
                continue

            # Витраж заращиваем всегда: его след всё равно надо закрыть, чтобы
            # контур не распался. Окна и двери — только когда их переносим.
            if not isinstance(element, Wall) and not move_inserts:
                continue

            corners = _box_corners(element)

            if corners:
                boxes.append(corners)
    except Exception:
        pass

    return boxes


def _is_rectilinear(faces):
    u"""Все контуры состоят только из горизонталей и вертикалей."""
    for loops in faces:
        for loop in loops:
            count = len(loop)

            for index in range(count):
                a = loop[index]
                b = loop[(index + 1) % count]

                if abs(a.Z - b.Z) <= TOL:
                    continue

                if abs(a.X - b.X) <= TOL and abs(a.Y - b.Y) <= TOL:
                    continue

                return False

    return True


def _plan_direction(line):
    u"""Единичное направление оси в плане, приведённое к канону."""
    p0 = line.GetEndPoint(0)
    p1 = line.GetEndPoint(1)

    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    length = math.sqrt(dx * dx + dy * dy)

    if length < TOL:
        return None

    dx /= length
    dy /= length

    # Одна и та же плоскость должна давать одно направление независимо от
    # того, в какую сторону архитектор вёл линию.
    if dx < -1e-9 or (abs(dx) <= 1e-9 and dy < 0):
        dx = -dx
        dy = -dy

    return XYZ(dx, dy, 0.0)


# ======================================================================
#  Разбор выделения
# ======================================================================

def plan(doc, walls, move_inserts=False):
    u"""Разложить выделенные стены на наборы. Возврат: (sets, rejects)."""
    rejects = []
    groups = {}

    for wall in walls:
        try:
            key, item, reason = _prepare(wall, doc, move_inserts)
        except Exception as ex:
            rejects.append(Reject(
                wall_title(wall),
                u"не удалось разобрать стену: {}".format(ex)
            ))
            continue

        if reason:
            rejects.append(Reject(wall_title(wall), reason))
            continue

        groups.setdefault(key, []).append(item)

    sets = []

    for key in sorted(groups.keys()):
        try:
            found, failed = _plane_sets(groups[key])
        except Exception as ex:
            rejects.append(Reject(
                u"{} {}".format(len(groups[key]), plural_walls(len(groups[key]))),
                u"не удалось собрать контур: {}".format(ex)
            ))
            continue

        sets.extend(found)
        rejects.extend(failed)

    return sets, rejects


def _prepare(wall, doc, move_inserts):
    u"""Разобрать одну стену. Возврат: (ключ плоскости, данные, причина отказа)."""
    # Контуры берём с РЕАЛЬНОЙ геометрии. Параметрам «низ/верх» верить нельзя:
    # у перемычки над проёмом они говорят «от пола до уровня выше», хотя тело
    # только над окном. Смещения к тому же относительные и у каждого куска свои.
    faces, problem = wall_contours(wall)

    reason = _reason_not_mergeable(doc, wall, faces, move_inserts)

    if reason:
        return None, None, reason

    if faces is None:
        return None, None, problem or u"не удалось прочитать геометрию стены"

    base_id = _param_id(wall, BuiltInParameter.WALL_BASE_CONSTRAINT)
    base_level = doc.GetElement(base_id) if base_id is not None else None

    if not isinstance(base_level, Level):
        return None, None, u"у стены не задан базовый уровень"

    line = wall.Location.Curve
    direction = _plan_direction(line)

    if direction is None:
        return None, None, u"нулевая длина стены"

    p0 = line.GetEndPoint(0)
    offset = p0.X * (-direction.Y) + p0.Y * direction.X

    key = (
        round(direction.X, 4),
        round(direction.Y, 4),
        round(offset * FT_MM, 0)
    )

    boxes = _insert_boxes(doc, wall, move_inserts)

    return key, (wall, line, direction, base_level, faces, boxes), None


def _plane_sets(items):
    u"""Собрать наборы внутри одной плоскости. Возврат: (sets, rejects)."""
    origin = items[0][1].GetEndPoint(0)
    direction = items[0][2]

    def along(point):
        return ((point.X - origin.X) * direction.X +
                (point.Y - origin.Y) * direction.Y)

    # Сначала контуры всех кусков — они задают общие границы плоскости.
    shapes = []
    limits = []

    for item in items:
        faces = item[4]
        flat = []

        for loops in faces:
            flat.append([[(along(point), point.Z) for point in loop]
                         for loop in loops])

        shapes.append(flat)

        for loops in flat:
            for loop in loops:
                limits.extend(loop)

    if not limits:
        return [], [Reject(wall_title(item[0]), u"пустой контур") for item in items]

    plane_u0 = min(u for u, _v in limits)
    plane_u1 = max(u for u, _v in limits)
    plane_v0 = min(v for _u, v in limits)
    plane_v1 = max(v for _u, v in limits)

    pieces = []

    for index, item in enumerate(items):
        wall = item[0]
        base_level = item[3]
        boxes = item[5]

        flat = shapes[index]
        patches = []

        for corners in boxes:
            us = [along(point) for point in corners]
            vs = [point.Z for point in corners]

            # Небольшой запас: габарит проёма и вырез в стене совпадают не
            # идеально, а тонкая недозакрашенная щель ломает всё.
            margin = 20.0 / FT_MM

            # Обрезаем по границам ВСЕЙ плоскости, а не своего куска. Дверь
            # часто числится за куском, который сам до пола не доходит
            # (перемычка над ней) — обрезка по куску схлопывала заращивание
            # в полоску, и вырез оставался.
            u0 = max(min(us) - margin, plane_u0)
            u1 = min(max(us) + margin, plane_u1)
            v0 = max(min(vs) - margin, plane_v0)
            v1 = min(max(vs) + margin, plane_v1)

            if u1 - u0 > TOL and v1 - v0 > TOL:
                patches.append([[(u0, v0), (u1, v0), (u1, v1), (u0, v1)]])

        pieces.append(Piece(wall, base_level, flat, patches))

    return _build_sets(pieces, origin, direction)


def _axis(values):
    u"""Отсортированные координаты сетки, близкие слиты в одну."""
    ordered = sorted(values)
    result = []

    for value in ordered:
        if not result or value - result[-1] > TOL:
            result.append(value)

    return result


def _signed_area(polygon):
    u"""Площадь замкнутого контура со знаком (шнуровка).

    Знак нужен, чтобы вырез внутри грани вычитался, а не складывался.
    """
    total = 0.0
    count = len(polygon)

    for index in range(count):
        x0, y0 = polygon[index]
        x1, y1 = polygon[(index + 1) % count]
        total += x0 * y1 - x1 * y0

    return total / 2.0


def _point_in(polygon, x, y):
    u"""Точка внутри контура (луч вправо, чётность пересечений)."""
    inside = False
    count = len(polygon)
    j = count - 1

    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]

        if (yi > y) != (yj > y):
            cross = xi + (y - yi) * (xj - xi) / (yj - yi)

            if x < cross:
                inside = not inside

        j = i

    return inside


def _titles(pieces):
    return u"{} {}: {}".format(
        len(pieces), plural_walls(len(pieces)),
        u", ".join([wall_title(piece.wall) for piece in pieces])
    )


def _build_sets(pieces, origin, direction):
    u"""Разложить куски плоскости на связные наборы и обвести их контуры.

    Работает по общей сетке из всех координат контуров: клетка закрашена,
    если её середина попала внутрь хотя бы одного куска. Так объединяются не
    только прямоугольники, но и уже объединённые Г- и П-образные стены.
    """
    sets = []
    rejects = []

    raw_us = []
    raw_vs = []

    for piece in pieces:
        for loops in piece.faces:
            for loop in loops:
                for u, v in loop:
                    raw_us.append(u)
                    raw_vs.append(v)

    us = _axis(raw_us)
    vs = _axis(raw_vs)

    nu = len(us) - 1
    nv = len(vs) - 1

    if nu < 1 or nv < 1:
        for piece in pieces:
            rejects.append(Reject(wall_title(piece.wall), u"кусок вырожден в линию"))

        return sets, rejects

    owners = []

    for i in range(nu):
        column = []
        cu = (us[i] + us[i + 1]) / 2.0

        for j in range(nv):
            cv = (vs[j] + vs[j + 1]) / 2.0
            here = []

            for index, piece in enumerate(pieces):
                if (piece.u0 - TOL <= cu <= piece.u1 + TOL and
                        piece.v0 - TOL <= cv <= piece.v1 + TOL and
                        piece.contains(cu, cv)):
                    here.append(index)

            column.append(here)

        owners.append(column)

    seen = set()

    for i0 in range(nu):
        for j0 in range(nv):
            if not owners[i0][j0] or (i0, j0) in seen:
                continue

            cells = []
            stack = [(i0, j0)]
            seen.add((i0, j0))

            while stack:
                i, j = stack.pop()
                cells.append((i, j))

                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ni = i + di
                    nj = j + dj

                    if (0 <= ni < nu and 0 <= nj < nv and owners[ni][nj]
                            and (ni, nj) not in seen):
                        seen.add((ni, nj))
                        stack.append((ni, nj))

            used = set()

            for i, j in cells:
                used.update(owners[i][j])

            component = [pieces[index] for index in sorted(used)]

            if len(component) < 2:
                rejects.append(Reject(
                    wall_title(component[0].wall),
                    u"в выделении не нашлось второго куска в той же "
                    u"плоскости — возможно, он отсеян по причине выше"
                ))
                continue

            cover = [[False] * nv for _ in range(nu)]
            area = 0.0

            for i, j in cells:
                cover[i][j] = True
                area += (us[i + 1] - us[i]) * (vs[j + 1] - vs[j])

            if _has_hole(cover, nu, nv):
                rejects.append(Reject(
                    _titles(component),
                    u"внутри объединения остаётся дырка"
                ))
                continue

            outline, error = _trace(cover, us, vs, nu, nv)

            if outline is None:
                rejects.append(Reject(_titles(component), error))
                continue

            sets.append(MergeSet(component, origin, direction, outline, area))

    return sets, rejects


def _has_hole(cover, nu, nv):
    u"""Пустая клетка, до которой не дойти снаружи, — это дырка."""
    seen = set()
    stack = []

    for i in range(-1, nu + 1):
        for j in (-1, nv):
            stack.append((i, j))

    for j in range(-1, nv + 1):
        for i in (-1, nu):
            stack.append((i, j))

    while stack:
        i, j = stack.pop()

        if (i, j) in seen:
            continue

        if not (-1 <= i <= nu and -1 <= j <= nv):
            continue

        if 0 <= i < nu and 0 <= j < nv and cover[i][j]:
            continue

        seen.add((i, j))

        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            stack.append((i + di, j + dj))

    for i in range(nu):
        for j in range(nv):
            if not cover[i][j] and (i, j) not in seen:
                return True

    return False


def _trace(cover, us, vs, nu, nv):
    u"""Обвести закрашенные клетки одним замкнутым контуром."""
    edges = {}
    added = 0

    for i in range(nu):
        for j in range(nv):
            if not cover[i][j]:
                continue

            sides = []

            if j == 0 or not cover[i][j - 1]:
                sides.append(((i, j), (i + 1, j)))

            if i == nu - 1 or not cover[i + 1][j]:
                sides.append(((i + 1, j), (i + 1, j + 1)))

            if j == nv - 1 or not cover[i][j + 1]:
                sides.append(((i + 1, j + 1), (i, j + 1)))

            if i == 0 or not cover[i - 1][j]:
                sides.append(((i, j + 1), (i, j)))

            for start, end in sides:
                added += 1
                edges[start] = end

    if added != len(edges):
        return None, u"слишком сложная форма объединения"

    start = min(edges.keys())
    chain = [start]
    current = edges[start]

    while current != start:
        chain.append(current)
        nxt = edges.get(current)

        if nxt is None or len(chain) > len(edges):
            return None, u"не удалось обвести контур объединения"

        current = nxt

    if len(chain) != len(edges):
        return None, u"слишком сложная форма объединения"

    points = [(us[i], vs[j]) for i, j in chain]

    return _drop_collinear(points), None


def _drop_collinear(points):
    u"""Убрать промежуточные точки на прямых участках контура."""
    count = len(points)
    result = []

    for index in range(count):
        prev_pt = points[(index - 1) % count]
        cur_pt = points[index]
        next_pt = points[(index + 1) % count]

        ax = cur_pt[0] - prev_pt[0]
        ay = cur_pt[1] - prev_pt[1]
        bx = next_pt[0] - cur_pt[0]
        by = next_pt[1] - cur_pt[1]

        if abs(ax * by - ay * bx) > TOL * TOL:
            result.append(cur_pt)

    return result if len(result) >= 4 else points


# ======================================================================
#  Создание объединённой стены
# ======================================================================

def _center_offset(wall, normal):
    u"""Положение середины стены поперёк её плоскости."""
    try:
        box = wall.get_BoundingBox(None)
    except Exception:
        return None

    if box is None:
        return None

    cx = (box.Min.X + box.Max.X) / 2.0
    cy = (box.Min.Y + box.Max.Y) / 2.0

    return cx * normal.X + cy * normal.Y


def _read_params(source):
    u"""Снимок пользовательских параметров: исходную стену удалят раньше новой."""
    values = []

    for param in source.Parameters:
        try:
            if param.IsReadOnly or param.Id.IntegerValue < 0:
                continue

            kind = param.StorageType

            if kind == StorageType.String:
                values.append((param.Definition.Name, kind, param.AsString() or u""))
            elif kind == StorageType.Double:
                values.append((param.Definition.Name, kind, param.AsDouble()))
            elif kind == StorageType.Integer:
                values.append((param.Definition.Name, kind, param.AsInteger()))
            elif kind == StorageType.ElementId:
                values.append((param.Definition.Name, kind, param.AsElementId()))
        except Exception:
            pass

    extra = []

    for bip in (BuiltInParameter.PHASE_CREATED, BuiltInParameter.PHASE_DEMOLISHED):
        try:
            src = source.get_Parameter(bip)

            if src is not None and src.HasValue:
                extra.append((bip, StorageType.ElementId, src.AsElementId()))
        except Exception:
            pass

    try:
        src = source.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)

        if src is not None and src.HasValue:
            extra.append((BuiltInParameter.ALL_MODEL_MARK,
                          StorageType.String, src.AsString() or u""))
    except Exception:
        pass

    try:
        src = source.get_Parameter(BuiltInParameter.ELEM_PARTITION_PARAM)

        if src is not None and src.HasValue:
            extra.append((BuiltInParameter.ELEM_PARTITION_PARAM,
                          StorageType.Integer, src.AsInteger()))
    except Exception:
        pass

    return values, extra


def _write_params(target, snapshot):
    u"""Разложить снимок параметров по новой стене."""
    values, extra = snapshot

    for name, kind, value in values:
        try:
            twin = target.LookupParameter(name)

            if twin is None or twin.IsReadOnly or twin.StorageType != kind:
                continue

            twin.Set(value)
        except Exception:
            pass

    for bip, kind, value in extra:
        try:
            twin = target.get_Parameter(bip)

            if twin is None or twin.IsReadOnly or twin.StorageType != kind:
                continue

            twin.Set(value)
        except Exception:
            pass


def _create_wall(doc, mset, wall_type_id, level, facing):
    u"""Прямоугольник — обычная стена, всё остальное — стена по профилю."""
    outline = mset.outline

    v0 = min(v for _u, v in outline)
    v1 = max(v for _u, v in outline)

    if mset.is_rectangle:
        u0 = min(u for u, _v in outline)
        u1 = max(u for u, _v in outline)

        line = Line.CreateBound(mset.point(u0, v0), mset.point(u1, v0))

        return Wall.Create(
            doc, line, wall_type_id, level.Id,
            v1 - v0, v0 - level.Elevation, False, False
        )

    points = [mset.point(u, v) for u, v in outline]
    last_error = None

    for order in (points, list(reversed(points))):
        curves = List[Curve]()

        for index in range(len(order)):
            curves.Add(Line.CreateBound(order[index], order[(index + 1) % len(order)]))

        try:
            return Wall.Create(doc, curves, wall_type_id, level.Id, False, facing)
        except Exception as ex:
            last_error = ex

    raise Exception(u"Revit не принял контур объединения: {}".format(last_error))


def _read_inserts(doc, pieces):
    u"""Снимок окон и дверей: Revit не умеет менять хост, только вставить заново."""
    records = []

    for piece in pieces:
        try:
            inserts = piece.wall.FindInserts(True, True, True, True)
        except Exception:
            continue

        if inserts is None:
            continue

        for insert_id in inserts:
            try:
                element = doc.GetElement(insert_id)

                if not isinstance(element, FamilyInstance):
                    continue

                location = element.Location

                if not isinstance(location, LocationPoint):
                    continue

                records.append({
                    u"symbol": element.Symbol,
                    u"point": location.Point,
                    u"level": doc.GetElement(element.LevelId),
                    u"facing": element.FacingFlipped,
                    u"hand": element.HandFlipped,
                    u"params": _read_params(element),
                    u"corners": _box_corners(element),
                    u"title": u"{} · id {}".format(
                        element_name(element.Symbol) or u"проём",
                        insert_id.IntegerValue
                    )
                })
            except Exception:
                pass

    return records


def _embedded_walls(doc, pieces):
    u"""Встроенные стены и витражи. Их не удаляем и не пересоздаём."""
    own = set()

    for piece in pieces:
        try:
            own.add(piece.id.IntegerValue)
        except Exception:
            pass

    found = []
    seen = set()

    for piece in pieces:
        try:
            inserts = piece.wall.FindInserts(True, True, True, True)
        except Exception:
            continue

        if inserts is None:
            continue

        for insert_id in inserts:
            try:
                number = insert_id.IntegerValue

                if number in own or number in seen:
                    continue

                element = doc.GetElement(insert_id)

                if isinstance(element, Wall):
                    seen.add(number)
                    found.append(element)
            except Exception:
                pass

    return found


def _cut_embedded(doc, wall, embedded):
    u"""Врезать витражи в новую стену. Возврат: (сколько вышло, замечания)."""
    done = 0
    notes = []

    for other in embedded:
        try:
            SolidSolidCutUtils.AddCutBetweenSolids(doc, wall, other)
            done += 1
        except Exception as ex:
            notes.append(u"{} — врезать не вышло ({}). Проверьте вручную: "
                         u"«Изменить» → «Вырезать геометрию».".format(
                             wall_title(other), ex))

    try:
        doc.Regenerate()
    except Exception:
        pass

    return done, notes


def _patch_note(mset):
    u"""Где именно встали заращивания — чтобы промах было видно сразу."""
    parts = []

    for piece in mset.pieces:
        for u0, u1, v0, v1 in piece.patch_rects:
            parts.append(u"{}…{} × {}…{}".format(
                _num(u0 * FT_M), _num(u1 * FT_M),
                _num(v0 * FT_M), _num(v1 * FT_M)
            ))

    return u", ".join(parts) if parts else u"нет"


def _uncovered_inserts(mset, records):
    u"""Проёмы, чьё место не попало в контур объединённой стены.

    Дешевле поймать это здесь, чем получить от Revit «Экземпляры ничего не
    вырезают» в середине транзакции: там уже не видно, о каком проёме речь.
    """
    problems = []

    origin = mset.origin
    direction = mset.direction

    for record in records:
        corners = record.get(u"corners")

        if not corners:
            continue

        us = [((point.X - origin.X) * direction.X +
               (point.Y - origin.Y) * direction.Y) for point in corners]
        vs = [point.Z for point in corners]

        middle_u = (min(us) + max(us)) / 2.0
        middle_v = (min(vs) + max(vs)) / 2.0

        if not _point_in(mset.outline, middle_u, middle_v):
            outline_us = [u for u, _v in mset.outline]
            outline_vs = [v for _u, v in mset.outline]

            problems.append(
                u"{}: середина по стене {} м, по высоте {} м; "
                u"контур по стене {}…{} м, по высоте {}…{} м; "
                u"заращено: {}".format(
                    record.get(u"title", u"проём"),
                    _num(middle_u * FT_M), _num(middle_v * FT_M),
                    _num(min(outline_us) * FT_M), _num(max(outline_us) * FT_M),
                    _num(min(outline_vs) * FT_M), _num(max(outline_vs) * FT_M),
                    _patch_note(mset)
                )
            )

    return problems


def _restore_inserts(doc, wall, records, level):
    u"""Вставить проёмы в новую стену. Возврат: (сколько вышло, что не вышло)."""
    done = 0
    failed = []

    for record in records:
        try:
            symbol = record[u"symbol"]

            if not symbol.IsActive:
                symbol.Activate()
                doc.Regenerate()

            host_level = record[u"level"] or level

            instance = doc.Create.NewFamilyInstance(
                record[u"point"], symbol, wall, host_level,
                StructuralType.NonStructural
            )

            doc.Regenerate()

            if instance.FacingFlipped != record[u"facing"]:
                instance.flipFacing()

            if instance.HandFlipped != record[u"hand"]:
                instance.flipHand()

            _write_params(instance, record[u"params"])
            doc.Regenerate()

            done += 1
        except Exception as ex:
            failed.append(u"{} — {}".format(record.get(u"title", u"проём"), ex))

    return done, failed


def _z_range(wall):
    u"""Фактические отметки низа и верха стены по габаритам. None — не вышло."""
    try:
        box = wall.get_BoundingBox(None)
    except Exception:
        return None

    if box is None:
        return None

    return box.Min.Z, box.Max.Z


def _enforce_height(doc, wall, mset, level):
    u"""Сверить высоту готовой стены с контуром и поправить параметрами.

    «Смещение снизу» и «Смещение сверху» в Revit относительные: они считаются
    от уровней, и у каждого куска свои. Складывать или копировать их нельзя —
    именно на этом объединение и разъезжается. Поэтому итог проверяется не по
    параметрам, а по фактическим габаритам созданной стены.
    """
    want0 = min(v for _u, v in mset.outline)
    want1 = max(v for _u, v in mset.outline)

    got = _z_range(wall)

    if got is None:
        return

    if abs(got[0] - want0) <= V_TOL and abs(got[1] - want1) <= V_TOL:
        return

    # Развязываем верх от уровня и задаём отметки напрямую.
    for bip, value in (
        (BuiltInParameter.WALL_HEIGHT_TYPE, ElementId.InvalidElementId),
        (BuiltInParameter.WALL_BASE_CONSTRAINT, level.Id),
        (BuiltInParameter.WALL_BASE_OFFSET, want0 - level.Elevation),
        (BuiltInParameter.WALL_USER_HEIGHT_PARAM, want1 - want0),
    ):
        try:
            param = wall.get_Parameter(bip)

            if param is not None and not param.IsReadOnly:
                param.Set(value)
        except Exception:
            pass

    doc.Regenerate()

    got = _z_range(wall)

    if got is None:
        return

    if abs(got[0] - want0) > V_FAIL or abs(got[1] - want1) > V_FAIL:
        raise Exception(
            u"высота не сошлась: нужно {}…{} м, получилось {}…{} м".format(
                _num(want0 * FT_M), _num(want1 * FT_M),
                _num(got[0] * FT_M), _num(got[1] * FT_M)
            )
        )


def merge(doc, mset, wall_type, move_inserts=False):
    u"""Создать объединённую стену вместо кусков набора.

    Вызывать внутри открытой транзакции (лучше — своей вложенной, чтобы
    неудача одного набора не роняла остальные). Возврат: новая стена.

    Исходные куски удаляются ДО создания новой стены: иначе Revit ругается
    на наложение стен и показывает диалог предупреждения посреди работы.
    """
    master = mset.master.wall
    normal = mset.normal
    level = mset.master.base_level

    facing = master.Orientation
    want_offset = _center_offset(master, normal)
    snapshot = _read_params(master)
    wall_type_id = wall_type.Id

    inserts = _read_inserts(doc, mset.pieces) if move_inserts else []
    embedded = _embedded_walls(doc, mset.pieces)

    victims = List[ElementId]()

    for piece in mset.pieces:
        victims.Add(piece.id)

    doc.Delete(victims)

    new_wall = _create_wall(doc, mset, wall_type_id, level, facing)

    _write_params(new_wall, snapshot)

    doc.Regenerate()

    try:
        if new_wall.Orientation.DotProduct(facing) < 0:
            new_wall.Flip()
            doc.Regenerate()
    except Exception:
        pass

    _enforce_height(doc, new_wall, mset, level)

    # Стена рождается по осевой; если исходные стояли по грани, сдвигаем
    # новую на ту же среднюю плоскость, что была у самого большого куска.
    got_offset = _center_offset(new_wall, normal)

    if want_offset is not None and got_offset is not None:
        delta = want_offset - got_offset

        if abs(delta) > TOL / 2.0:
            try:
                ElementTransformUtils.MoveElement(
                    doc, new_wall.Id,
                    XYZ(normal.X * delta, normal.Y * delta, 0.0)
                )
            except Exception:
                pass

    if embedded:
        mset.embedded_cut, mset.embedded_notes = _cut_embedded(
            doc, new_wall, embedded
        )

    if inserts:
        gaps = _uncovered_inserts(mset, inserts)

        if gaps:
            raise Exception(
                u"объединённая стена не накрывает проёмы: {}".format(
                    u"; ".join(gaps)
                )
            )

        done, failed = _restore_inserts(doc, new_wall, inserts, level)
        mset.moved_inserts = done

        if failed:
            raise Exception(u"не удалось вставить проёмы: {}".format(
                u"; ".join(failed)
            ))

    return new_wall
