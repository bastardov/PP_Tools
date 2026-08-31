# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
u"""Раскладывает расчётный расход пространства по решёткам внутри него.

Идея взята из рабочего скрипта Dynamo и перенесена на кнопку без изменения
расчёта: для каждого выбранного пространства инструмент находит
воздухораспределители, которые стоят внутри него, делит общий расход
пространства на их количество и записывает долю каждой решётке.

Вытяжка и приток разделяются по имени системы решётки: имя начинается с
одного из вытяжных префиксов (В, ВЕ) — решётка вытяжная, с приточных
(П, ПЕ) — приточная. Системы противодымной вентиляции (ДУ, ПД, подпор)
отсеиваются по списку слов-исключений: их расход инструмент не трогает.

Принадлежность решётки пространству проверяется так же, как в Dynamo:
берётся точка вставки, центр, низ и верх габаритного бокса, а если ни одна
из них не попала в пространство — те же точки со сдвигом вниз на 100…1000 мм.
Так учитываются решётки, посаженные в подшивной потолок над пространством.

Настройки окна (имена параметров, префиксы, исключения) хранятся в
lib/pp_settings.json под ключом space_airflow_settings.
"""

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    FilteredElementCollector,
    LocationPoint,
    StorageType,
    Transaction,
    XYZ
)
from Autodesk.Revit.DB.Mechanical import Space as MEPSpace
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

import os
import sys

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_settings
import pp_param_source

from pp_airflow_window import ask_options, split_list


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TOOL_TITLE = u"Расход из пространства"
SETTINGS_KEY = u"space_airflow_settings"

DEFAULT_CONFIG = {
    u"exhaust_total_param": u"ADSK_Расчетная вытяжка",
    u"supply_total_param": u"ADSK_Расчетный приток",
    u"exhaust_count_param": u"PP_Количество решеток систем В",
    u"supply_count_param": u"PP_Количество решеток систем П",
    u"terminal_flow_param": u"ADSK_Расход воздуха",
    u"exhaust_prefixes": u"В, ВЕ",
    u"supply_prefixes": u"П, ПЕ",
    u"exclude_words": u"ДУ, ДЫМ, ДЫМО, ДЫМОУДАЛ, ПД, ПДВ, ПОДПОР, ПРОТИВОДЫМ",
    u"write_counts": True
}

MM_TO_FT = 1.0 / 304.8
DOWN_OFFSETS_MM = [100, 200, 300, 500, 700, 1000]

# Запас габаритного бокса пространства при быстром отсеве решёток:
# по X и Y небольшой допуск, вверх — на всю глубину поиска вниз.
BBOX_TOL_FT = 100.0 * MM_TO_FT
BBOX_UP_FT = max(DOWN_OFFSETS_MM) * MM_TO_FT

SYSTEM_NAME_PARAMS = [
    u"Имя системы",
    u"System Name",
    u"ADSK_Имя системы"
]

