# -*- coding: utf-8 -*-
u"""PP_Tools — раскладка марок на виде: поиск наложений и раздвижка.

Модуль решает одну задачу: марки, наехавшие друг на друга, разъезжаются на
минимальное расстояние, а всё остальное на виде остаётся как было.

Как пользоваться из кнопки:

    import pp_tag_layout

    tags = pp_tag_layout.collect_tags(doc, view)
    boxes = pp_tag_layout.measure_boxes(doc, view, tags)      # ВНЕ транзакции
    result = pp_tag_layout.solve(boxes, gap, max_offset)      # чистая математика

    t = Transaction(doc, u"Раздвинуть марки")
    t.Start()
    pp_tag_layout.apply_moves(doc, view, boxes, result[u"moves"], options)
    t.Commit()

Или одной строкой, если транзакциями управлять не нужно:

    result = pp_tag_layout.spread_tags(doc, view, tags, options)

Четыре вещи, которые важно помнить.

1. ``measure_boxes`` открывает СВОЮ транзакцию и откатывает её. Вызывать её,
   когда другая транзакция уже открыта, нельзя — Revit не разрешает вложенные.
   Инструмент, который сам расставляет марки, сначала закрывает свою
   транзакцию, и только потом зовёт раздвижку.

2. ``tag.get_BoundingBox(view)`` возвращает габарит ВМЕСТЕ с выноской, поэтому
   для поиска наложений он не годится. Замер идёт в транзакции-зонде: сначала
   запоминается геометрия выносок, потом выноски временно выключаются,
   снимается чистый габарит текста, транзакция откатывается. Откат возвращает
   выноски, изгибы и LeaderEndCondition ровно как были.

3. Считаем в плоскости вида: u вдоль view.RightDirection, v вдоль
   view.UpDirection. Поэтому инструмент одинаково работает на планах, разрезах
   и фасадах, а координата поперёк вида не трогается вообще.

4. Разведённые по тексту марки — ещё не разобранный лист: выноски при этом
   спокойно перекрещиваются между собой и проходят сквозь чужой текст. Поэтому
   решатель считает не только наложения прямоугольников, но и пересечения
   выносок, и разбирает их ДВУМЯ разными способами — перестановкой марок
   местами (перекрестились = порядок марок не совпадает с порядком элементов)
   и выталкиванием марки с чужой выноски.

``solve`` про Revit не знает — на вход идут словари, на выход словари. Это
позволяет окну инструмента пересчитывать предпросмотр на каждое изменение
параметров, не открывая транзакций.
"""

import math

from Autodesk.Revit.DB import (
    ElementId, FilteredElementCollector, IndependentTag, Transaction, XYZ
)

import pp_leaders
import pp_tags


MM_PER_FT = 304.8

#: Смещение меньше этого (в футах модели) считаем нулевым и марку не трогаем.
MIN_MOVE_FT = 0.001

MODE_AUTO = u"auto"
MODE_VERTICAL = u"vertical"
MODE_HORIZONTAL = u"horizontal"

FROZEN_PINNED = u"закреплена"
FROZEN_GROUP = u"внутри группы"
FROZEN_SCOPE = u"вне области раздвижки"

#: Веса бед при выборе лучшей раскладки. Наложение текста читается хуже всего,
#: выноска сквозь чужой текст — следом, перекрестившиеся выноски — легче всего.
WEIGHT_OVERLAP = 3.0
WEIGHT_THROUGH = 2.0
WEIGHT_CROSS = 1.0

#: Сколько соседей смотрим при перестановке и сколько перестановок примеряем за
#: проход. Без этих потолков на плотном плане предпросмотр начинает думать
#: секундами, а пользы от дальних перестановок всё равно нет.
MAX_NEIGHBOURS = 10
MAX_SWAP_ATTEMPTS = 2500

DEFAULT_OPTIONS = {
    u"gap_mm": 1.5,
    u"max_offset_mm": 20.0,
    u"mode": MODE_AUTO,
    u"leaders": True,
    u"enable_leader": True,
    u"leader_threshold_mm": 5.0,
    u"rebuild_elbow": True,
    u"iterations": 60,
}


# ======================================================================
#  Единицы и координаты вида
# ======================================================================

def view_scale(view):
    u"""Знаменатель масштаба вида. Для видов без масштаба — 100."""
    try:
        scale = int(view.Scale)

        if scale > 0:
            return scale
    except Exception:
        pass

    return 100


def mm_to_ft(mm, view):
    u"""Длина НА ЛИСТЕ в миллиметрах -> длина в модели в футах."""
    return float(mm) / MM_PER_FT * view_scale(view)


def ft_to_mm(ft, view):
    u"""Обратный перевод: футы модели -> миллиметры на листе."""
    return float(ft) * MM_PER_FT / view_scale(view)


def view_axes(view):
    u"""Начало отсчёта и оси плоскости вида."""
    return view.Origin, view.RightDirection, view.UpDirection


def to_uv(point, origin, right, up):
    u"""Точка модели -> координаты в плоскости вида."""
    delta = point.Subtract(origin)

    return delta.DotProduct(right), delta.DotProduct(up)


def to_xyz(point, right, up, du, dv):
    u"""Сдвинуть точку модели на (du, dv) в плоскости вида."""
    return XYZ(
        point.X + right.X * du + up.X * dv,
        point.Y + right.Y * du + up.Y * dv,
        point.Z + right.Z * du + up.Z * dv
    )


