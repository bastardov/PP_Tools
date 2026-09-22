# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import clr
import random

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    FillPatternElement,
    InstanceBinding,
    OverrideGraphicSettings,
    Color,
    ElementId,
    Transaction,
    StorageType
)

import System

from pyrevit import script

import pp_wpf
import pp_paint_dialog


class Stop(Exception):
    u"""Осмысленная остановка: сообщение уходит в окно отчёта, транзакция откатывается."""
    pass


def fail(message):
    raise Stop(message)


def rollback():
    u"""Откат незакрытой транзакции. Вызывается из каждой ветки except."""
    try:
        if "t" in globals():
            _t = globals()["t"]
            if hasattr(_t, "HasStarted") and _t.HasStarted() and not _t.HasEnded():
                _t.RollBack()
    except Exception:
        pass


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

TITLE = u"Окрасить рандомно"
DEFAULT_PARAM = u"ADSK_Группирование"

# Конфиг pyRevit — хранит выбор между запусками
cfg = script.get_config()


def load_saved():
    u"""Загружает сохранённые имена категорий и параметр."""
    try:
        cats = cfg.get_option("selected_cats", [])
    except:
        cats = []
    if not isinstance(cats, list):
        cats = []

    try:
        param = cfg.get_option("group_param", DEFAULT_PARAM)
    except:
        param = DEFAULT_PARAM
    if not param:
        param = DEFAULT_PARAM

    return set(cats), param


def save_selection(cat_names, param):
    try:
        cfg.selected_cats = list(cat_names)
        cfg.group_param = param
        script.save_config()
    except:
        pass


def collect_view_data():
    u"""Собирает элементы активного вида по категориям модели
    и множество имён параметров экземпляра."""
    elems = FilteredElementCollector(doc, view.Id) \
        .WhereElementIsNotElementType() \
        .ToElements()

    by_cat = {}          # cat_id_int -> {"name": str, "elems": [...], "params": set()}
    param_names = set()

    for e in elems:
        try:
            cat = e.Category
        except:
            cat = None

        if cat is None:
            continue

        try:
            cat_id = cat.Id.IntegerValue
        except:
            continue

        # Только категории модели (отсекаем аннотации и т.п.)
        try:
            if cat.CategoryType is not None:
                if str(cat.CategoryType) != "Model":
                    continue
        except:
            pass

        try:
            cat_name = cat.Name
        except:
            cat_name = u"(без имени)"

        if cat_id not in by_cat:
            by_cat[cat_id] = {"name": cat_name, "elems": [], "params": set()}

        by_cat[cat_id]["elems"].append(e)

    # Сбор имён параметров — по выборке до 60 элементов на категорию.
    # Имена запоминаются и по категориям: окно выбора показывает, у скольких
    # из отмеченных категорий параметр вообще встретился.
    for cat_id in by_cat:
        sample = by_cat[cat_id]["elems"][:60]
        for e in sample:
            try:
                for p in e.Parameters:
                    try:
                        if p.Definition is not None:
                            by_cat[cat_id]["params"].add(p.Definition.Name)
                    except:
                        pass
            except:
                pass

        param_names.update(by_cat[cat_id]["params"])

    return by_cat, param_names


def collect_project_params():
    u"""Параметры проекта: имя -> множество id категорий.

    Берутся только привязки экземпляра: значение читается через
    LookupParameter у самого элемента, параметр типоразмера так не прочитается
    и в списке был бы обманом."""
    result = {}

    try:
        iterator = doc.ParameterBindings.ForwardIterator()
        iterator.Reset()
    except:
        return result

    while True:
        try:
            if not iterator.MoveNext():
                break
        except:
            break

        try:
            definition = iterator.Key
            binding = iterator.Current
        except:
            continue

        if definition is None or binding is None:
            continue

        if not isinstance(binding, InstanceBinding):
            continue

        try:
            name = definition.Name
        except:
            name = None

        if not name:
            continue

        cat_ids = result.get(name)

        if cat_ids is None:
            cat_ids = set()
            result[name] = cat_ids

        try:
            for cat in binding.Categories:
                try:
                    cat_ids.add(cat.Id.IntegerValue)
                except:
                    pass
        except:
            pass

    return result


def build_param_options(by_cat, project_params, selected):
    u"""Строки для окна выбора параметра под текущие отметки категорий.

    selected — то, что вернул диалог: [(подпись, (cat_id, имя)), ...]."""
    selected_ids = [payload[0] for _label, payload in selected]
    total = len(selected_ids)

    view_names = set()

    for info in by_cat.values():
        view_names.update(info["params"])

    names = set(view_names)
    names.update(project_params.keys())

    options = []

    for name in names:
        on_view = 0
        bound = 0

        for cat_id in selected_ids:
            info = by_cat.get(cat_id)

            if info is not None and name in info["params"]:
                on_view += 1

            if cat_id in project_params.get(name, set()):
                bound += 1

        if name in view_names and name in project_params:
            source = u"с вида + параметр проекта"
        elif name in view_names:
            source = u"с вида"
        else:
            source = u"параметр проекта"

        coverage = max(on_view, bound)

        if total:
            display = u"{}    · {} · есть у {} из {} отмеченных категорий".format(
                name, source, coverage, total)
        else:
            display = u"{}    · {}".format(name, source)

        options.append({
            u"name": name,
            u"display": display,
            u"coverage": coverage,
        })

    # Сверху — параметры, покрывающие все отмеченные категории
    return sorted(
        options,
        key=lambda item: (-item[u"coverage"], item[u"name"].lower())
    )