MAX_REPORT_LINES = 30


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта."""
    pass


def fail(message):
    raise Stop(message)


def rollback():
    try:
        if "t" in globals():
            _t = globals()["t"]
            if hasattr(_t, "HasStarted") and _t.HasStarted() and not _t.HasEnded():
                _t.RollBack()
    except Exception:
        pass


# ======================================================================
#  Выбор пространств
# ======================================================================

class SpaceSelectionFilter(ISelectionFilter):
    u"""На виде разрешаем выбирать только пространства."""

    def AllowElement(self, elem):
        try:
            return isinstance(elem, MEPSpace)
        except Exception:
            return False

    def AllowReference(self, reference, point):
        return False


def selected_spaces():
    u"""Пространства из текущего выделения. Прочее молча пропускаем."""
    spaces = []

    try:
        ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        return spaces

    for element_id in ids:
        element = doc.GetElement(element_id)

        if isinstance(element, MEPSpace):
            spaces.append(element)

    return spaces


def pick_spaces():
    refs = uidoc.Selection.PickObjects(
        ObjectType.Element,
        SpaceSelectionFilter(),
        u"Выберите пространства и нажмите «Готово» в панели параметров"
    )

    spaces = []

    for ref in refs:
        element = doc.GetElement(ref.ElementId)

        if isinstance(element, MEPSpace):
            spaces.append(element)

    return spaces


# ======================================================================
#  Параметры
# ======================================================================

def get_param(element, param_name):
    if not param_name:
        return None

    try:
        return element.LookupParameter(param_name)
    except Exception:
        return None


def read_double(element, param_name):
    parameter = get_param(element, param_name)

    if parameter is None:
        return None

    try:
        return parameter.AsDouble()
    except Exception:
        return None


def read_display(element, param_name):
    u"""Значение так, как его показывает Revit, — с единицами проекта."""
    parameter = get_param(element, param_name)

    if parameter is None:
        return u"—"

    try:
        text = parameter.AsValueString()

        if text:
            return text
    except Exception:
        pass

    try:
        return u"{:.1f}".format(parameter.AsDouble())
    except Exception:
        return u"—"


def write_value(element, param_name, value):
    u"""Возврат: (получилось, причина отказа)."""
    parameter = get_param(element, param_name)

    if parameter is None:
        return False, u"параметр «{}» не найден".format(param_name)

    if parameter.IsReadOnly:
        return False, u"параметр «{}» только для чтения".format(param_name)

    try:
        if parameter.StorageType == StorageType.Double:
            parameter.Set(float(value))
            return True, None

        if parameter.StorageType == StorageType.Integer:
            parameter.Set(int(round(value)))
            return True, None

        if parameter.StorageType == StorageType.String:
            parameter.Set(unicode(value))
            return True, None
    except Exception as ex:
        return False, unicode(ex)

    return False, u"неподдерживаемый тип параметра «{}»".format(param_name)


# ======================================================================
#  Системы
# ======================================================================

def normalize_text(value):
    if value is None:
        return u""

    try:
        return unicode(value).upper().replace(u" ", u"")
    except Exception:
        return u""


def get_system_name(element):
    for name in SYSTEM_NAME_PARAMS:
        parameter = get_param(element, name)

        if parameter is None:
            continue

        for reader in (parameter.AsString, parameter.AsValueString):
            try:
                value = reader()

                if value:
                    return value
            except Exception:
                pass

    try:
        parameter = element.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)

        if parameter:
            value = parameter.AsString()

            if value:
                return value
    except Exception:
        pass

    return u""


def has_excluded_words(system_name, exclude_words):
    for word in exclude_words:
        if word and word in system_name:
            return True

    return False


def matches_prefix(system_name, prefixes):
    for prefix in prefixes:
        if prefix and system_name.startswith(prefix):
            return True

    return False


# ======================================================================
#  Принадлежность решётки пространству
# ======================================================================

def probe_points(element):
    u"""Точки, которыми решётка «щупает» пространство. Считаются один раз."""
    points = []

    try:
        location = element.Location

        if isinstance(location, LocationPoint):
            points.append(location.Point)
    except Exception:
        pass

    try:
        bbox = element.get_BoundingBox(None)

        if bbox:
            mid_x = (bbox.Min.X + bbox.Max.X) / 2.0
            mid_y = (bbox.Min.Y + bbox.Max.Y) / 2.0

            points.append(XYZ(mid_x, mid_y, (bbox.Min.Z + bbox.Max.Z) / 2.0))
            points.append(XYZ(mid_x, mid_y, bbox.Min.Z))
            points.append(XYZ(mid_x, mid_y, bbox.Max.Z))
    except Exception:
        pass

    probes = list(points)

    # Решётка в подшивном потолке лежит выше пространства — щупаем вниз
    for point in points:
        for offset_mm in DOWN_OFFSETS_MM:
            probes.append(XYZ(point.X, point.Y, point.Z - offset_mm * MM_TO_FT))

    return probes


def make_terminal_info(element):
    probes = probe_points(element)

    if not probes:
        return None

    xs = [p.X for p in probes]
    ys = [p.Y for p in probes]
    zs = [p.Z for p in probes]

    return {
        "element": element,
        "id": element.Id.IntegerValue,
        "system": get_system_name(element),
        "probes": probes,
        "min_x": min(xs), "max_x": max(xs),
        "min_y": min(ys), "max_y": max(ys),
        "min_z": min(zs), "max_z": max(zs)
    }


def space_box(space):
    try:
        return space.get_BoundingBox(None)
    except Exception:
        return None


def maybe_inside(info, bbox):
    u"""Быстрый отсев по габаритам: без него IsPointInSpace съедает минуты."""
    if bbox is None:
        return True

    if info["max_x"] < bbox.Min.X - BBOX_TOL_FT:
        return False

    if info["min_x"] > bbox.Max.X + BBOX_TOL_FT:
        return False

    if info["max_y"] < bbox.Min.Y - BBOX_TOL_FT:
        return False

    if info["min_y"] > bbox.Max.Y + BBOX_TOL_FT:
        return False

    if info["max_z"] < bbox.Min.Z - BBOX_TOL_FT:
        return False

    if info["min_z"] > bbox.Max.Z + BBOX_UP_FT:
        return False

    return True


def is_inside(info, space):
    for point in info["probes"]:
        try:
            if space.IsPointInSpace(point):
                return True
        except Exception:
            pass

    return False


# ======================================================================
#  Обработка пространства
# ======================================================================

def space_label(space):
    try:
        number = space.Number or u""
    except Exception:
        number = u""

    try:
        name = space.Name or u""
    except Exception:
        name = u""

    return u"{} {}".format(number, name).strip() or u"Id {}".format(
        space.Id.IntegerValue
    )


def is_placed(space):
    try:
        return space.Area > 0
    except Exception:
        return True


def sort_terminals(terminals, opts):
    u"""Делит найденные решётки на вытяжные, приточные и чужие."""
    exhaust = []
    supply = []
    skipped = []

    for info in terminals:
        system = normalize_text(info["system"])

        if not system:
            skipped.append((info, u"нет имени системы"))
            continue

        if has_excluded_words(system, opts["exclude_words"]):
            skipped.append((info, info["system"]))
            continue

        if opts["exhaust_on"] and matches_prefix(system, opts["exhaust_prefixes"]):
            exhaust.append(info)

        elif opts["supply_on"] and matches_prefix(system, opts["supply_prefixes"]):
            supply.append(info)

        else:
            skipped.append((info, info["system"]))

    return exhaust, supply, skipped


def spread_flow(space, terminals, total_param, count_param, opts, errors):
    u"""Делит расход пространства на решётки. Возврат: словарь для отчёта."""
    count = len(terminals)
    label = space_label(space)

    if opts["write_counts"] and count_param:
        ok, reason = write_value(space, count_param, count)

        if not ok:
            errors.append(u"{}: {}".format(label, reason))

    if count == 0:
        return {"count": 0, "total": u"—", "each": u"—"}

    total = read_double(space, total_param)

    if total is None:
        errors.append(
            u"{}: не читается параметр «{}» — расход не разложен".format(
                label, total_param
            )
        )
        return {"count": count, "total": u"не читается", "each": u"—"}

    share = total / float(count)
    written = 0
    each = u"—"

    for info in terminals:
        ok, reason = write_value(
            info["element"], opts["terminal_flow_param"], share
        )

        if ok:
            written += 1

            # Долю показываем так, как её увидит пользователь в свойствах
            if written == 1:
                each = read_display(
                    info["element"], opts["terminal_flow_param"]
                )
        else:
            errors.append(u"Решётка Id {}: {}".format(info["id"], reason))

    return {
        "count": count,
        "written": written,
        "total": read_display(space, total_param),
        "each": each
    }


# ======================================================================
#  Отчёт
# ======================================================================

def limited(lines):
    if len(lines) <= MAX_REPORT_LINES:
        return lines

    tail = len(lines) - MAX_REPORT_LINES

    return lines[:MAX_REPORT_LINES] + [u"  … и ещё {}".format(tail)]


def build_report(results, skipped, shared, errors, opts):
    lines = []

    total_exhaust = sum(r["exhaust"]["count"] for r in results)
    total_supply = sum(r["supply"]["count"] for r in results)

    lines.append(u"Обработано пространств: {}".format(len(results)))
    lines.append(u"Вытяжных решёток: {}     Приточных решёток: {}".format(
        total_exhaust, total_supply
    ))
    lines.append(u"Пропущено решёток: {}     Ошибок: {}".format(
        len(skipped), len(errors)
    ))
    lines.append(u"")

    for item in results:
        lines.append(item["label"])

        if item["unplaced"]:
            lines.append(u"    пространство не размещено — решётки не искались")
            lines.append(u"")
            continue

        for title, key, on in (
            (u"Вытяжка", "exhaust", opts["exhaust_on"]),
            (u"Приток ", "supply", opts["supply_on"])
        ):
            data = item[key]

            if not on:
                lines.append(u"    {}  не считается".format(title))

            elif data["count"] == 0:
                lines.append(u"    {}  решёток не найдено".format(title))

            else:
                lines.append(u"    {}  {}  /  {} реш.  =  {}".format(
                    title, data["total"], data["count"], data["each"]
                ))

        lines.append(u"")

    if skipped:
        lines.append(u"Решётки с чужой системой — расход не изменён: {}".format(
            len(skipped)
        ))
        lines.extend(limited([
            u"  Id {}  ·  система «{}»".format(info["id"], reason)
            for info, reason in skipped
        ]))
        lines.append(u"")

    if shared:
        lines.append(
            u"Решётки, попавшие сразу в несколько выбранных пространств: {}".format(
                len(shared)
            )
        )
        lines.append(
            u"  Записан расход последнего пространства — проверьте границы."
        )
        lines.extend(limited([
            u"  Id {}  ·  {}".format(terminal_id, u", ".join(labels))
            for terminal_id, labels in shared
        ]))
        lines.append(u"")

    if errors:
        lines.append(u"Ошибки: {}".format(len(errors)))
        lines.extend(limited([u"  " + text for text in errors]))

    return u"\n".join(lines).rstrip()


# ======================================================================
#  Настройки
# ======================================================================

def load_config():
    config = dict(DEFAULT_CONFIG)

    try:
        stored = pp_settings.load_settings().get(SETTINGS_KEY) or {}
    except Exception:
        stored = {}

    if isinstance(stored, dict):
        for key in DEFAULT_CONFIG:
            if key in stored:
                config[key] = stored[key]

    return config


def save_config(config):
    try:
        settings = pp_settings.load_settings()
        settings[SETTINGS_KEY] = config
        pp_settings.save_settings(settings)
    except Exception:
        pass


def param_provider(kind):
    if kind == u"space":
        category = BuiltInCategory.OST_MEPSpaces
    else:
        category = BuiltInCategory.OST_DuctTerminal

    try:
        return pp_param_source.project_options(
            doc,
            category_ids=[int(category)],
            instance_only=True
        )
    except Exception:
        return []


# ======================================================================
#  Запуск
# ======================================================================

try:
    config = load_config()

    spaces = selected_spaces()

    answer = ask_options(config, param_provider, len(spaces))

    if answer is None:
        raise SystemExit

    save_config(answer)

    opts = {
        "terminal_flow_param": answer[u"terminal_flow_param"],
        "exhaust_prefixes": [normalize_text(x)
                             for x in split_list(answer[u"exhaust_prefixes"])],
        "supply_prefixes": [normalize_text(x)
                            for x in split_list(answer[u"supply_prefixes"])],
        "exclude_words": [normalize_text(x)
                          for x in split_list(answer[u"exclude_words"])],
        "write_counts": bool(answer[u"write_counts"])
    }

    opts["exhaust_on"] = bool(answer[u"exhaust_total_param"]
                              and opts["exhaust_prefixes"])
    opts["supply_on"] = bool(answer[u"supply_total_param"]
                             and opts["supply_prefixes"])

    if not spaces:
        spaces = pick_spaces()

    if not spaces:
        fail(u"Не выбрано ни одного пространства.")

    terminals = []

    for element in (FilteredElementCollector(doc)
                    .OfCategory(BuiltInCategory.OST_DuctTerminal)
                    .WhereElementIsNotElementType()):
        info = make_terminal_info(element)

        if info is not None:
            terminals.append(info)

    if not terminals:
        fail(u"В модели нет ни одного воздухораспределителя.")

    results = []
    errors = []
    skipped_all = {}
    owners = {}

    t = Transaction(doc, TOOL_TITLE)
    t.Start()

    try:
        for space in spaces:
            label = space_label(space)

            if not is_placed(space):
                results.append({
                    "label": label,
                    "unplaced": True,
                    "exhaust": {"count": 0, "total": u"—", "each": u"—"},
                    "supply": {"count": 0, "total": u"—", "each": u"—"}
                })
                continue

            bbox = space_box(space)

            inside = [info for info in terminals
                      if maybe_inside(info, bbox) and is_inside(info, space)]

            for info in inside:
                owners.setdefault(info["id"], []).append(label)

            exhaust, supply, skipped = sort_terminals(inside, opts)

            for info, reason in skipped:
                skipped_all[info["id"]] = (info, reason)

            results.append({
                "label": label,
                "unplaced": False,
                "exhaust": spread_flow(
                    space, exhaust,
                    answer[u"exhaust_total_param"],
                    answer[u"exhaust_count_param"],
                    opts, errors
                ) if opts["exhaust_on"] else {
                    "count": 0, "total": u"—", "each": u"—"
                },
                "supply": spread_flow(
                    space, supply,
                    answer[u"supply_total_param"],
                    answer[u"supply_count_param"],
                    opts, errors
                ) if opts["supply_on"] else {
                    "count": 0, "total": u"—", "each": u"—"
                }
            })

        t.Commit()
    except Exception:
        t.RollBack()
        raise

    shared = [(terminal_id, labels)
              for terminal_id, labels in owners.items() if len(labels) > 1]
    shared.sort()

    skipped_list = [skipped_all[key] for key in sorted(skipped_all)]

    pp_wpf.show_report(
        build_report(results, skipped_list, shared, errors, opts),
        title=u"Готово",
        subtitle=TOOL_TITLE,
        monospace=True,
        width=760
    )

except OperationCanceledException:
    rollback()

except SystemExit:
    pass

except Stop as ex:
    rollback()
    pp_wpf.show_report(
        unicode(ex),
        title=u"Не выполнено",
        subtitle=TOOL_TITLE,
        is_error=True
    )

except Exception as ex:
    rollback()
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )
