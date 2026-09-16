# -*- coding: utf-8 -*-
u"""Раскладка видов на листе — чистая геометрия, без Revit.

Всё в миллиметрах листа. Прямоугольник — (x0, y0, x1, y1), ось Y вверх,
как в координатах листа Revit.

    rows, placed = pack_rows(sizes, width, height, gap)
    positions = place_rows(rows, sizes, area, gap)

Вынесено из script.py отдельным модулем, чтобы проверять раскладку обычным
python без Revit.
"""

EPS = 0.01  # мм — погрешность сравнения габаритов


def block_height(rows, gap):
    u"""Высота блока рядов: сумма высот рядов плюс зазоры между ними."""
    if not rows:
        return 0.0

    return sum(row[2] for row in rows) + gap * (len(rows) - 1)


def pack_rows(sizes, width, height, gap):
    u"""Уложить виды рядами строго по порядку.

    sizes  — [(ширина, высота), ...] в порядке размещения
    width, height — рабочая область листа
    gap    — зазор между видами и между рядами

    Ряд заполняется слева направо, пока помещается по ширине; не влез — новый
    ряд. Как только очередной вид не помещается по высоте, раскладка
    останавливается: порядок видов не переставляется.

    Возврат: (rows, placed)
      rows   — [[индексы], ширина ряда, высота ряда], ...
      placed — сколько первых видов поместилось
    """
    rows = []

    for index, (w, h) in enumerate(sizes):
        if w > width + EPS or h > height + EPS:
            break

        if rows and rows[-1][1] + gap + w <= width + EPS:
            last = rows[-1]
            trial = rows[:-1] + [[last[0] + [index],
                                  last[1] + gap + w,
                                  max(last[2], h)]]
        else:
            trial = rows + [[[index], w, h]]

        if block_height(trial, gap) > height + EPS:
            break

        rows = trial

    placed = sum(len(row[0]) for row in rows)
    return rows, placed


def place_rows(rows, sizes, area, gap):
    u"""Координаты левого нижнего угла каждого вида.

    Каждый ряд центрируется по горизонтали, весь блок — по вертикали; внутри
    ряда виды выравниваются по середине высоты ряда.

    Возврат: {индекс: (x0, y0)}
    """
    ax0, ay0, ax1, ay1 = area
    top = ay1 - ((ay1 - ay0) - block_height(rows, gap)) / 2.0
    result = {}

    for indices, row_w, row_h in rows:
        x = ax0 + ((ax1 - ax0) - row_w) / 2.0
        middle = top - row_h / 2.0

        for index in indices:
            w, h = sizes[index]
            result[index] = (x, middle - h / 2.0)
            x += w + gap

        top -= row_h + gap

    return result


def single_row(sizes, index=0):
    u"""Один вид отдельным рядом — когда он не влезает ни в какой формат."""
    w, h = sizes[index]
    return [[[index], w, h]]
