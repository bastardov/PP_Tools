# -*- coding: utf-8 -*-
u"""Раскладка видов на листе — чистая геометрия, без Revit.

Всё в миллиметрах листа. Прямоугольник — (x0, y0, x1, y1), ось Y вверх,
как в координатах листа Revit.

Вид описывается тройкой (ширина, высота, below):

* ширина, высота — габарит вида вместе с заголовком;
* below — сколько миллиметров от низа габарита до «линии выравнивания».

Внутри ряда виды ставятся так, чтобы их линии выравнивания совпали. Что это за
линия, решает script.py:

* по середине (below = высота / 2) — виды центрируются по высоте ряда;
* по низу рамки вида, когда заголовок под видом — заголовки всего ряда
  встают на одну высоту («заголовки по рядам»);
* по верху рамки вида, когда заголовок над видом — то же сверху.

Пара (ширина, высота) без below — выравнивание по середине.

    rows, placed = pack_rows(items, width, height, gap)
    positions = place_rows(rows, items, area, gap)

Вынесено из script.py отдельным модулем, чтобы проверять раскладку обычным
python без Revit.
"""

EPS = 0.01  # мм — погрешность сравнения габаритов


def _item(item):
    u"""(ширина, высота, below, above) — above = высота над линией."""
    if len(item) >= 3:
        w, h, below = item[0], item[1], item[2]
    else:
        w, h = item[0], item[1]
        below = h / 2.0

    return w, h, below, h - below


def _row(indices, items, width):
    below = max(_item(items[i])[2] for i in indices)
    above = max(_item(items[i])[3] for i in indices)
    return [list(indices), width, below + above, below, above]


def block_height(rows, gap):
    u"""Высота блока рядов: сумма высот рядов плюс зазоры между ними."""
    if not rows:
        return 0.0

    return sum(row[2] for row in rows) + gap * (len(rows) - 1)


def pack_rows(items, width, height, gap):
    u"""Уложить виды рядами строго по порядку.

    items  — [(ширина, высота[, below]), ...] в порядке размещения
    width, height — рабочая область листа
    gap    — зазор между видами и между рядами

    Ряд заполняется слева направо, пока помещается по ширине; не влез — новый
    ряд. Высота ряда учитывает выравнивание по линии: вид с заголовком снизу
    и вид повыше не просто «максимум высот», а сумма самых глубоких частей под
    линией и над ней. Как только очередной вид не помещается по высоте,
    раскладка останавливается: порядок видов не переставляется.

    Возврат: (rows, placed)
      rows   — [[индексы], ширина ряда, высота ряда, below, above], ...
      placed — сколько первых видов поместилось
    """
    rows = []

    for index, item in enumerate(items):
        w, h, _below, _above = _item(item)

        if w > width + EPS or h > height + EPS:
            break

        if rows and rows[-1][1] + gap + w <= width + EPS:
            last = rows[-1]
            trial = rows[:-1] + [_row(last[0] + [index], items,
                                      last[1] + gap + w)]
        else:
            trial = rows + [_row([index], items, w)]

        if block_height(trial, gap) > height + EPS:
            break

        rows = trial

    placed = sum(len(row[0]) for row in rows)
    return rows, placed


def place_rows(rows, items, area, gap):
    u"""Координаты левого нижнего угла габарита каждого вида.

    Каждый ряд центрируется по горизонтали, весь блок — по вертикали; внутри
    ряда виды совмещаются по линии выравнивания.

    Возврат: {индекс: (x0, y0)}
    """
    ax0, ay0, ax1, ay1 = area
    top = ay1 - ((ay1 - ay0) - block_height(rows, gap)) / 2.0
    result = {}

    for indices, row_w, row_h, _below, above in rows:
        x = ax0 + ((ax1 - ax0) - row_w) / 2.0
        line = top - above

        for index in indices:
            w, _h, below, _above = _item(items[index])
            result[index] = (x, line - below)
            x += w + gap

        top -= row_h + gap

    return result


def single_row(items, index=0):
    u"""Один вид отдельным рядом — когда он не влезает ни в какой формат."""
    return [_row([index], items, _item(items[index])[0])]
