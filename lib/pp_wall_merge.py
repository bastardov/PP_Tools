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


def _param_double(element, bip):
    try:
        p = element.get_Parameter(bip)
        if p is not None and p.HasValue:
            return p.AsDouble()
    except Exception:
        pass

    return 0.0


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
    u"""Кусок стены: контур грани в системе координат набора."""

    def __init__(self, wall, base_level, polygon, source=u"грани"):
        self.wall = wall
        self.id = wall.Id
        self.base_level = base_level
        self.source = source
        self.polygon = polygon

        us = [u for u, _v in polygon]
        vs = [v for _u, v in polygon]

        self.u0 = min(us)
        self.u1 = max(us)
        self.v0 = min(vs)
        self.v1 = max(vs)
        self.area = _polygon_area(polygon)

    @property
    def type_id(self):
        try:
            return self.wall.WallType.Id
        except Exception:
            return None


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
            rows.append(u"{} · длина {} м · низ {}, верх {} · по {}".format(
                wall_title(piece.wall),
                _num((piece.u1 - piece.u0) * FT_M),
                _num(piece.v0 * FT_M),
                _num(piece.v1 * FT_M),
                piece.source
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

def _reason_not_mergeable(wall, points):
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
            return u"в стене есть проёмы (окно, дверь, ниша)"
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
    if points is not None and not _is_rectilinear(points):
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


def _biggest_face(wall):
    u"""Самая большая грань стены, смотрящая наружу или внутрь. None — нет."""
    try:
        options = Options()
        options.ComputeReferences = False
        options.IncludeNonVisibleObjects = False
        options.DetailLevel = ViewDetailLevel.Medium

        facing = wall.Orientation
        best = None

        for item in _solids(wall.get_Geometry(options)):
            for face in item.Faces:
                if not isinstance(face, PlanarFace):
                    continue

                normal = face.FaceNormal

                if abs(normal.Z) > 0.01:
                    continue

                if abs(normal.DotProduct(facing)) < 0.99:
                    continue

                if best is None or face.Area > best.Area:
                    best = face

        return best
    except Exception:
        return None


def _same_point(a, b):
    return (abs(a.X - b.X) <= TOL and
            abs(a.Y - b.Y) <= TOL and
            abs(a.Z - b.Z) <= TOL)


def _face_polygon(face):
    u"""Точки контура грани по порядку обхода. None — прочитать не вышло."""
    try:
        loops = face.EdgeLoops

        if loops.Size != 1:
            return None

        points = []

        for edge in loops.get_Item(0):
            try:
                chunk = edge.TessellateOnFace(face)
            except Exception:
                chunk = edge.Tessellate()

            for point in chunk:
                if points and _same_point(points[-1], point):
                    continue

                points.append(point)

        while len(points) > 1 and _same_point(points[0], points[-1]):
            points.pop()

        return points if len(points) >= 4 else None
    except Exception:
        return None


def _is_rectilinear(points):
    u"""Контур состоит только из горизонталей и вертикалей."""
    count = len(points)

    for index in range(count):
        a = points[index]
        b = points[(index + 1) % count]

        if abs(a.Z - b.Z) <= TOL:
            continue

        if abs(a.X - b.X) <= TOL and abs(a.Y - b.Y) <= TOL:
            continue

        return False

    return True


def _vertical_range(wall, doc):
    u"""(низ, верх) по параметрам стены. Запасной путь, если нет геометрии.

    Параметрам верить нельзя, когда у стены отредактирован профиль: они
    описывают зависимости, а не реальное тело. Поэтому это именно фолбэк.
    """
    base_id = _param_id(wall, BuiltInParameter.WALL_BASE_CONSTRAINT)
    base_level = doc.GetElement(base_id) if base_id is not None else None

    if not isinstance(base_level, Level):
        return None

    base_z = base_level.Elevation + _param_double(wall, BuiltInParameter.WALL_BASE_OFFSET)

    top_id = _param_id(wall, BuiltInParameter.WALL_HEIGHT_TYPE)
    top_level = doc.GetElement(top_id) if top_id is not None else None

    if isinstance(top_level, Level):
        top_z = top_level.Elevation + _param_double(wall, BuiltInParameter.WALL_TOP_OFFSET)
    else:
        top_z = base_z + _param_double(wall, BuiltInParameter.WALL_USER_HEIGHT_PARAM)

    if top_z - base_z < TOL:
        return None

    return base_z, top_z


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

def plan(doc, walls):
    u"""Разложить выделенные стены на наборы. Возврат: (sets, rejects)."""
    rejects = []
    groups = {}

    for wall in walls:
        try:
            key, item, reason = _prepare(wall, doc)
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


def _prepare(wall, doc):
    u"""Разобрать одну стену. Возврат: (ключ плоскости, данные, причина отказа)."""
    face = _biggest_face(wall)
    points = _face_polygon(face) if face is not None else None

    if face is not None and points is None:
        return None, None, u"не удалось прочитать контур грани стены"

    reason = _reason_not_mergeable(wall, points)

    if reason:
        return None, None, reason

    base_id = _param_id(wall, BuiltInParameter.WALL_BASE_CONSTRAINT)
    base_level = doc.GetElement(base_id) if base_id is not None else None

    if not isinstance(base_level, Level):
        return None, None, u"у стены не задан базовый уровень"

    # Контур куска снимаем с РЕАЛЬНОЙ грани: параметры «низ/верх» врут,
    # если у стены отредактирован профиль (перемычка над проёмом — как раз
    # такой случай: по параметрам она от пола до потолка).
    vertical = _vertical_range(wall, doc)

    if points is None and vertical is None:
        return None, None, u"не удалось определить низ и верх"

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

    return key, (wall, line, direction, base_level, points, vertical), None


def _plane_sets(items):
    u"""Собрать наборы внутри одной плоскости. Возврат: (sets, rejects)."""
    origin = items[0][1].GetEndPoint(0)
    direction = items[0][2]

    def along(point):
        return ((point.X - origin.X) * direction.X +
                (point.Y - origin.Y) * direction.Y)

    pieces = []

    for wall, line, _dir, base_level, points, vertical in items:
        if points:
            polygon = [(along(point), point.Z) for point in points]
            source = u"грани"
        else:
            u_a = along(line.GetEndPoint(0))
            u_b = along(line.GetEndPoint(1))
            v0, v1 = vertical

            polygon = [
                (min(u_a, u_b), v0), (max(u_a, u_b), v0),
                (max(u_a, u_b), v1), (min(u_a, u_b), v1)
            ]
            source = u"параметрам"

        pieces.append(Piece(wall, base_level, polygon, source))

    return _build_sets(pieces, origin, direction)


def _axis(values):
    u"""Отсортированные координаты сетки, близкие слиты в одну."""
    ordered = sorted(values)
    result = []

    for value in ordered:
        if not result or value - result[-1] > TOL:
            result.append(value)

    return result


def _polygon_area(polygon):
    u"""Площадь замкнутого контура по формуле шнуровки."""
    total = 0.0
    count = len(polygon)

    for index in range(count):
        x0, y0 = polygon[index]
        x1, y1 = polygon[(index + 1) % count]
        total += x0 * y1 - x1 * y0

    return abs(total) / 2.0


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

    us = _axis([u for piece in pieces for u, _v in piece.polygon])
    vs = _axis([v for piece in pieces for _u, v in piece.polygon])

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
                        _point_in(piece.polygon, cu, cv)):
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
                    u"рядом нет второго куска в той же плоскости"
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


def merge(doc, mset, wall_type):
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

    return new_wall
