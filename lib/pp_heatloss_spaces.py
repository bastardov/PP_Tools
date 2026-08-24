# -*- coding: utf-8 -*-
"""Поиск пространства, связанного со строительным элементом.

Логика портирована из инструмента «Перенос данных из пространств» (v8):
она должна давать ТОТ ЖЕ результат, иначе проверка будет ругаться на
значения, которые сама же и записала.

Отличия от оригинала — только организационные:
  * doc передаётся явно, а не берётся из глобали модуля;
  * состояние (кэш пространств, кэш несущих стен, кэш пространств по
    хост-стенам) живёт в объекте SpaceFinder, а не в глобальных списках,
    поэтому один запуск проверки = один кэш;
  * ориентация по сторонам света не считается (проверке она не нужна).

Оригинальный инструмент НЕ ТРОГАЕТСЯ: он продолжает работать на своей
копии кода. Этот модуль — read-only спутник для проверок.

Порядок поиска (как в v8):
  1. "pts"      — зонды + doc.GetSpaceAtPoint() с фазой элемента;
  2. "ipis"     — те же зонды + Space.IsPointInSpace() по пространствам,
                  пересекающимся с элементом по Z;
  3. "fallback" — ближайшее в плане пространство внутри Z-диапазона
                  (ненадёжно, помечается отдельно).
"""

import math

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    FilteredElementCollector,
    LocationCurve,
    WallKind,
    XYZ,
)


# --------------------------------------------------------------------------- #
#                                  константы                                  #
# --------------------------------------------------------------------------- #

# Отступ зонда от грани элемента, футы (150 мм).
MARGIN_FT = 0.15 / 0.3048

# Отступ зонда над/под перекрытием, футы (100 мм).
FLOOR_OFFSET = 0.10 / 0.3048

WALL_CAT = int(BuiltInCategory.OST_Walls)
WINDOW_CAT = int(BuiltInCategory.OST_Windows)
DOOR_CAT = int(BuiltInCategory.OST_Doors)
FLOOR_CAT = int(BuiltInCategory.OST_Floors)

METHOD_PTS = u"pts"
METHOD_IPIS = u"ipis"
METHOD_FALLBACK = u"fallback"

# Методы, которым можно доверять без ручной проверки.
RELIABLE_METHODS = (METHOD_PTS, METHOD_IPIS)


# --------------------------------------------------------------------------- #
#                              результат поиска                               #
# --------------------------------------------------------------------------- #

class SpaceHit(object):
    """Результат поиска пространства для одного элемента."""

    def __init__(self, space=None, method=None, votes=0, total=0, note=u""):
        self.space = space
        self.method = method
        self.votes = votes
        self.total = total
        # Пояснение для отчёта, когда пространство не найдено.
        self.note = note or u""

    @property
    def found(self):
        return self.space is not None

    @property
    def reliable(self):
        return self.space is not None and self.method in RELIABLE_METHODS

    def method_label(self):
        if self.method == METHOD_PTS:
            return u"{}/{} зондов".format(self.votes, self.total)
        if self.method == METHOD_IPIS:
            return u"IPIS {}/{}".format(self.votes, self.total)
        if self.method == METHOD_FALLBACK:
            return u"2D-ближайшее (ненадёжно)"
        return u"не найдено"


# --------------------------------------------------------------------------- #
#                             утилиты (без doc)                               #
# --------------------------------------------------------------------------- #

def get_category_id(element):
    try:
        return element.Category.Id.IntegerValue
    except:
        return None


def is_wall(element):
    return get_category_id(element) == WALL_CAT


def is_curtain_wall(wall):
    try:
        return wall.WallType.Kind == WallKind.Curtain
    except:
        return False


def get_space_name(space):
    """Имя пространства — как читает его «Перенос данных»."""
    for pname in [None, u"Name", u"Имя"]:
        try:
            if pname is None:
                param = space.get_Parameter(BuiltInParameter.ROOM_NAME)
            else:
                param = space.LookupParameter(pname)
            if param:
                value = param.AsString()
                if value:
                    return value
        except:
            pass
    return u""


def get_space_number(space):
    try:
        return space.Number or u""
    except:
        return u""