# ======================================================================
#  Сбор марок
# ======================================================================

def collect_tags(doc, view):
    u"""Все марки, видимые на виде. Порядок Revit сохраняем."""
    tags = []

    collector = FilteredElementCollector(doc, view.Id) \
        .OfClass(IndependentTag) \
        .WhereElementIsNotElementType()

    for tag in collector:
        tags.append(tag)

    return tags


def freeze_reason(tag):
    u"""Почему марку двигать нельзя. None — двигать можно."""
    try:
        if tag.Pinned:
            return FROZEN_PINNED
    except Exception:
        pass

    try:
        group_id = tag.GroupId

        if group_id is not None and group_id != ElementId.InvalidElementId:
            return FROZEN_GROUP
    except Exception:
        pass

    return None


def tag_category(tag):
    u"""(id категории, имя категории) самой марки, а не маркируемого элемента.

    Пользователь думает категориями марок — «Марки воздухораспределителей», —
    и они же определены для марок элементов из связей, где маркируемый элемент
    в модели не найти.
    """
    try:
        category = tag.Category

        if category is not None:
            return category.Id.IntegerValue, category.Name
    except Exception:
        pass

    return 0, u"Марки без категории"


# ======================================================================
#  Замер габаритов и выносок: транзакция-зонд с откатом
# ======================================================================

def _extent_uv(bbox, origin, right, up):
    u"""Габарит модели -> прямоугольник в плоскости вида."""
    transform = bbox.Transform
    lo = bbox.Min
    hi = bbox.Max

    us = []
    vs = []

    for x in (lo.X, hi.X):
        for y in (lo.Y, hi.Y):
            for z in (lo.Z, hi.Z):
                point = XYZ(x, y, z)

                try:
                    point = transform.OfPoint(point)
                except Exception:
                    pass

                u_value, v_value = to_uv(point, origin, right, up)

                us.append(u_value)
                vs.append(v_value)

    return min(us), max(us), min(vs), max(vs)


def _element_point(doc, tag, view):
    u"""Точка маркируемого элемента — куда смотрит выноска.

    Нужна для марок с прикреплённой выноской: у них GetLeaderEnd не работает,
    а точка привязки Revit'ом наружу не отдаётся. Точка расположения элемента
    для разбора пересечений достаточно точна.
    """
    element = pp_tags.get_tagged_element_from_tag(doc, tag)

    if element is None:
        return None

    try:
        location = element.Location

        if location is not None and hasattr(location, u"Point"):
            return location.Point
    except Exception:
        pass

    try:
        bbox = element.get_BoundingBox(view)

        if bbox is None:
            bbox = element.get_BoundingBox(None)

        if bbox is not None:
            return XYZ(
                (bbox.Min.X + bbox.Max.X) * 0.5,
                (bbox.Min.Y + bbox.Max.Y) * 0.5,
                (bbox.Min.Z + bbox.Max.Z) * 0.5
            )
    except Exception:
        pass

    return None


def _leader_geometry(doc, tag, view):
    u"""(конец выноски, изгиб) в координатах модели. Читать ДО выключения выносок."""
    try:
        if not tag.HasLeader:
            return None, None
    except Exception:
        return None, None

    reference = pp_leaders.get_first_reference(tag)

    end = pp_leaders.safe_get_leader_end(tag, reference)
    elbow = pp_leaders.safe_get_leader_elbow(tag, reference)

    if end is None:
        end = _element_point(doc, tag, view)

    return end, elbow


def _make_box(tag, view, origin, right, up, movable_ids, leader_geometry):
    u"""Один прямоугольник для решателя. None — марку в расчёт не берём."""
    try:
        bbox = tag.get_BoundingBox(view)
    except Exception:
        bbox = None

    if bbox is None:
        return None

    try:
        head = tag.TagHeadPosition
    except Exception:
        return None

    if head is None:
        return None

    u_min, u_max, v_min, v_max = _extent_uv(bbox, origin, right, up)
    head_u, head_v = to_uv(head, origin, right, up)

    tag_id = tag.Id.IntegerValue
    reason = freeze_reason(tag)

    if reason is None and movable_ids is not None and tag_id not in movable_ids:
        reason = FROZEN_SCOPE

    category_id, category_name = tag_category(tag)

    box = {
        u"id": tag_id,
        u"hu": head_u,
        u"hv": head_v,
        u"cu": (u_min + u_max) * 0.5 - head_u,
        u"cv": (v_min + v_max) * 0.5 - head_v,
        u"hw": (u_max - u_min) * 0.5,
        u"hh": (v_max - v_min) * 0.5,
        u"frozen": reason is not None,
        u"reason": reason,
        u"cat": category_id,
        u"cat_name": category_name,
        u"lead": None,
        u"elbow": None,
    }

    end, elbow = leader_geometry

    if end is not None:
        box[u"lead"] = to_uv(end, origin, right, up)

        if elbow is not None:
            elbow_u, elbow_v = to_uv(elbow, origin, right, up)
            box[u"elbow"] = (elbow_u - head_u, elbow_v - head_v)

    return box


def measure_boxes(doc, view, tags, movable_ids=None):
    u"""Габариты марок без выносок плюс геометрия самих выносок.

    ВЫЗЫВАТЬ ВНЕ ОТКРЫТОЙ ТРАНЗАКЦИИ.

    movable_ids — множество целых id марок, которые разрешено двигать.
    Остальные попадают в расчёт препятствиями. None — двигать можно все,
    кроме закреплённых и сгруппированных.
    """
    origin, right, up = view_axes(view)

    boxes = []
    transaction = Transaction(doc, u"PP: замер марок")

    try:
        transaction.Start()

        # Сначала выноски: после выключения их геометрию уже не прочитать.
        geometry = {}

        for tag in tags:
            geometry[tag.Id.IntegerValue] = _leader_geometry(doc, tag, view)

        changed = False

        for tag in tags:
            try:
                if tag.HasLeader:
                    tag.HasLeader = False
                    changed = True
            except Exception:
                pass

        if changed:
            doc.Regenerate()

        for tag in tags:
            box = _make_box(
                tag, view, origin, right, up, movable_ids,
                geometry.get(tag.Id.IntegerValue, (None, None))
            )

            if box is not None:
                boxes.append(box)
    finally:
        try:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
        except Exception:
            pass

    return boxes


def size_summary(boxes, view):
    u"""Замеренный габарит марки в миллиметрах НА ЛИСТЕ.

    Диагностика для окна: если высота заметно больше видимого текста, значит в
    семействе марки есть пустая строка или подчёркивание, и они тоже занимают
    место. Без этой строки непонятно, почему марки разъезжаются так далеко.
    """
    if not boxes:
        return None

    widths = []
    heights = []

    for box in boxes:
        widths.append(ft_to_mm(box[u"hw"] * 2.0, view))
        heights.append(ft_to_mm(box[u"hh"] * 2.0, view))

    count = float(len(boxes))

    return {
        u"avg_w": sum(widths) / count,
        u"avg_h": sum(heights) / count,
        u"max_w": max(widths),
        u"max_h": max(heights),
    }


# ======================================================================
#  Геометрия плоскости вида. Про Revit не знает ничего
# ======================================================================

def leader_segments(box, position):
    u"""Выноска марки в текущем положении: список отрезков или пустой список.

    Начинается выноска не в точке головы, а у КРАЯ марки со стороны элемента —
    именно так её рисует Revit. Если считать от головы, отрезок проходит сквозь
    собственный текст марки, и проверка «выноска сквозь чужой текст» начинает
    врать на каждом столбике марок.

    Конец выноски стоит на элементе и не двигается, изгиб едет вместе с
    головой — так же, как это делает apply_moves.
    """
    end = box.get(u"lead")

    if end is None:
        return []

    center_u = position[0] + box[u"cu"]

    if end[0] >= center_u:
        start = (center_u + box[u"hw"], position[1])
    else:
        start = (center_u - box[u"hw"], position[1])

    elbow = box.get(u"elbow")

    if elbow is None:
        return [(start, end)]

    bend = (position[0] + elbow[0], position[1] + elbow[1])

    return [(start, bend), (bend, end)]


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(a, b, c, d):
    u"""Отрезки ab и cd пересекаются. Касание концами не считаем: две марки
    одного элемента приходят в одну точку, и это не ошибка оформления."""
    d1 = _orient(c, d, a)
    d2 = _orient(c, d, b)
    d3 = _orient(a, b, c)
    d4 = _orient(a, b, d)

    return ((d1 > 0.0) != (d2 > 0.0)) and ((d3 > 0.0) != (d4 > 0.0))


def leaders_cross(segments_a, segments_b):
    for a, b in segments_a:
        for c, d in segments_b:
            if _segments_cross(a, b, c, d):
                return True

    return False


def _clip_band(point_a, point_b, axis, low, high):
    u"""Часть отрезка внутри полосы low..high по оси axis.

    Возврат — диапазон второй координаты у этой части, либо None, если отрезок
    в полосу не попадает.
    """
    first = point_a[axis]
    second = point_a[1 - axis]
    last = point_b[axis]
    other = point_b[1 - axis]

    if first > last:
        first, last = last, first
        second, other = other, second

    if last < low or first > high:
        return None

    span = last - first

    if abs(span) < 1e-12:
        return min(second, other), max(second, other)

    start = max(0.0, (low - first) / span)
    stop = min(1.0, (high - first) / span)

    if stop < start:
        return None

    value_a = second + (other - second) * start
    value_b = second + (other - second) * stop

    if value_a > value_b:
        value_a, value_b = value_b, value_a

    return value_a, value_b


def _box_bounds(box, position, gap):
    center_u = position[0] + box[u"cu"]
    center_v = position[1] + box[u"cv"]

    return (
        center_u - box[u"hw"] - gap, center_u + box[u"hw"] + gap,
        center_v - box[u"hh"] - gap, center_v + box[u"hh"] + gap
    )


def segment_hits_box(box, position, segment, gap=0.0):
    u"""Отрезок задевает прямоугольник марки (выноска идёт сквозь чужой текст)."""
    u_low, u_high, v_low, v_high = _box_bounds(box, position, gap)

    band = _clip_band(segment[0], segment[1], 0, u_low, u_high)

    if band is None:
        return False

    return not (band[1] < v_low or band[0] > v_high)


