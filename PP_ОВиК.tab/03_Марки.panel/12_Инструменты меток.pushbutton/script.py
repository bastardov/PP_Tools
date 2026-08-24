# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import os
import sys

import pp_wpf
import pp_tag_sample
from pp_settings import load_settings, save_settings
import pp_tagrules as tr


_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

from pp_tagtools_window import ask_tag_rules


TOOL_TITLE = u"Инструменты меток"

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


settings = load_settings()

# Рабочие копии: на диск уходит только то, что вернуло окно по «Сохранить».
rules = [dict(r) for r in settings.get("tag_rules", [])]  # все категории

# Воздуховоды ушли в свою вкладку: старые правила по семейству/типу для них
# больше не действуют и убираются, чтобы не работать втихую.
dropped_rules = tr.strip_size_categories(rules)

size_presets = tr.normalize_presets(settings.get("tag_size_presets"))
size_active = settings.get("tag_size_active") or u""


def load_marks(cat_enum):
    u"""Уникальные пары (семейство марки, тип марки), загруженные в проект."""
    seen = set()
    items = []

    for fam, tname, sym in tr.collect_tag_symbols(doc, cat_enum):
        if not fam or not tname:
            continue
        key = (fam, tname)
        if key in seen:
            continue
        seen.add(key)
        items.append(key)

    items.sort()

    return items


def load_types(cat_enum):
    u"""Семейства категории и их типы: {семейство: [тип, ...]}."""
    grouped = {}

    for fam, tname, sym in tr.collect_element_types(doc, cat_enum):
        grouped.setdefault(fam, [])
        if tname and tname not in grouped[fam]:
            grouped[fam].append(tname)

    return grouped


def take_from_view(family_level):
    u"""Уйти на план за образцом и записать правило. Возврат: (текст, узел)."""
    # Выделение НЕ используем: пользователь нажал кнопку именно чтобы пойти и
    # показать марку, а не чтобы применилось что-то выделенное до открытия.
    tags, scope = pp_tag_sample.collect(
        uidoc, use_selection=False,
        prompt=u"Выберите марки-образцы и нажмите «Готово»")

    if not tags:
        return (u"Выбор отменён — шаблон не изменился.", None)

    picked = pp_tag_sample.apply(doc, rules, tags, family_level)

    return (pp_tag_sample.summary(picked), picked.get("last_key"))


try:
    # Кнопки «С плана» закрывают окно, чтобы дать выбрать марку в модели,
    # и оно тут же открывается снова с прежним состоянием.
    state = {}
    result = None

    while True:
        result = ask_tag_rules(rules, tr.CONFIG_ENUM_NAMES, load_marks,
                               load_types, size_presets, size_active, state)

        if result is None:
            break

        rules = result.get("rules") or []
        size_presets = result.get("presets") or []
        size_active = result.get("active") or u""
        state = result.get("state") or {}

        pick = result.get("pick")

        if not pick:
            break

        message, node_key = take_from_view(pick == u"family")

        state["message"] = message
        state["focus"] = node_key

        if node_key:
            state["cat"] = node_key[0]

    if result is not None:
        saved_rules = result.get("rules") or []
        saved_presets = result.get("presets") or []
        saved_active = result.get("active") or u""

        settings["tag_rules"] = saved_rules
        settings["tag_size_presets"] = saved_presets
        settings["tag_size_active"] = saved_active
        save_settings(settings)

        active_rows = len(tr.active_size_rules(saved_presets, saved_active))

        lines = [u"Шаблон меток сохранён.",
                 u"",
                 u"Правил по семейству и типу: {}".format(len(saved_rules)),
                 u"Пресетов воздуховодов: {}".format(len(saved_presets)),
                 u"Строк в активном пресете «{}»: {}".format(
                     saved_active or u"—", active_rows)]

        if dropped_rules:
            lines.append(u"")
            lines.append(
                u"Убрано старых правил по воздуховодам: {}. Теперь воздуховоды "
                u"настраиваются во вкладке «Воздуховоды».".format(dropped_rules))

        pp_wpf.show_report(u"\n".join(lines), title=u"Готово",
                           subtitle=TOOL_TITLE)

except Exception as ex:
    pp_wpf.show_report(
        u"Ошибка сохранения:\n\n{}".format(unicode(ex)),
        title=u"Ошибка",
        subtitle=TOOL_TITLE,
        is_error=True
    )
