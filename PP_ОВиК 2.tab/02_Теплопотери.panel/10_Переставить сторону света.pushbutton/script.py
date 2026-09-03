# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
# Переставить стор. света — версия 1.
#
# Здание развернули (или поменяли угол к северу) — записанные стороны света
# стали неверными. Пересчитывать их заново зондами долго и не всегда нужно:
# если повернули ВСЁ здание, достаточно сдвинуть готовые значения по кругу.
#
# Инструмент берёт элементы активного 3D вида (стены, витражи, окна, двери)
# с заполненным параметром PP_Ориентация по стороне света и сдвигает значение
# на выбранный угол по часовой стрелке (шаг 45°).
#
# Окно — pp_compass_shift_window.py, здесь только сбор с вида, транзакция
# и отчёт. Логику определения стороны света см. в кнопке «Опр. стор. света».

import os
import sys
import traceback

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    BuiltInCategory, FilteredElementCollector, StorageType, Transaction,
    View3D, WallKind
)
from Autodesk.Revit.Exceptions import OperationCanceledException

import pp_wpf
import pp_settings

_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

from pp_compass_shift_window import (
    SECTORS, ask_options, plural_elements, shift
)


doc = __revit__.ActiveUIDocument.Document


TOOL_TITLE = u"Переставить стор. света"

PARAM_ORIENT = u"PP_Ориентация по стороне света"
PARAM_COEFF = u"PP_Добавка на сторону света"

# Та же таблица, что в кнопке «Опр. стор. света»: правится синхронно.
ORIENT_COEFF = {
    u"С": 1.1, u"СВ": 1.1, u"В": 1.1, u"ЮВ": 1.05,
    u"Ю": 1.0, u"ЮЗ": 1.0, u"З": 1.05, u"СЗ": 1.1
}

WALL_CAT = int(BuiltInCategory.OST_Walls)
WINDOW_CAT = int(BuiltInCategory.OST_Windows)
DOOR_CAT = int(BuiltInCategory.OST_Doors)

TARGET_CATEGORIES = (
    BuiltInCategory.OST_Walls,
    BuiltInCategory.OST_Windows,
    BuiltInCategory.OST_Doors
)

# Латиница и цифра, которые в русских значениях выглядят как кириллица.
LOOKALIKE = {u"C": u"С", u"B": u"В", u"3": u"З", u"E": u"Е", u"O": u"О"}

KIND_WALL = u"Стены"
KIND_CURTAIN = u"Витражи"
KIND_WINDOW = u"Окна"
KIND_DOOR = u"Двери"

KIND_ORDER = (KIND_WALL, KIND_CURTAIN, KIND_WINDOW, KIND_DOOR)

KIND_ONE = {
    KIND_WALL: u"Стена",
    KIND_CURTAIN: u"Витраж",
    KIND_WINDOW: u"Окно",
    KIND_DOOR: u"Дверь"
}


class Stop(Exception):
    pass