def ask_options(by_cat, param_names, saved_cats, saved_param):
    u"""Общий диалог окраски: категории с активного вида плюс выбор параметра."""
    ordered = sorted(by_cat.items(), key=lambda kv: kv[1]["name"].lower())

    categories = []

    for cat_id, info in ordered:
        label = u"{}  ({})".format(info["name"], len(info["elems"]))
        categories.append((label, (cat_id, info["name"]), info["name"] in saved_cats))

    project_params = collect_project_params()

    def picker_provider(selected):
        u"""Список для кнопки «Выбрать…» — под категории, отмеченные сейчас."""
        return build_param_options(by_cat, project_params, selected)

    all_names = set(param_names)
    all_names.update(project_params.keys())

    result = pp_paint_dialog.ask({
        u"title": TITLE,
        u"subtitle": u"Элементы с одинаковым значением параметра получают общий "
                     u"случайный цвет — видно, как значения разложены по модели.",
        u"categories": categories,
        u"categories_label": u"КАТЕГОРИИ С АКТИВНОГО ВИДА",
        u"categories_hint": u"В скобках — сколько элементов этой категории на виде.",
        u"choice": {
            u"label": u"ПАРАМЕТР ГРУППИРОВКИ",
            u"hint": u"Можно выбрать из списка ниже, открыть «Выбрать…» "
                     u"с пояснениями или вписать имя параметра вручную.",
            u"items": sorted(all_names, key=lambda s: s.lower()),
            u"value": saved_param,
            u"picker": {
                u"title": u"Параметр группировки",
                u"subtitle": u"Параметры экземпляра, найденные на активном виде, "
                             u"и параметры проекта, привязанные к категориям. "
                             u"Сверху — те, что покрывают все отмеченные категории.",
                u"empty_text": u"Параметры не найдены. Отметьте категории в окне "
                               u"или впишите имя параметра вручную.",
                u"provider": picker_provider,
            },
        },
        u"run_label": u"Окрасить",
        u"reset_label": u"Сбросить окрашивание вместо окраски",
        u"reset_hint": u"Снимет переопределения с выбранных категорий на активном виде.",
    })

    if result is None:
        return None

    selected = [payload for _label, payload in result[u"categories"]]

    return {
        "cat_ids":   [cat_id for cat_id, _name in selected],
        "cat_names": [name for _cat_id, name in selected],
        "param":     result[u"choice"] or u"",
        "reset":     result[u"reset"],
    }


def get_solid_fill_id():
    try:
        fps = FilteredElementCollector(doc).OfClass(FillPatternElement).ToElements()
        for fp in fps:
            try:
                if fp.GetFillPattern().IsSolidFill:
                    return fp.Id
            except:
                pass
    except:
        pass
    return ElementId.InvalidElementId


def unique_colors(n):
    colors = []
    rng = random.Random()
    for i in range(n):
        h = (i * 137.508) % 360
        s = 0.65 + rng.uniform(-0.10, 0.10)
        v = 0.80 + rng.uniform(-0.10, 0.10)

        hi = int(h / 60) % 6
        f = (h / 60) - int(h / 60)

        p = v * (1 - s)
        q = v * (1 - f * s)
        t = v * (1 - (1 - f) * s)

        table = [
            (v, t, p),
            (q, v, p),
            (p, v, t),
            (p, q, v),
            (t, p, v),
            (v, p, q)
        ]
        r, g, b = table[hi]
        colors.append(Color(int(r * 255), int(g * 255), int(b * 255)))
    return colors


def get_group_key(el, param_name):
    u"""Строковый ключ группировки по значению параметра или None."""
    try:
        p = el.LookupParameter(param_name)
    except:
        p = None

    if p is None or not p.HasValue:
        return None

    try:
        st = p.StorageType
    except:
        st = None

    val = None
    try:
        if st == StorageType.String:
            val = p.AsString()
        elif st == StorageType.Integer:
            val = p.AsValueString()
            if val is None:
                iv = p.AsInteger()
                val = unicode(iv) if iv is not None else None
        elif st == StorageType.Double:
            val = p.AsValueString()
        elif st == StorageType.ElementId:
            val = p.AsValueString()
        else:
            val = p.AsValueString()
    except:
        val = None

    if val is None:
        return None

    val = val.strip()
    if not val:
        return None

    return val