def build_num_name(space):
    """Эталонное значение PP_Номер имя помещения.

    Формат обязан совпадать с process_wall() в «Переносе данных»:
        u"{} {}".format(number, name).strip()
    """
    if space is None:
        return u""
    number = get_space_number(space)
    name = get_space_name(space)
    return u"{} {}".format(number, name).strip()


def get_wall_normal(wall):
    try:
        location = wall.Location
        if not isinstance(location, LocationCurve):
            return None
        direction = location.Curve.Direction
        return XYZ(-direction.Y, direction.X, 0).Normalize()
    except:
        return None


def get_wall_probe_offsets(wall):
    """Расстояния зондов от линии стены (v8).

    Линия привязки может быть осью ИЛИ гранью — зондируем на двух
    расстояниях: W/2+150 (линия = ось) и W+150 (линия = грань).
    """
    try:
        width = wall.Width
    except:
        width = 0.5 / 0.3048
    return [width / 2.0 + MARGIN_FT, width + MARGIN_FT]


def _probe_points_along_curve(curve, normal, offsets, z_min, z_max):
    """Общая сетка зондов: 3 точки по длине × 2 по высоте × offsets × 2 стороны."""
    points = []
    height = z_max - z_min

    for t in [0.25, 0.50, 0.75]:
        try:
            base = curve.Evaluate(t, True)
        except:
            continue
        for z_ratio in [1.0 / 3.0, 2.0 / 3.0]:
            z = z_min + height * z_ratio
            for offset in offsets:
                for sign in [1, -1]:
                    points.append((
                        XYZ(
                            base.X + normal.X * offset * sign,
                            base.Y + normal.Y * offset * sign,
                            z
                        ),
                        sign
                    ))
    return points


def get_floor_probe_points(floor):
    bounding_box = floor.get_BoundingBox(None)
    if bounding_box is None:
        return []

    points = []
    for tx in [0.25, 0.50, 0.75]:
        for ty in [0.25, 0.50, 0.75]:
            x = bounding_box.Min.X + (bounding_box.Max.X - bounding_box.Min.X) * tx
            y = bounding_box.Min.Y + (bounding_box.Max.Y - bounding_box.Min.Y) * ty
            points.append((XYZ(x, y, bounding_box.Min.Z - FLOOR_OFFSET), -1))
            points.append((XYZ(x, y, bounding_box.Max.Z + FLOOR_OFFSET), 1))

    return points


# --------------------------------------------------------------------------- #
#                                 SpaceFinder                                 #
# --------------------------------------------------------------------------- #

class SpaceFinder(object):
    """Ищет пространство рядом с элементом; кэши живут один запуск."""

    def __init__(self, doc):
        self.doc = doc
        self._all_spaces = None
        self._basic_walls = None
        self._host_cache = {}      # id хост-стены -> SpaceHit
        self._curtain_host = {}    # id витража -> хост-стена или None

    # ---------------------------- коллекции -------------------------------- #

    def get_all_spaces(self):
        """Все размещённые пространства документа (кэш на один запуск)."""
        if self._all_spaces is None:
            spaces = []
            try:
                collector = FilteredElementCollector(self.doc) \
                    .OfCategory(BuiltInCategory.OST_MEPSpaces) \
                    .WhereElementIsNotElementType() \
                    .ToElements()
            except:
                collector = []
            for space in collector:
                try:
                    if space.Area > 0:
                        spaces.append(space)
                except:
                    pass
            self._all_spaces = spaces
        return self._all_spaces

    def get_basic_walls(self):
        """Несущие (не витражные) стены — для поиска хоста витража."""
        if self._basic_walls is None:
            walls = []
            try:
                collector = FilteredElementCollector(self.doc) \
                    .OfCategory(BuiltInCategory.OST_Walls) \
                    .WhereElementIsNotElementType() \
                    .ToElements()
            except:
                collector = []
            for wall in collector:
                try:
                    if not is_curtain_wall(wall):
                        walls.append(wall)
                except:
                    pass
            self._basic_walls = walls
        return self._basic_walls

    def spaces_in_z_range(self, z_min, z_max, z_tolerance=1.5):
        """Пространства, пересекающиеся с диапазоном [z_min, z_max] по Z."""
        result = []
        for space in self.get_all_spaces():
            try:
                bounding_box = space.get_BoundingBox(None)
                if bounding_box is not None:
                    if bounding_box.Max.Z < z_min - z_tolerance:
                        continue
                    if bounding_box.Min.Z > z_max + z_tolerance:
                        continue
                else:
                    try:
                        level_z = space.Level.Elevation
                        if not ((z_min - z_tolerance) <= level_z <= (z_max + z_tolerance)):
                            continue
                    except:
                        pass
                result.append(space)
            except:
                pass
        return result

    # ------------------------------ методы --------------------------------- #

    def get_element_phase(self, element):
        """Фаза создания элемента; если нет — последняя фаза проекта."""
        try:
            param = element.get_Parameter(BuiltInParameter.PHASE_CREATED)
            if param and param.HasValue:
                phase = self.doc.GetElement(param.AsElementId())
                if phase is not None:
                    return phase
        except:
            pass
        try:
            phases = self.doc.Phases
            return phases.get_Item(phases.Size - 1)
        except:
            return None

    def get_space_at_point(self, point, phase):
        """GetSpaceAtPoint с фазой; без фазы — как раньше."""
        try:
            if phase is not None:
                space = self.doc.GetSpaceAtPoint(point, phase)
                if space is not None:
                    return space
            return self.doc.GetSpaceAtPoint(point)
        except:
            try:
                return self.doc.GetSpaceAtPoint(point)
            except:
                return None

    def vote_for_space(self, points, phase=None):
        """Голосование зондов через GetSpaceAtPoint. -> (space, votes, total)."""
        votes = {}
        space_refs = {}

        for point, _sign in points:
            try:
                space = self.get_space_at_point(point, phase)
                if space is not None and space.Area > 0:
                    key = space.Id.IntegerValue
                    votes[key] = votes.get(key, 0) + 1
                    space_refs[key] = space
            except:
                pass

        if not votes:
            return None, 0, len(points)

        best_key = max(votes, key=lambda k: votes[k])
        return space_refs[best_key], votes[best_key], len(points)

    def vote_for_space_ipis(self, points, z_min, z_max):
        """Голосование зондов через Space.IsPointInSpace().

        Работает там, где doc.GetSpaceAtPoint() молчит (отключённые объёмы,
        расхождение фаз и т.п.).
        """
        candidates = self.spaces_in_z_range(z_min, z_max)
        if not candidates:
            return None, 0, len(points)

        votes = {}
        space_refs = {}

        for point, _sign in points:
            for space in candidates:
                try:
                    if space.IsPointInSpace(point):
                        key = space.Id.IntegerValue
                        votes[key] = votes.get(key, 0) + 1
                        space_refs[key] = space
                        break
                except:
                    pass

        if not votes:
            return None, 0, len(points)

        best_key = max(votes, key=lambda k: votes[k])
        return space_refs[best_key], votes[best_key], len(points)

    def nearest_space_fallback(self, reference_point, z_min=None, z_max=None):
        """Ближайшее в плане пространство, но только в пределах Z-диапазона.

        Без Z-фильтра стены друг над другом получали бы одно и то же
        помещение (баг v5 оригинала).
        """
        if z_min is not None and z_max is not None:
            candidates = self.spaces_in_z_range(z_min, z_max)
        else:
            candidates = self.get_all_spaces()

        best = None
        best_distance = float("inf")

        for space in candidates:
            try:
                if space.Location is None:
                    continue
                space_point = space.Location.Point
                dx = reference_point.X - space_point.X
                dy = reference_point.Y - space_point.Y
                distance = math.sqrt(dx * dx + dy * dy)
                if distance < best_distance:
                    best_distance = distance
                    best = space
            except:
                pass

        return best

    # ------------------------------- стена --------------------------------- #

    def find_for_wall(self, wall):
        """Обычная (несущая) стена."""
        normal = get_wall_normal(wall)
        if normal is None:
            return SpaceHit(note=u"у стены нет линии привязки")

        bounding_box = wall.get_BoundingBox(None)
        if bounding_box is None:
            return SpaceHit(note=u"нет габаритного контейнера")

        try:
            curve = wall.Location.Curve
        except:
            return SpaceHit(note=u"у стены нет линии привязки")

        points = _probe_points_along_curve(
            curve,
            normal,
            get_wall_probe_offsets(wall),
            bounding_box.Min.Z,
            bounding_box.Max.Z
        )
        if not points:
            return SpaceHit(note=u"не удалось построить зонды")

        space, votes, total = self.vote_for_space(
            points, self.get_element_phase(wall)
        )
        if space is not None:
            return SpaceHit(space, METHOD_PTS, votes, total)

        space, votes, total = self.vote_for_space_ipis(
            points, bounding_box.Min.Z, bounding_box.Max.Z
        )
        if space is not None:
            return SpaceHit(space, METHOD_IPIS, votes, total)

        try:
            middle = curve.Evaluate(0.5, True)
            best = self.nearest_space_fallback(
                middle, bounding_box.Min.Z, bounding_box.Max.Z
            )
            if best is not None:
                return SpaceHit(best, METHOD_FALLBACK)
        except:
            pass

        return SpaceHit(note=u"ни один зонд не попал в пространство")

    # ------------------------------- витраж -------------------------------- #

    def find_host_wall_for_curtain(self, curtain_wall):
        """Несущая стена, в которую вставлен витраж (кэш по id витража).

        Критерии в 2D: параллельность осей, перпендикулярное расстояние
        в пределах полутолщины стены и перекрытие проекций по длине.
        """
        key = curtain_wall.Id.IntegerValue
        if key in self._curtain_host:
            return self._curtain_host[key]

        host = self._search_host_wall(curtain_wall)
        self._curtain_host[key] = host
        return host

    def _search_host_wall(self, curtain_wall):
        try:
            curve = curtain_wall.Location.Curve
            direction = XYZ(curve.Direction.X, curve.Direction.Y, 0).Normalize()
            middle = curve.Evaluate(0.5, True)
            middle_2d = XYZ(middle.X, middle.Y, 0)
            start_2d = XYZ(curve.GetEndPoint(0).X, curve.GetEndPoint(0).Y, 0)
            end_2d = XYZ(curve.GetEndPoint(1).X, curve.GetEndPoint(1).Y, 0)
        except:
            return None

        best_wall = None
        best_distance = float("inf")

        for wall in self.get_basic_walls():
            try:
                location = wall.Location
                if not isinstance(location, LocationCurve):
                    continue

                wall_curve = location.Curve
                wall_direction = XYZ(
                    wall_curve.Direction.X, wall_curve.Direction.Y, 0
                ).Normalize()

                if abs(direction.DotProduct(wall_direction)) < 0.98:
                    continue

                wall_start = XYZ(
                    wall_curve.GetEndPoint(0).X, wall_curve.GetEndPoint(0).Y, 0
                )
                along = (middle_2d - wall_start).DotProduct(wall_direction)
                projected = wall_start + wall_direction.Multiply(along)
                distance_perp = middle_2d.DistanceTo(projected)

                tolerance = wall.Width / 2.0 + (10.0 / 304.8)
                if distance_perp > tolerance:
                    continue

                wall_end = XYZ(
                    wall_curve.GetEndPoint(1).X, wall_curve.GetEndPoint(1).Y, 0
                )
                wall_length = wall_start.DistanceTo(wall_end)
                projection_start = (start_2d - wall_start).DotProduct(wall_direction)
                projection_end = (end_2d - wall_start).DotProduct(wall_direction)
                overlap_start = max(min(projection_start, projection_end), 0.0)
                overlap_end = min(max(projection_start, projection_end), wall_length)

                if overlap_end <= overlap_start:
                    continue

                if distance_perp < best_distance:
                    best_distance = distance_perp
                    best_wall = wall
            except:
                pass

        return best_wall

    def find_for_curtain(self, curtain_wall):
        """Витраж: Z-диапазон от витража, нормаль и отступ от хост-стены."""
        host_wall = self.find_host_wall_for_curtain(curtain_wall)

        normal = get_wall_normal(host_wall) if host_wall is not None else None
        if normal is None:
            normal = get_wall_normal(curtain_wall)
        if normal is None:
            return SpaceHit(note=u"у витража нет линии привязки")

        bounding_box = curtain_wall.get_BoundingBox(None)
        if bounding_box is None:
            return SpaceHit(note=u"нет габаритного контейнера")

        if host_wall is not None:
            offsets = get_wall_probe_offsets(host_wall)
        else:
            offsets = [MARGIN_FT * 2, MARGIN_FT * 4]

        try:
            curve = curtain_wall.Location.Curve
        except:
            return SpaceHit(note=u"у витража нет линии привязки")

        points = _probe_points_along_curve(
            curve, normal, offsets, bounding_box.Min.Z, bounding_box.Max.Z
        )
        if not points:
            return SpaceHit(note=u"не удалось построить зонды")

        space, votes, total = self.vote_for_space(
            points, self.get_element_phase(curtain_wall)
        )
        if space is not None:
            return SpaceHit(space, METHOD_PTS, votes, total)

        space, votes, total = self.vote_for_space_ipis(
            points, bounding_box.Min.Z, bounding_box.Max.Z
        )
        if space is not None:
            return SpaceHit(space, METHOD_IPIS, votes, total)

        try:
            middle = curve.Evaluate(0.5, True)
            best = self.nearest_space_fallback(
                middle, bounding_box.Min.Z, bounding_box.Max.Z
            )
            if best is not None:
                return SpaceHit(best, METHOD_FALLBACK)
        except:
            pass

        return SpaceHit(note=u"ни один зонд не попал в пространство")

    # ---------------------------- перекрытие ------------------------------- #

    def find_for_floor(self, floor):
        """Перекрытие: зонды сеткой над и под плитой."""
        points = get_floor_probe_points(floor)
        if not points:
            return SpaceHit(note=u"нет габаритного контейнера")

        space, votes, total = self.vote_for_space(
            points, self.get_element_phase(floor)
        )
        if space is not None:
            return SpaceHit(space, METHOD_PTS, votes, total)

        bounding_box = floor.get_BoundingBox(None)
        if bounding_box is None:
            return SpaceHit(note=u"нет габаритного контейнера")

        # Запас 10 футов: пространство этажа выше/ниже плиты.
        space, votes, total = self.vote_for_space_ipis(
            points, bounding_box.Min.Z - 10.0, bounding_box.Max.Z + 10.0
        )
        if space is not None:
            return SpaceHit(space, METHOD_IPIS, votes, total)

        center = XYZ(
            (bounding_box.Min.X + bounding_box.Max.X) / 2.0,
            (bounding_box.Min.Y + bounding_box.Max.Y) / 2.0,
            (bounding_box.Min.Z + bounding_box.Max.Z) / 2.0
        )
        best = self.nearest_space_fallback(
            center, bounding_box.Min.Z - 10.0, bounding_box.Max.Z + 10.0
        )
        if best is not None:
            return SpaceHit(best, METHOD_FALLBACK)

        return SpaceHit(note=u"ни один зонд не попал в пространство")

    # -------------------------- окно / дверь ------------------------------- #

    def find_for_hosted(self, element):
        """Окно или дверь: пространство берётся у хост-стены.

        Так же поступает «Перенос данных» — он копирует значения именно
        с хост-стены, а не ищет пространство для проёма отдельно.
        Результат по хост-стене кэшируется: у одной стены обычно
        несколько окон.
        """
        try:
            host = element.Host
        except:
            host = None

        if host is None:
            return SpaceHit(note=u"хост-стена не найдена"), None
        if get_category_id(host) != WALL_CAT:
            return SpaceHit(note=u"хост не является стеной"), host

        return self.find_for_host_wall(host), host

    def find_for_host_wall(self, wall):
        """Кэшированный поиск пространства для стены (по id)."""
        key = wall.Id.IntegerValue
        hit = self._host_cache.get(key)
        if hit is None:
            if is_curtain_wall(wall):
                hit = self.find_for_curtain(wall)
            else:
                hit = self.find_for_wall(wall)
            self._host_cache[key] = hit
        return hit

    # ------------------------------ диспетчер ------------------------------ #

    def find(self, element):
        """Единая точка входа. -> (SpaceHit, host_element или None)."""
        category_id = get_category_id(element)

        if category_id == WALL_CAT:
            return self.find_for_host_wall(element), None
        if category_id in (WINDOW_CAT, DOOR_CAT):
            return self.find_for_hosted(element)
        if category_id == FLOOR_CAT:
            return self.find_for_floor(element), None

        return SpaceHit(note=u"категория не поддерживается"), None
