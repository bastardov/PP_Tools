# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

u"""Раздвинуть марки — разводит наложившиеся марки на активном виде.

Порядок работы:

1. Собираем марки вида и замеряем их габариты без выносок (транзакция-зонд
   с откатом внутри pp_tag_layout.measure_boxes).
2. Показываем окно. Расчёт раскладки — чистая математика, поэтому окно
   пересчитывает предпросмотр на каждое изменение параметров само.
3. Применяем уже посчитанные смещения одной транзакцией: что пользователь
   видел в окне, то и получит.

Ядро вынесено в lib/pp_tag_layout.py, чтобы раздвижку можно было доклеить
галочкой к другим кнопкам панели «Марки».
"""

import os
import sys

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("System")

from System.Collections.Generic import List

from Autodesk.Revit.DB import ElementId, Transaction, View3D, ViewSheet

from pyrevit import script

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_tag_layout
import pp_spread_window


TITLE = u"Раздвинуть марки"


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

my_config = script.get_config()


def fail(message):
    u"""Скрипт плоский, общего try нет: показываем окно и выходим."""
    pp_wpf.show_report(message, title=u"Ничего не сделано", subtitle=TITLE,
                       is_error=True)
    sys.exit(0)


def word(count, one, few, many):
    count = abs(int(count))

    if count % 100 in (11, 12, 13, 14):
        return many

    last = count % 10

    if last == 1:
        return one

    if last in (2, 3, 4):
        return few

    return many


def tags_word(count):
    return word(count, u"марка", u"марки", u"марок")


# ======================================================================
#  Настройки окна между запусками
# ======================================================================

def load_saved():
    saved = {}

    for key, default in (
        (u"scope", u"all"),
        (u"mode", u"auto"),
    ):
        try:
            saved[key] = my_config.get_option(str(key), default)
        except Exception:
            saved[key] = default

    for key, default in (
        (u"gap_mm", 1.5),
        (u"offset_mm", 20.0),
        (u"leader_mm", 5.0),
    ):
        try:
            saved[key] = float(my_config.get_option(str(key), default))
        except Exception:
            saved[key] = default

    for key, default in (
        (u"tangle", True),
        (u"enable_leader", True),
        (u"rebuild_elbow", True),
    ):
        try:
            saved[key] = bool(my_config.get_option(str(key), default))
        except Exception:
            saved[key] = default

    try:
        saved[u"categories"] = my_config.get_option("categories", None)
    except Exception:
        saved[u"categories"] = None

    return saved


def save_chosen(options):
    try:
        my_config.scope = options.get(u"scope")
        my_config.mode = options.get(u"mode")
        my_config.gap_mm = options.get(u"gap_mm")
        my_config.offset_mm = options.get(u"offset_mm")
        my_config.leader_mm = options.get(u"leader_mm")
        my_config.tangle = options.get(u"tangle")
        my_config.enable_leader = options.get(u"enable_leader")
        my_config.rebuild_elbow = options.get(u"rebuild_elbow")
        my_config.categories = options.get(u"categories")

        script.save_config()
    except Exception:
        pass


# ======================================================================
#  Подготовка данных
# ======================================================================

def check_view(view):
    if view is None:
        fail(u"Активный вид не определён. Откройте план, разрез или фасад.")

    if isinstance(view, ViewSheet):
        fail(u"Инструмент работает на видах, а не на листе.\n\n"
             u"Откройте нужный план или разрез и запустите ещё раз.")

    try:
        if view.IsTemplate:
            fail(u"Активен шаблон вида. Откройте обычный вид.")
    except Exception:
        pass

    if isinstance(view, View3D):
        try:
            if not view.IsLocked:
                fail(u"3D-вид не заблокирован, марки на нём не размещаются.\n\n"
                     u"Заблокируйте ориентацию вида или откройте план либо разрез.")
        except Exception:
            pass


def collect_selected_tag_ids(tags):
    u"""Выделенные в модели марки. Всё лишнее из выделения отбрасываем."""
    known = set()

    for tag in tags:
        known.add(tag.Id.IntegerValue)

    selected = set()

    try:
        for element_id in uidoc.Selection.GetElementIds():
            value = element_id.IntegerValue

            if value in known:
                selected.add(value)
    except Exception:
        pass

    return selected


def build_categories(boxes):
    u"""Категории самих марок с числом марок в каждой, по алфавиту."""
    found = {}

    for box in boxes:
        key = box[u"cat"]

        if key in found:
            found[key][u"count"] += 1
        else:
            found[key] = {
                u"key": key,
                u"name": box[u"cat_name"],
                u"count": 1,
            }

    return sorted(found.values(), key=lambda item: item[u"name"])


# ======================================================================
#  Отчёт
# ======================================================================