def make_ogs(color, solid_id):
    ogs = OverrideGraphicSettings()

    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetSurfaceForegroundPatternId(solid_id)
            ogs.SetSurfaceForegroundPatternVisible(True)
        ogs.SetSurfaceForegroundPatternColor(color)
    except:
        pass

    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetSurfaceBackgroundPatternId(solid_id)
            ogs.SetSurfaceBackgroundPatternVisible(True)
        ogs.SetSurfaceBackgroundPatternColor(color)
    except:
        pass

    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetCutForegroundPatternId(solid_id)
            ogs.SetCutForegroundPatternVisible(True)
        ogs.SetCutForegroundPatternColor(color)
    except:
        pass

    try:
        if solid_id != ElementId.InvalidElementId:
            ogs.SetCutBackgroundPatternId(solid_id)
            ogs.SetCutBackgroundPatternVisible(True)
        ogs.SetCutBackgroundPatternColor(color)
    except:
        pass

    # Линии — чтобы тонкие элементы (соединители) тоже читались цветом
    try:
        ogs.SetProjectionLineColor(color)
        ogs.SetCutLineColor(color)
    except:
        pass

    return ogs


try:
    saved_cats, saved_param = load_saved()

    by_cat, param_names = collect_view_data()

    if not by_cat:
        fail(
            u"На активном виде не найдено элементов модели.\n"
            u"Откройте нужный 3D-вид или план и запустите инструмент снова."
        )

    result = ask_options(by_cat, param_names, saved_cats, saved_param)

    if result is None:
        raise System.OperationCanceledException()

    cat_ids = result["cat_ids"]
    param_name = result["param"]
    reset_only = result["reset"]

    # Запоминаем выбор для следующего запуска
    save_selection(result["cat_names"], param_name)

    if not cat_ids:
        fail(
            u"Ни одна категория не выбрана."
        )

    target_elems = []
    for cid in cat_ids:
        target_elems.extend(by_cat[cid]["elems"])

    cat_names = u", ".join(sorted([by_cat[cid]["name"] for cid in cat_ids]))

    # Режим сброса
    if reset_only:
        t = Transaction(doc, u"PP: Сброс окрашивания (рандом)")
        t.Start()
        clean_ogs = OverrideGraphicSettings()
        reset_count = 0
        for e in target_elems:
            try:
                view.SetElementOverrides(e.Id, clean_ogs)
                reset_count += 1
            except:
                pass
        t.Commit()

        pp_wpf.show_report(
            u"Готово.\n\nВид: {}\nКатегории: {}\nСброшено элементов: {}".format(
            view.Name, cat_names, reset_count
            ),
            title=u"Готово",
            subtitle=TITLE
        )
        raise SystemExit

    if not param_name:
        fail(
            u"Не задан параметр группировки."
        )

    # Группировка
    groups = {}
    no_value = []

    for e in target_elems:
        key = get_group_key(e, param_name)
        if key:
            if key not in groups:
                groups[key] = []
            groups[key].append(e)
        else:
            no_value.append(e)

    if not groups:
        fail(
            u"У элементов выбранных категорий нет значений в параметре «{}».\n"
            u"Проверьте имя параметра.".format(param_name)
        )

    solid_id = get_solid_fill_id()

    # Перемешиваем порядок групп, чтобы соседние значения были контрастны
    group_keys = list(groups.keys())
    random.Random(len(group_keys)).shuffle(group_keys)

    palette = unique_colors(len(group_keys))
    group_colors = {}
    for i, k in enumerate(group_keys):
        group_colors[k] = palette[i]

    applied = 0

    t = Transaction(doc, u"PP: Окрасить рандомно")
    t.Start()

    for key, elems in groups.items():
        ogs = make_ogs(group_colors[key], solid_id)
        for e in elems:
            try:
                view.SetElementOverrides(e.Id, ogs)
                applied += 1
            except:
                pass

    clean_ogs = OverrideGraphicSettings()
    cleaned = 0
    for e in no_value:
        try:
            view.SetElementOverrides(e.Id, clean_ogs)
            cleaned += 1
        except:
            pass

    t.Commit()

    lines = [
        u"Готово.",
        u"",
        u"Вид: {}".format(view.Name),
        u"Категории: {}".format(cat_names),
        u"Параметр: {}".format(param_name),
        u"Групп (цветов): {}".format(len(groups)),
        u"Элементов окрашено: {}".format(applied),
        u"Без значения / сброшено: {}".format(cleaned)
    ]
    pp_wpf.show_report(
        u"\n".join(lines),
        title=u"Готово",
        subtitle=TITLE
    )

except System.OperationCanceledException:
    rollback()

except SystemExit:
    pass

except Stop as ex:
    rollback()

    pp_wpf.show_report(
        unicode(ex),
        title=u"Не выполнено",
        subtitle=TITLE,
        is_error=True
    )

except Exception as ex:
    rollback()

    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TITLE,
        is_error=True
    )