def segment_push(box, position, segment, gap, mode):
    u"""Куда сдвинуть марку, чтобы сойти с чужой выноски. None — не задевает.

    Возврат: (ось, величина со знаком). Ось 0 — вдоль u, 1 — вдоль v.
    """
    u_low, u_high, v_low, v_high = _box_bounds(box, position, gap)

    vertical = _clip_band(segment[0], segment[1], 0, u_low, u_high)

    if vertical is None or vertical[1] < v_low or vertical[0] > v_high:
        return None

    up_shift = vertical[1] - v_low
    down_shift = vertical[0] - v_high

    best_v = up_shift if abs(up_shift) <= abs(down_shift) else down_shift

    if mode == MODE_VERTICAL:
        return 1, best_v

    horizontal = _clip_band(segment[0], segment[1], 1, v_low, v_high)

    if horizontal is None:
        return 1, best_v

    right_shift = horizontal[1] - u_low
    left_shift = horizontal[0] - u_high

    best_u = right_shift if abs(right_shift) <= abs(left_shift) else left_shift

    if mode == MODE_HORIZONTAL:
        return 0, best_u

    if abs(best_u) <= abs(best_v):
        return 0, best_u

    return 1, best_v


# ======================================================================
#  Поиск соседей
# ======================================================================

def _cell_size(boxes, gap):
    u"""Сторона ячейки сетки: самый крупный габарит плюс зазор."""
    size = 0.0

    for box in boxes:
        size = max(size, box[u"hw"] * 2.0, box[u"hh"] * 2.0)

    size = size + gap

    if size <= 1e-9:
        return 1.0

    return size


