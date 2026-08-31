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

Три вещи, которые важно помнить.

1. ``measure_boxes`` открывает СВОЮ транзакцию и откатывает её. Вызывать её,
   когда другая транзакция уже открыта, нельзя — Revit не разрешает вложенные.
   Инструмент, который сам расставляет марки, сначала закрывает свою
   транзакцию, и только потом зовёт раздвижку.

2. ``tag.get_BoundingBox(view)`` возвращает габарит ВМЕСТЕ с выноской, поэтому
   для поиска наложений он не годится. Замер идёт в транзакции-зонде: выноски
   временно выключаются, снимается чистый габарит текста, транзакция
   откатывается. Откат возвращает выноски, изгибы и LeaderEndCondition ровно
   как были — вручную настроенные выноски не страдают.

3. Считаем в плоскости вида: u вдоль view.RightDirection, v вдоль
   view.UpDirection. Поэтому инструмент одинаково работает на планах, разрезах
   и фасадах, а координата поперёк вида не трогается вообще.

``solve`` про Revit не знает — на вход идут словари, на выход словари. Это
позволяет окну инструмента пересчитывать предпросмотр на каждое изменение
параметров, не открывая транзакций.
"""

import math

from Autodesk.Revit.DB import (
    ElementId, FilteredElementCollector, IndependentTag, Transaction, XYZ
)

import pp_leaders


MM_PER_FT = 304.8

#: Смещение меньше этого (в футах модели) считаем нулевым и марку не трогаем.
MIN_MOVE_FT = 0.001

MODE_AUTO = u"auto"
MODE_VERTICAL = u"vertical"
MODE_HORIZONTAL = u"horizontal"

FROZEN_PINNED = u"закреплена"
FROZEN_GROUP = u"внутри группы"
FROZEN_SCOPE = u"вне области раздвижки"

DEFAULT_OPTIONS = {
    u"gap_mm": 1.5,
    u"max_offset_mm": 20.0,
    u"mode": MODE_AUTO,
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
#  Замер габаритов: транзакция-зонд с откатом
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


def _make_box(tag, view, origin, right, up, movable_ids):
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

    return {
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
    }


def measure_boxes(doc, view, tags, movable_ids=None):
    u"""Габариты марок без выносок. ВЫЗЫВАТЬ ВНЕ ОТКРЫТОЙ ТРАНЗАКЦИИ.

    movable_ids — множество целых id марок, которые разрешено двигать.
    Остальные попадают в расчёт препятствиями. None — двигать можно все,
    кроме закреплённых и сгруппированных.
    """
    origin, right, up = view_axes(view)

    boxes = []
    transaction = Transaction(doc, u"PP: замер марок")

    try:
        transaction.Start()

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
            box = _make_box(tag, view, origin, right, up, movable_ids)

            if box is not None:
                boxes.append(box)
    finally:
        try:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
        except Exception:
            pass

    return boxes


# ======================================================================
#  Решатель. Про Revit не знает ничего
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


def _candidate_pairs(boxes, positions, gap):
    u"""Пары, которые вообще могут пересечься. Перебор N**2 не годится:
    на плане бывает 500 марок, а решатель делает десятки проходов."""
    cell = _cell_size(boxes, gap)
    grid = {}

    for index, box in enumerate(boxes):
        center_u = positions[index][0] + box[u"cu"]
        center_v = positions[index][1] + box[u"cv"]

        key = (int(math.floor(center_u / cell)), int(math.floor(center_v / cell)))

        if key in grid:
            grid[key].append(index)
        else:
            grid[key] = [index]

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
    u"""Пересекающиеся пары и участвующие в них марки."""
    pairs = 0
    involved = set()

    for i, j in _candidate_pairs(boxes, positions, gap):
        if _overlap(boxes[i], positions[i], boxes[j], positions[j], gap) is None:
            continue

        pairs += 1
        involved.add(boxes[i][u"id"])
        involved.add(boxes[j][u"id"])

    return pairs, involved


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


def solve(boxes, gap, max_offset, mode=MODE_AUTO, iterations=60,
          damping=1.0, pull=0.05):
    u"""Разложить марки без наложений. Всё в футах модели.

    gap        — требуемый просвет между марками;
    max_offset — насколько далеко марке разрешено уйти от исходного места;
    mode       — MODE_AUTO (по меньшему перекрытию), MODE_VERTICAL,
                 MODE_HORIZONTAL.

    Проходы идут по Гауссу-Зейделю: поправка каждой пары применяется сразу.
    Это сходится в разы быстрее накопления поправок, а в стопке одинаковых
    марок ещё и разводит их по сторонам, вместо того чтобы дрожать на месте.

    Раздвигаем с запасом (work_gap), а считаем наложения по точному зазору:
    иначе марка, не дошедшая до цели на тысячную долю, остаётся «наложенной».

    Ни один проход не может ухудшить картину: лучшая раскладка запоминается и
    возвращается, даже если последующие проходы разошлись хуже.

    Возврат: словарь с ключами moves, overlaps_before, overlaps_after,
    moved, unresolved.
    """
    count = len(boxes)

    positions = [[box[u"hu"], box[u"hv"]] for box in boxes]
    anchors = [(box[u"hu"], box[u"hv"]) for box in boxes]
    movable = [not box[u"frozen"] for box in boxes]

    overlaps_before, involved_before = count_overlaps(boxes, positions, gap)

    work_gap = gap + max(gap * 0.05, 1e-4)

    best = [list(item) for item in positions]
    best_count = overlaps_before

    # Притяжение к исходному месту работает только в первой половине проходов:
    # ближе к концу оно мешало бы марке доехать до свободного места.
    pull_until = int(iterations) // 2

    if overlaps_before:
        for step in range(int(iterations)):
            pairs = _candidate_pairs(boxes, positions, work_gap)

            current = 0

            for i, j in pairs:
                if _overlap(boxes[i], positions[i], boxes[j], positions[j], gap):
                    current += 1

            if current < best_count:
                best_count = current
                best = [list(item) for item in positions]

            if current == 0:
                break

            touched = [False] * count
            travelled = 0.0

            for i, j in pairs:
                result = _overlap(
                    boxes[i], positions[i], boxes[j], positions[j], work_gap)

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

            if step < pull_until and pull > 0.0:
                for index in range(count):
                    if touched[index] or not movable[index]:
                        continue

                    back_u = (anchors[index][0] - positions[index][0]) * pull
                    back_v = (anchors[index][1] - positions[index][1]) * pull

                    positions[index][0] += back_u
                    positions[index][1] += back_v

                    travelled += abs(back_u) + abs(back_v)

            if travelled < work_gap * 0.005:
                break

    overlaps_after, involved_after = count_overlaps(boxes, positions, gap)

    # Не строго «лучше» — при равном счёте берём более раннюю раскладку: она
    # ближе к исходной, а значит марок сдвинуто меньше.
    if overlaps_after >= best_count:
        positions = best
        overlaps_after, involved_after = count_overlaps(boxes, positions, gap)

    moves = {}

    for index, box in enumerate(boxes):
        if not movable[index]:
            continue

        move_u = positions[index][0] - anchors[index][0]
        move_v = positions[index][1] - anchors[index][1]

        if abs(move_u) < MIN_MOVE_FT and abs(move_v) < MIN_MOVE_FT:
            continue

        moves[box[u"id"]] = (move_u, move_v)

    return {
        u"moves": moves,
        u"overlaps_before": overlaps_before,
        u"overlaps_after": overlaps_after,
        u"tags_before": len(involved_before),
        u"tags_after": len(involved_after),
        u"moved": len(moves),
        u"unresolved": sorted(involved_after),
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

    if not tags:
        return {
            u"moves": {}, u"overlaps_before": 0, u"overlaps_after": 0,
            u"tags_before": 0, u"tags_after": 0, u"moved": 0,
            u"unresolved": [], u"applied": 0, u"leaders": 0, u"failed": [],
            u"boxes": [],
        }

    boxes = measure_boxes(doc, view, tags, movable_ids)

    result = solve(
        boxes,
        mm_to_ft(settings.get(u"gap_mm"), view),
        mm_to_ft(settings.get(u"max_offset_mm"), view),
        settings.get(u"mode", MODE_AUTO),
        settings.get(u"iterations", 40)
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