def fail(message, title):
    u"""Показать окно ошибки и прервать сценарий."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    raise Stop()


# ─────────────────────────────────────────────
# РАЗБОР ЗНАЧЕНИЙ
# ─────────────────────────────────────────────

def normalize(value):
    u"""Значение параметра → сторона света из списка либо None."""
    text = unicode(value or u"").strip().upper()

    if not text:
        return None

    text = u"".join(LOOKALIKE.get(char, char) for char in text)

    return text if text in SECTORS else None


def category_id(element):
    try:
        return element.Category.Id.IntegerValue
    except:
        return None


def is_curtain_wall(wall):
    try:
        return wall.WallType.Kind == WallKind.Curtain
    except:
        return False


def kind_of(element):
    cid = category_id(element)

    if cid == WALL_CAT:
        return KIND_CURTAIN if is_curtain_wall(element) else KIND_WALL

    if cid == WINDOW_CAT:
        return KIND_WINDOW

    if cid == DOOR_CAT:
        return KIND_DOOR

    return None


# ─────────────────────────────────────────────
# СБОР С АКТИВНОГО 3D ВИДА
# ─────────────────────────────────────────────

def active_3d_view():
    view = doc.ActiveView

    if view is None:
        fail(
            u"Активного вида нет. Откройте 3D вид и запустите инструмент заново.",
            u"Нет активного вида"
        )

    if not isinstance(view, View3D) or view.IsTemplate:
        fail(
            u"Инструмент работает по активному 3D виду, а сейчас открыт вид "
            u"«{}».\n\n"
            u"Откройте 3D вид, оставьте на нём то, что нужно переставить "
            u"(разрезающая рамка и фильтры вида учитываются), и запустите "
            u"инструмент заново.".format(view.Name),
            u"Нужен 3D вид"
        )

    return view


def collect(view):
    u"""Элементы вида с заполненной ориентацией + разбор для окна."""
    items = []
    counts = dict((letter, 0) for letter in SECTORS)
    kinds = dict((name, 0) for name in KIND_ORDER)
    unknown = 0

    for category in TARGET_CATEGORIES:
        collector = FilteredElementCollector(doc, view.Id) \
            .OfCategory(category) \
            .WhereElementIsNotElementType()

        for element in collector:
            kind = kind_of(element)

            if kind is None:
                continue

            parameter = element.LookupParameter(PARAM_ORIENT)

            if parameter is None or parameter.StorageType != StorageType.String:
                continue

            try:
                raw = parameter.AsString()
            except:
                raw = None

            if not unicode(raw or u"").strip():
                continue

            letter = normalize(raw)

            if letter is None:
                unknown += 1
                continue

            items.append((element, letter, kind))
            counts[letter] += 1
            kinds[kind] += 1

    plan = {
        u"counts": counts,
        u"total": len(items),
        u"unknown": unknown,
        u"by_kind": [(name, kinds[name]) for name in KIND_ORDER if kinds[name]],
        u"view_name": view.Name
    }

    return items, plan


# ─────────────────────────────────────────────
# ЗАПИСЬ
# ─────────────────────────────────────────────

def write_orientation(element, letter):
    u"""Вернуть текст ошибки либо None, если записалось."""
    parameter = element.LookupParameter(PARAM_ORIENT)

    if parameter is None:
        return u"нет параметра «{}»".format(PARAM_ORIENT)

    if parameter.IsReadOnly:
        return u"параметр «{}» только для чтения".format(PARAM_ORIENT)

    try:
        if not parameter.Set(letter):
            return u"Revit отказался записать ориентацию"
    except Exception as error:
        return unicode(error)

    return None


def write_coeff(element, letter):
    u"""Вернуть текст ошибки либо None. Отсутствие параметра — не ошибка."""
    parameter = element.LookupParameter(PARAM_COEFF)

    if parameter is None or parameter.IsReadOnly:
        return None

    try:
        parameter.Set(ORIENT_COEFF.get(letter, 1.0))
    except Exception as error:
        return unicode(error)

    return None


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

try:
    view = active_3d_view()

    items, plan = collect(view)

    if not plan[u"total"]:
        message = (
            u"На виде «{}» нет элементов с заполненным параметром «{}».\n\n"
            u"Проверьте, что на виде видны стены, окна и двери, и что сторона "
            u"света у них уже определена кнопкой «Опр. стор. света»."
        ).format(view.Name, PARAM_ORIENT)

        if plan[u"unknown"]:
            message += (
                u"\n\nЗначения есть, но не читаются как сторона света: {}. "
                u"Ожидаются С, СВ, В, ЮВ, Ю, ЮЗ, З, СЗ."
            ).format(plan[u"unknown"])

        fail(message, u"Переставлять нечего")

    options = ask_options(plan)

    if options is None:
        raise Stop()

    steps = int(options[u"steps"])
    recalc = bool(options[u"recalc_coeff"])

    done = 0
    coeff_done = 0
    errors = []

    transaction = Transaction(doc, u"PP: Переставить стороны света")
    transaction.Start()

    try:
        for element, letter, kind in items:
            new_letter = shift(letter, steps)

            error = write_orientation(element, new_letter)

            if error:
                errors.append(u"{} id {} — {}".format(
                    KIND_ONE.get(kind, kind), element.Id.IntegerValue, error
                ))
                continue

            done += 1

            if recalc:
                coeff_error = write_coeff(element, new_letter)

                if coeff_error:
                    errors.append(u"id {} — добавка: {}".format(
                        element.Id.IntegerValue, coeff_error
                    ))
                else:
                    coeff_done += 1

        transaction.Commit()
    except:
        transaction.RollBack()
        raise

    # ── Отчёт ────────────────────────────────────────────────
    if not done:
        fail(
            u"Ни один элемент переставить не удалось.\n\n" + u"\n".join(errors),
            u"Значения не изменены"
        )

    success = [u"Переставлено: {} {} на виде «{}».".format(
        done, plural_elements(done), view.Name
    )]

    success.append(u"Поворот на {}° по часовой стрелке:".format(steps * 45))

    for letter in SECTORS:
        moved = plan[u"counts"].get(letter, 0)

        if moved:
            success.append(u"  • {} → {} — {} {}".format(
                letter, shift(letter, steps), moved, plural_elements(moved)
            ))

    success.append(u"")

    if recalc:
        success.append(
            u"Параметр «{}» пересчитан у {} {}.".format(
                PARAM_COEFF, coeff_done, plural_elements(coeff_done)
            )
        )
    else:
        success.append(
            u"Параметр «{}» не трогали — он остался от прежней стороны света.".format(
                PARAM_COEFF
            )
        )

    success.append(u"Если результат не устроил — отмена по Ctrl+Z.")

    warning = list(success)

    if plan[u"unknown"]:
        warning.append(u"")
        warning.append(
            u"Пропущено значений, не похожих на сторону света: {}.".format(
                plan[u"unknown"]
            )
        )

    if errors:
        warning.append(u"")
        warning.append(u"Не записалось:")

        for line in errors:
            warning.append(u"  • {}".format(line))

    pp_settings.show_report(
        None,
        TOOL_TITLE,
        u"\n".join(success),
        u"\n".join(warning),
        bool(errors or plan[u"unknown"])
    )

except Stop:
    pass

except OperationCanceledException:
    pass

except Exception as ex:
    details = u""

    try:
        details = unicode(traceback.format_exc())
    except Exception:
        pass

    pp_wpf.show_report(
        u"{}\n\n{}".format(unicode(ex), details).strip(),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True,
        monospace=True,
        width=900
    )