#: Половина окрестности: вторая половина даст те же пары в обратном порядке.
_NEIGHBOURS = ((0, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def _build_grid(boxes, positions, cell):
    grid = {}

    for index, box in enumerate(boxes):
        center_u = positions[index][0] + box[u"cu"]
        center_v = positions[index][1] + box[u"cv"]

        key = (int(math.floor(center_u / cell)), int(math.floor(center_v / cell)))

        if key in grid:
            grid[key].append(index)
        else:
            grid[key] = [index]

    return grid


def _candidate_pairs(boxes, positions, gap):
    u"""Пары, которые вообще могут пересечься. Перебор N**2 не годится:
    на плане бывает 500 марок, а решатель делает десятки проходов."""
    cell = _cell_size(boxes, gap)
    grid = _build_grid(boxes, positions, cell)

    pairs = []

    for key in grid:
        own = grid[key]

        for shift_u, shift_v in _NEIGHBOURS:
            other_key = (key[0] + shift_u, key[1] + shift_v)

            if other_key not in grid:
                continue

            other = grid[other_key]

            if other_key == key:
                for i in range(len(own)):
                    for j in range(i + 1, len(own)):
                        pairs.append((own[i], own[j]))
            else:
                for i in own:
                    for j in other:
                        pairs.append((i, j))

    return pairs


def _neighbour_map(boxes, positions, radius):
    u"""Ближайшие соседи каждой марки. Список обрезан: на плотном плане в
    радиус попадают десятки марок, и перестановка начинает тормозить."""
    cell = radius if radius > 1e-9 else 1.0
    grid = _build_grid(boxes, positions, cell)

    result = {}

    for index in range(len(boxes)):
        center_u = positions[index][0] + boxes[index][u"cu"]
        center_v = positions[index][1] + boxes[index][u"cv"]

        key = (int(math.floor(center_u / cell)), int(math.floor(center_v / cell)))

        found = []

        for shift_u in (-1, 0, 1):
            for shift_v in (-1, 0, 1):
                other_key = (key[0] + shift_u, key[1] + shift_v)

                if other_key not in grid:
                    continue

                for other in grid[other_key]:
                    if other == index:
                        continue

                    other_u = positions[other][0] + boxes[other][u"cu"]
                    other_v = positions[other][1] + boxes[other][u"cv"]

                    distance = (other_u - center_u) ** 2 + (other_v - center_v) ** 2
                    found.append((distance, other))

        found.sort()
        result[index] = [item[1] for item in found[:MAX_NEIGHBOURS]]

    return result


def _cell_keys(u_low, u_high, v_low, v_high, cell):
    keys = []

    first_u = int(math.floor(u_low / cell))
    last_u = int(math.floor(u_high / cell))
    first_v = int(math.floor(v_low / cell))
    last_v = int(math.floor(v_high / cell))

    for cell_u in range(first_u, last_u + 1):
        for cell_v in range(first_v, last_v + 1):
            keys.append((cell_u, cell_v))

    return keys


def _leader_index(boxes, positions):
    u"""Разбивка выносок и прямоугольников по общей сетке.

    Выноска бывает длинной, поэтому сетки по головам марок здесь мало: марку
    задевает выноска, чья голова стоит совсем в другом конце плана. Без такой
    разбивки проверка «выноска сквозь текст» превращается в перебор N**2,
    а её зовёт каждый пересчёт предпросмотра.

    Сторона ячейки — компромисс: мельче, и длинная выноска попадёт в сотни
    ячеек; крупнее, и в одну ячейку сползутся все марки плана.
    """
    segments = {}
    spans = {}
    longest = 0.0

    for index, box in enumerate(boxes):
        found = leader_segments(box, positions[index])

        if not found:
            continue

        segments[index] = found

        us = []
        vs = []

        for start, stop in found:
            us.append(start[0])
            us.append(stop[0])
            vs.append(start[1])
            vs.append(stop[1])

        span = (min(us), max(us), min(vs), max(vs))
        spans[index] = span

        longest = max(longest, span[1] - span[0], span[3] - span[2])

    if not spans:
        return None

    cell = max(_cell_size(boxes, 0.0) * 4.0, longest / 8.0)

    if cell <= 1e-9:
        cell = 1.0

    leader_cells = {}
    leader_grid = {}

    for index in spans:
        u_low, u_high, v_low, v_high = spans[index]
        keys = _cell_keys(u_low, u_high, v_low, v_high, cell)

        leader_cells[index] = keys

        for key in keys:
            if key in leader_grid:
                leader_grid[key].append(index)
            else:
                leader_grid[key] = [index]

    box_grid = {}

    for index, box in enumerate(boxes):
        u_low, u_high, v_low, v_high = _box_bounds(box, positions[index], 0.0)

        for key in _cell_keys(u_low, u_high, v_low, v_high, cell):
            if key in box_grid:
                box_grid[key].append(index)
            else:
                box_grid[key] = [index]

    return {
        u"segments": segments,
        u"leader_cells": leader_cells,
        u"leader_grid": leader_grid,
        u"box_grid": box_grid,
    }


def _leader_pairs(index_data):
    u"""Пары марок, чьи выноски могут пересечься."""
    pairs = set()

    for key in index_data[u"leader_grid"]:
        own = index_data[u"leader_grid"][key]

        for i in range(len(own)):
            for j in range(i + 1, len(own)):
                first, second = own[i], own[j]

                if first > second:
                    first, second = second, first

                pairs.add((first, second))

    return pairs


def _boxes_near_leader(index_data, owner):
    u"""Прямоугольники, которые вообще может задеть выноска этой марки."""
    found = set()
    box_grid = index_data[u"box_grid"]

    for key in index_data[u"leader_cells"].get(owner, ()):
        if key in box_grid:
            found.update(box_grid[key])

    found.discard(owner)

    return found


# ======================================================================
#  Оценка раскладки
# ======================================================================

def _overlap(box_a, pos_a, box_b, pos_b, gap):
    u"""Перекрытие двух прямоугольников с учётом зазора. None — не пересекаются."""
    delta_u = (pos_a[0] + box_a[u"cu"]) - (pos_b[0] + box_b[u"cu"])
    delta_v = (pos_a[1] + box_a[u"cv"]) - (pos_b[1] + box_b[u"cv"])

    over_u = (box_a[u"hw"] + box_b[u"hw"] + gap) - abs(delta_u)

    if over_u <= 0.0:
        return None

    over_v = (box_a[u"hh"] + box_b[u"hh"] + gap) - abs(delta_v)

    if over_v <= 0.0:
        return None

    return delta_u, delta_v, over_u, over_v


def count_overlaps(boxes, positions, gap):
    u"""Пересекающиеся пары прямоугольников и участвующие в них марки."""
    pairs = 0
    involved = set()

    for i, j in _candidate_pairs(boxes, positions, gap):
        if _overlap(boxes[i], positions[i], boxes[j], positions[j], gap) is None:
            continue

        pairs += 1
        involved.add(boxes[i][u"id"])
        involved.add(boxes[j][u"id"])

    return pairs, involved


def count_leader_problems(boxes, positions, index_data=None):
    u"""Перекрестившиеся выноски и выноски сквозь чужой текст."""
    if index_data is None:
        index_data = _leader_index(boxes, positions)

    if index_data is None:
        return 0, 0, set()

    segments = index_data[u"segments"]

    crossings = 0
    through = 0
    involved = set()

    for i, j in _leader_pairs(index_data):
        if leaders_cross(segments[i], segments[j]):
            crossings += 1
            involved.add(boxes[i][u"id"])
            involved.add(boxes[j][u"id"])

    for owner in segments:
        for other in _boxes_near_leader(index_data, owner):
            for segment in segments[owner]:
                if segment_hits_box(boxes[other], positions[other], segment):
                    through += 1
                    involved.add(boxes[owner][u"id"])
                    involved.add(boxes[other][u"id"])
                    break

    return crossings, through, involved


def _score(overlaps, crossings, through):
    return (overlaps * WEIGHT_OVERLAP
            + through * WEIGHT_THROUGH
            + crossings * WEIGHT_CROSS)


def evaluate(boxes, positions, gap, leaders=True):
    u"""Сколько бед в раскладке. Один словарь на все метрики."""
    overlaps, overlap_tags = count_overlaps(boxes, positions, gap)

    if leaders:
        crossings, through, leader_tags = count_leader_problems(boxes, positions)
    else:
        crossings, through, leader_tags = 0, 0, set()

    return {
        u"overlaps": overlaps,
        u"overlap_tags": overlap_tags,
        u"crossings": crossings,
        u"through": through,
        u"leader_tags": leader_tags,
        u"score": _score(overlaps, crossings, through),
    }


# ======================================================================
#  Решатель
# ======================================================================

def _clamp_to_anchor(position, anchor, max_offset):
    u"""Не дать марке уйти от исходного места дальше разрешённого."""
    if max_offset <= 0.0:
        return

    off_u = position[0] - anchor[0]
    off_v = position[1] - anchor[1]

    distance = math.sqrt(off_u * off_u + off_v * off_v)

    if distance <= max_offset:
        return

    factor = max_offset / distance

    position[0] = anchor[0] + off_u * factor
    position[1] = anchor[1] + off_v * factor


def _within_offset(position, anchor, max_offset):
    if max_offset <= 0.0:
        return True

    off_u = position[0] - anchor[0]
    off_v = position[1] - anchor[1]

    return math.sqrt(off_u * off_u + off_v * off_v) <= max_offset + 1e-9


def _relax(boxes, positions, anchors, movable, gap, max_offset, mode,
           iterations, damping, pull, obstacles=None):
    u"""Развести прямоугольники. Поправка каждой пары применяется сразу
    (Гаусс-Зейдель): это сходится в разы быстрее накопления поправок, а в
    стопке одинаковых марок ещё и разводит их по сторонам, вместо того чтобы
    дрожать на месте.

    obstacles — пары (индекс марки, индекс хозяина выноски): марку выталкивает
    с чужой выноски. Набор считается снаружи и за проход не меняется.
    """
    count = len(boxes)
    pull_until = int(iterations) // 2

    for step in range(int(iterations)):
        pairs = _candidate_pairs(boxes, positions, gap)

        touched = [False] * count
        travelled = 0.0

        for i, j in pairs:
            result = _overlap(boxes[i], positions[i], boxes[j], positions[j], gap)

            if result is None:
                continue

            delta_u, delta_v, over_u, over_v = result

            touched[i] = True
            touched[j] = True

            if not movable[i] and not movable[j]:
                continue

            if mode == MODE_VERTICAL:
                axis, amount, delta = 1, over_v, delta_v
            elif mode == MODE_HORIZONTAL:
                axis, amount, delta = 0, over_u, delta_u
            elif over_u <= over_v:
                axis, amount, delta = 0, over_u, delta_u
            else:
                axis, amount, delta = 1, over_v, delta_v

            sign = (1.0 if delta >= 0.0 else -1.0) * amount * damping

            if movable[i] and movable[j]:
                shift_i, shift_j = sign * 0.5, -sign * 0.5
            elif movable[i]:
                shift_i, shift_j = sign, 0.0
            else:
                shift_i, shift_j = 0.0, -sign

            if shift_i:
                positions[i][axis] += shift_i
                _clamp_to_anchor(positions[i], anchors[i], max_offset)

            if shift_j:
                positions[j][axis] += shift_j
                _clamp_to_anchor(positions[j], anchors[j], max_offset)

            travelled += abs(shift_i) + abs(shift_j)

        for index, owner in (obstacles or ()):
            if not movable[index]:
                continue

            for segment in leader_segments(boxes[owner], positions[owner]):
                push = segment_push(boxes[index], positions[index], segment, gap, mode)

                if push is None:
                    continue

                axis, amount = push
                amount = amount * damping

                positions[index][axis] += amount
                _clamp_to_anchor(positions[index], anchors[index], max_offset)

                touched[index] = True
                travelled += abs(amount)

        if step < pull_until and pull > 0.0:
            # Марка, которую в этом проходе никто не толкал, потихоньку
            # возвращается на исходное место: сосед мог уже уехать.
            for index in range(count):
                if touched[index] or not movable[index]:
                    continue

                back_u = (anchors[index][0] - positions[index][0]) * pull
                back_v = (anchors[index][1] - positions[index][1]) * pull

                positions[index][0] += back_u
                positions[index][1] += back_v

                travelled += abs(back_u) + abs(back_v)

        if travelled < gap * 0.005:
            break


def _pair_penalty(boxes, positions, i, j, gap, segments):
    u"""Беды ровно между двумя марками: наложение, крест выносок, выноска
    сквозь чужой текст.

    Возврат: (суммарный вес, число крестов). Кресты возвращаются отдельно,
    чтобы разрешать перестановку, которая распутывает выноски, ничего при этом
    не ухудшая: на столбике марок такие перестановки почти всегда идут вровень
    по весу и без этого молча отклонялись бы.
    """
    penalty = 0.0
    crossings = 0

    if _overlap(boxes[i], positions[i], boxes[j], positions[j], gap) is not None:
        penalty += WEIGHT_OVERLAP

    first = segments(i)
    second = segments(j)

    if first and second and leaders_cross(first, second):
        penalty += WEIGHT_CROSS
        crossings += 1

    for segment in first:
        if segment_hits_box(boxes[j], positions[j], segment):
            penalty += WEIGHT_THROUGH
            break

    for segment in second:
        if segment_hits_box(boxes[i], positions[i], segment):
            penalty += WEIGHT_THROUGH
            break

    return penalty, crossings


def _local_penalty(boxes, positions, index, scope, gap, segments):
    u"""Сколько бед вокруг одной марки. Считается по узкой окрестности —
    перестановка меняет картину только рядом."""
    penalty = 0.0
    crossings = 0

    for other in scope:
        if other == index:
            continue

        weight, crossed = _pair_penalty(
            boxes, positions, index, other, gap, segments)

        penalty += weight
        crossings += crossed

    return penalty, crossings


def _untangle(boxes, positions, anchors, movable, gap, max_offset, passes=2):
    u"""Разобрать перекрестившиеся выноски перестановкой марок местами.

    Если две выноски пересеклись, значит порядок марок не совпадает с порядком
    их элементов. Раздвижкой это не лечится — марки надо поменять местами.
    Меняем только тогда, когда обеим хватает разрешённого смещения и когда
    после перестановки бед вокруг стало меньше.

    Перестановка примеряется только к парам, у которых беда есть прямо сейчас,
    и общее число примерок ограничено: без этих двух оговорок предпросмотр
    на плотном плане начинает заметно думать.
    """
    swapped = 0
    radius = max(max_offset, _cell_size(boxes, gap))

    for _ in range(int(passes)):
        neighbours = _neighbour_map(boxes, positions, radius)
        cache = {}

        def segments(index):
            if index not in cache:
                cache[index] = leader_segments(boxes[index], positions[index])

            return cache[index]

        changed = False
        attempts = 0

        for i in range(len(boxes)):
            if not movable[i] or attempts >= MAX_SWAP_ATTEMPTS:
                break

            for j in neighbours[i]:
                if j <= i or not movable[j]:
                    continue

                if not boxes[i].get(u"lead") and not boxes[j].get(u"lead"):
                    continue

                if _pair_penalty(boxes, positions, i, j, gap, segments)[0] <= 0.0:
                    continue

                if not _within_offset(positions[j], anchors[i], max_offset):
                    continue

                if not _within_offset(positions[i], anchors[j], max_offset):
                    continue

                attempts += 1

                if attempts >= MAX_SWAP_ATTEMPTS:
                    break

                scope = set(neighbours[i]) | set(neighbours[j])
                scope.discard(i)
                scope.discard(j)
                scope = list(scope) + [i, j]

                weight_i, cross_i = _local_penalty(
                    boxes, positions, i, scope, gap, segments)
                weight_j, cross_j = _local_penalty(
                    boxes, positions, j, scope, gap, segments)

                before = weight_i + weight_j
                before_cross = cross_i + cross_j

                positions[i], positions[j] = positions[j], positions[i]
                cache.pop(i, None)
                cache.pop(j, None)

                weight_i, cross_i = _local_penalty(
                    boxes, positions, i, scope, gap, segments)
                weight_j, cross_j = _local_penalty(
                    boxes, positions, j, scope, gap, segments)

                after = weight_i + weight_j
                after_cross = cross_i + cross_j

                better = after < before - 1e-9

                if not better and abs(after - before) <= 1e-9:
                    better = after_cross < before_cross

                if better:
                    swapped += 1
                    changed = True
                else:
                    positions[i], positions[j] = positions[j], positions[i]
                    cache.pop(i, None)
                    cache.pop(j, None)

        if not changed:
            break

    return swapped


def _leader_obstacles(boxes, positions, movable, gap):
    u"""Пары «марка — чужая выноска, которая её задевает»."""
    index_data = _leader_index(boxes, positions)

    if index_data is None:
        return []

    segments = index_data[u"segments"]
    obstacles = []

    for owner in segments:
        for index in _boxes_near_leader(index_data, owner):
            if not movable[index]:
                continue

            for segment in segments[owner]:
                if segment_hits_box(boxes[index], positions[index], segment, gap):
                    obstacles.append((index, owner))
                    break

    return obstacles


def solve(boxes, gap, max_offset, mode=MODE_AUTO, iterations=60,
          damping=1.0, pull=0.05, leaders=True):
    u"""Разложить марки без наложений и без перекрестившихся выносок.

    Всё в футах модели.

    gap        — требуемый просвет между марками;
    max_offset — насколько далеко марке разрешено уйти от исходного места;
    mode       — MODE_AUTO (по меньшему перекрытию), MODE_VERTICAL,
                 MODE_HORIZONTAL;
    leaders    — разбирать выноски: перестановка марок местами плюс
                 выталкивание марки с чужой выноски.

    Порядок работы: развели прямоугольники → разобрали выноски перестановкой →
    развели ещё раз, уже считая чужие выноски препятствиями. Два круга: после
    перестановки появляются новые соседи.

    Раздвигаем с запасом (work_gap), а считаем наложения по точному зазору:
    иначе марка, не дошедшая до цели на тысячную долю, остаётся «наложенной».

    Ни один круг не может ухудшить картину: лучшая раскладка запоминается и
    возвращается, даже если дальнейшие проходы разошлись хуже.

    Возврат: словарь с ключами moves, overlaps_before/after, crossings_before/
    after, through_before/after, moved, swapped, unresolved.
    """
    positions = [[box[u"hu"], box[u"hv"]] for box in boxes]
    anchors = [(box[u"hu"], box[u"hv"]) for box in boxes]
    movable = [not box[u"frozen"] for box in boxes]

    start = evaluate(boxes, positions, gap, leaders)

    best = [list(item) for item in positions]
    best_state = start

    if start[u"score"] > 0.0:
        work_gap = gap + max(gap * 0.05, 1e-4)

        _relax(boxes, positions, anchors, movable, work_gap, max_offset, mode,
               iterations, damping, pull)

        current = evaluate(boxes, positions, gap, leaders)

        if current[u"score"] < best_state[u"score"]:
            best_state = current
            best = [list(item) for item in positions]

        swapped = 0

        if leaders:
            for _ in range(2):
                swapped += _untangle(boxes, positions, anchors, movable,
                                     work_gap, max_offset)

                obstacles = _leader_obstacles(boxes, positions, movable, work_gap)

                _relax(boxes, positions, anchors, movable, work_gap, max_offset,
                       mode, max(int(iterations) // 3, 6), damping, 0.0,
                       obstacles)

                current = evaluate(boxes, positions, gap, leaders)

                if current[u"score"] < best_state[u"score"]:
                    best_state = current
                    best = [list(item) for item in positions]
    else:
        swapped = 0

    positions = best
    final = best_state

    moves = {}

    for index, box in enumerate(boxes):
        if not movable[index]:
            continue

        move_u = positions[index][0] - anchors[index][0]
        move_v = positions[index][1] - anchors[index][1]

        if abs(move_u) < MIN_MOVE_FT and abs(move_v) < MIN_MOVE_FT:
            continue

        moves[box[u"id"]] = (move_u, move_v)

    unresolved = set(final[u"overlap_tags"]) | set(final[u"leader_tags"])

    if not moves:
        # Лучшей оказалась исходная раскладка: перестановки были примерены и
        # отменены, отчитываться о них нечем.
        swapped = 0

    return {
        u"moves": moves,
        u"moved": len(moves),
        u"swapped": swapped,

        u"overlaps_before": start[u"overlaps"],
        u"overlaps_after": final[u"overlaps"],
        u"crossings_before": start[u"crossings"],
        u"crossings_after": final[u"crossings"],
        u"through_before": start[u"through"],
        u"through_after": final[u"through"],

        u"tags_before": len(set(start[u"overlap_tags"]) | set(start[u"leader_tags"])),
        u"tags_after": len(unresolved),

        u"unresolved": sorted(unresolved),
    }


# ======================================================================
#  Применение к модели
# ======================================================================

def _saved_elbows(tag):
    u"""Изгибы выносок ДО перемещения головы.

    После перестановки головы каждый изгиб сдвигается на тот же вектор — так
    же, как при перетаскивании марки мышью. Иначе изгиб остаётся висеть в
    старой точке и выноска ломается.
    """
    try:
        references = tag.GetTaggedReferences()
    except Exception:
        return []

    saved = []

    for reference in references:
        elbow = pp_leaders.safe_get_leader_elbow(tag, reference)

        if elbow is not None:
            saved.append((reference, elbow))

    return saved


def apply_moves(doc, view, boxes, moves, options=None):
    u"""Переставить головы марок. ВЫЗЫВАТЬ ВНУТРИ УЖЕ ОТКРЫТОЙ ТРАНЗАКЦИИ.

    Возврат: словарь с ключами applied, leaders, failed (список сообщений).
    """
    settings = dict(DEFAULT_OPTIONS)

    if options:
        settings.update(options)

    right = view.RightDirection
    up = view.UpDirection

    enable_leader = bool(settings.get(u"enable_leader"))
    rebuild_elbow = bool(settings.get(u"rebuild_elbow"))
    threshold = mm_to_ft(settings.get(u"leader_threshold_mm", 0.0), view)

    names = {}

    for box in boxes:
        names[box[u"id"]] = box.get(u"cat_name")

    applied = 0
    leaders = 0
    failed = []

    for tag_id in sorted(moves.keys()):
        move_u, move_v = moves[tag_id]

        tag = doc.GetElement(ElementId(tag_id))

        if tag is None:
            continue

        try:
            head = tag.TagHeadPosition
        except Exception:
            failed.append(u"{}: не удалось прочитать положение марки".format(
                names.get(tag_id, u"Марка")))
            continue

        elbows = _saved_elbows(tag) if rebuild_elbow else []

        try:
            tag.TagHeadPosition = to_xyz(head, right, up, move_u, move_v)
            applied += 1
        except Exception as error:
            failed.append(u"{} (id {}): {}".format(
                names.get(tag_id, u"Марка"), tag_id, unicode(error)))
            continue

        distance = math.sqrt(move_u * move_u + move_v * move_v)

        if enable_leader and distance >= threshold:
            try:
                if not tag.HasLeader:
                    tag.HasLeader = True
                    leaders += 1
            except Exception:
                pass

        for reference, elbow in elbows:
            if elbow is None:
                continue

            pp_leaders.safe_set_leader_elbow(
                tag, reference, to_xyz(elbow, right, up, move_u, move_v))

    return {
        u"applied": applied,
        u"leaders": leaders,
        u"failed": failed,
    }


def spread_tags(doc, view, tags=None, options=None, movable_ids=None):
    u"""Всё вместе: замер, расчёт и перестановка в собственной транзакции.

    Для встраивания в другие кнопки: своя транзакция там уже должна быть
    закрыта. Возврат — объединённый словарь solve и apply_moves.
    """
    settings = dict(DEFAULT_OPTIONS)

    if options:
        settings.update(options)

    if tags is None:
        tags = collect_tags(doc, view)

    empty = {
        u"moves": {}, u"moved": 0, u"swapped": 0,
        u"overlaps_before": 0, u"overlaps_after": 0,
        u"crossings_before": 0, u"crossings_after": 0,
        u"through_before": 0, u"through_after": 0,
        u"tags_before": 0, u"tags_after": 0,
        u"unresolved": [], u"applied": 0, u"leaders": 0, u"failed": [],
        u"boxes": [],
    }

    if not tags:
        return empty

    boxes = measure_boxes(doc, view, tags, movable_ids)

    if not boxes:
        return empty

    result = solve(
        boxes,
        mm_to_ft(settings.get(u"gap_mm"), view),
        mm_to_ft(settings.get(u"max_offset_mm"), view),
        settings.get(u"mode", MODE_AUTO),
        settings.get(u"iterations", 60),
        leaders=bool(settings.get(u"leaders", True))
    )

    result[u"boxes"] = boxes

    if not result[u"moves"]:
        result[u"applied"] = 0
        result[u"leaders"] = 0
        result[u"failed"] = []

        return result

    transaction = Transaction(doc, u"PP: раздвинуть марки")

    try:
        transaction.Start()
        result.update(apply_moves(doc, view, boxes, result[u"moves"], settings))
        transaction.Commit()
    except Exception:
        try:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
        except Exception:
            pass

        raise

    return result