def build_report(stats, applied):
    lines = []

    moved = applied.get(u"applied", 0)
    before = stats.get(u"tags_before", 0)
    after = stats.get(u"tags_after", 0)

    lines.append(u"Переставлено марок: {}.".format(moved))
    lines.append(u"Было с бедой: {} {}, осталось: {}.".format(
        before, tags_word(before), after))

    crossings_before = stats.get(u"crossings_before", 0)
    through_before = stats.get(u"through_before", 0)

    if crossings_before or through_before:
        lines.append(u"")

        if crossings_before:
            lines.append(u"Пересечений выносок: {} \u2192 {}.".format(
                crossings_before, stats.get(u"crossings_after", 0)))

        if through_before:
            lines.append(u"Выносок сквозь чужой текст: {} \u2192 {}.".format(
                through_before, stats.get(u"through_after", 0)))

        swapped = stats.get(u"swapped", 0)

        if swapped:
            lines.append(u"Марок переставлено местами: {}.".format(swapped))

    leaders = applied.get(u"leaders", 0)

    if leaders:
        lines.append(u"Включено выносок: {}.".format(leaders))

    if moved:
        lines.append(u"")
        lines.append(u"Переставленные марки выделены в модели — сразу видно, "
                     u"что изменилось.")

    if after:
        lines.append(u"")
        lines.append(u"Осталось с бедой: {}. Обычно не хватает разрешённого "
                     u"смещения или места по соседству — увеличьте максимальное "
                     u"смещение либо разведите вручную.".format(after))

    failed = applied.get(u"failed") or []

    if failed:
        lines.append(u"")
        lines.append(u"Не удалось переставить: {}.".format(len(failed)))

        for message in failed[:10]:
            lines.append(u"  - {}".format(message))

        if len(failed) > 10:
            lines.append(u"  и ещё {}".format(len(failed) - 10))

    lines.append(u"")
    lines.append(u"Ctrl+Z вернёт всё как было.")

    return u"\n".join(lines)


def select_in_model(ids):
    u"""Выделяем ровно то, что переставили.

    Раньше выделялись все марки, оставшиеся с бедой, и на плотном плане это
    выглядело как «выделило вообще всё» — пользы ноль.
    """
    if not ids:
        return

    try:
        collection = List[ElementId]()

        for value in ids:
            collection.Add(ElementId(value))

        uidoc.Selection.SetElementIds(collection)
    except Exception:
        pass


# ======================================================================
#  Основной ход
# ======================================================================

view = doc.ActiveView

check_view(view)

tags = pp_tag_layout.collect_tags(doc, view)

if not tags:
    fail(u"На активном виде нет марок.\n\n"
         u"Инструмент разводит марки, которые уже расставлены.")

selected_ids = collect_selected_tag_ids(tags)

boxes = pp_tag_layout.measure_boxes(doc, view, tags)

if not boxes:
    fail(u"Не удалось замерить ни одной марки на этом виде.\n\n"
         u"Так бывает, если марки скрыты фильтром вида или лежат за границей "
         u"области подрезки.")

positions = [[box[u"hu"], box[u"hv"]] for box in boxes]

overlapping_pairs, overlapping_tags = pp_tag_layout.count_overlaps(boxes, positions, 0.0)
crossings, through, leader_tags = pp_tag_layout.count_leader_problems(boxes, positions)

if not overlapping_pairs and not crossings and not through:
    fail(u"Марки не накладываются друг на друга, выноски не перепутаны — "
         u"разбирать нечего.\n\n"
         u"Проверено марок: {}.".format(len(boxes)))


def solve(scoped_boxes, gap_ft, offset_ft, mode, leaders):
    return pp_tag_layout.solve(
        scoped_boxes, gap_ft, offset_ft, mode, leaders=leaders)


state = {
    u"boxes": boxes,
    u"selected_ids": selected_ids,
    u"categories": build_categories(boxes),
    u"overlapping": len(overlapping_tags),
    u"crossing": len(leader_tags),
    u"sizes": pp_tag_layout.size_summary(boxes, view),
    u"scale": pp_tag_layout.view_scale(view),
    u"ft_per_mm": pp_tag_layout.mm_to_ft(1.0, view),
    u"solve": solve,
}

options = pp_spread_window.ask_options(state, load_saved())

if not options:
    script.exit()

save_chosen(options)

moves = options.get(u"moves") or {}

if not moves:
    fail(u"Ни одна марка не сдвинулась.")

transaction = Transaction(doc, u"PP: Раздвинуть марки")

try:
    transaction.Start()

    applied = pp_tag_layout.apply_moves(doc, view, boxes, moves, options)

    transaction.Commit()
except Exception as error:
    try:
        if transaction.HasStarted() and not transaction.HasEnded():
            transaction.RollBack()
    except Exception:
        pass

    fail(u"Не удалось раздвинуть марки: {}".format(unicode(error)))

stats = options.get(u"stats") or {}

select_in_model(applied.get(u"moved_ids") or [])

pp_wpf.show_report(
    build_report(stats, applied),
    title=u"Готово",
    subtitle=TITLE,
    is_error=bool(applied.get(u"failed"))
)
