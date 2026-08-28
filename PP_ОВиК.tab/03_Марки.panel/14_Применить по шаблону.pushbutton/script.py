# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import sys

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, IndependentTag, FilteredElementCollector
)

import pp_wpf
import pp_paint_dialog
from pp_settings import load_settings, save_settings
from pp_tags import get_tagged_element_from_tag
import pp_tagrules as tr


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView


TOOL_TITLE = u"Применить по шаблону"


def fail(message, title):
    u"""Показать окно ошибки и выйти: общего try в скрипте нет."""
    pp_wpf.show_report(message, title=title, subtitle=TOOL_TITLE, is_error=True)
    sys.exit(0)


settings = load_settings()
rules = list(settings.get("tag_rules", []))

# Воздуховоды настраиваются только размерными правилами: старые записи по
# семейству/типу для них игнорируем, чтобы не спорили с пресетом.
tr.strip_size_categories(rules)

# Размерные правила активного пресета: у воздуховодов и труб ширина марки
# зависит от длины надписи, а не от семейства и типа элемента.
size_presets = tr.normalize_presets(settings.get("tag_size_presets"))
size_active = settings.get("tag_size_active") or u""
size_rules = tr.active_size_rules(size_presets, size_active)

if not rules and not size_rules:
    fail(
        u"Шаблон меток пуст.\n\nОткройте «Инструменты меток» и назначьте\n"
        u"марки для семейств/типов либо соберите пресет в режиме\n"
        u"«По размеру».",
        u"Шаблон меток пуст")


def read_tag_text(tag):
    u"""Надпись марки, как она нарисована на виде. None — прочитать не вышло."""
    try:
        return tag.TagText
    except:
        return None


def get_target_tags():
    """Если выделены марки — работаем по ним, иначе по всему активному виду."""
    sel_ids = list(uidoc.Selection.GetElementIds())
    sel_tags = []
    for eid in sel_ids:
        el = doc.GetElement(eid)
        if isinstance(el, IndependentTag):
            sel_tags.append(el)
    if sel_tags:
        return sel_tags, u"выделение"
    all_tags = list(FilteredElementCollector(doc, view.Id).OfClass(IndependentTag))
    return all_tags, u"активный вид"


tags, scope = get_target_tags()


# ── Категории ────────────────────────────────────────────────────────────────
#
# Спрашиваем только при работе по всему виду: если марки выделены, юзер уже
# показал руками, что править, и второй вопрос лишний.

CATS_KEY = "tag_apply_categories"   # [] = все категории


def ask_categories():
    u"""Окно с галочками категорий. Возврат: set имён enum. Отказ — выход."""
    saved = settings.get(CATS_KEY)

    if not isinstance(saved, list):
        saved = []

    items = [(tr.display_name(enum_name), enum_name,
              (not saved) or (enum_name in saved))
             for enum_name in tr.ENUM_NAMES]

    # Пустой набор оставил бы окно с неактивной кнопкой запуска: считаем,
    # что сохранённые категории потерялись, и открываем со всеми.
    if not [row for row in items if row[2]]:
        items = [(row[0], row[1], True) for row in items]

    options = pp_paint_dialog.ask({
        u"title": TOOL_TITLE,
        u"subtitle": (
            u"Меняет тип уже поставленных марок на активном виде по шаблону "
            u"«Инструменты меток». Снимите галочки с категорий, которые "
            u"трогать не надо."
        ),
        u"reset": False,
        u"categories": items,
        u"categories_hint": u"Отмеченные категории запомнятся до следующего раза.",
        u"ready_label": u"Смена типа марок",
        u"run_label": u"Применить",
    })

    if options is None:
        sys.exit(0)

    chosen = set([value for _label, value in options[u"categories"]])

    settings[CATS_KEY] = sorted(chosen)

    try:
        save_settings(settings)
    except Exception:
        pass   # не смогли запомнить выбор — не повод отменять работу

    return chosen


allowed_enums = None   # None = без ограничения по категориям

if scope != u"выделение":
    allowed_enums = ask_categories()

changed = 0
changed_by_size = 0
already = 0
no_text = 0
processed = 0
covered_by_rule = 0
skipped_by_cat = 0
skipped_no_range = {}
skipped_no_tag = {}
uncovered_types = {}
errors = []

t = Transaction(doc, u"PP: Применить марки по шаблону")
t.Start()
try:
    for tag in tags:
        try:
            el = get_tagged_element_from_tag(doc, tag)
            if el is None or not tr.is_allowed_element(el):
                continue

            cat_enum = tr.get_element_category_enum(el)

            if allowed_enums is not None and cat_enum not in allowed_enums:
                skipped_by_cat += 1
                continue

            processed += 1

            rule = None
            matched_by_size = False
            length = 0

            # Длину надписи меряем ОДИН раз, до смены типа: если у марок разный
            # набор label, после ChangeTypeId текст изменится и повторный замер
            # начнёт качать тип туда-обратно.
            size_capable = tr.has_size_rules(size_rules, cat_enum)

            if size_capable:
                length = tr.measure_tag_text(read_tag_text(tag))

                if length:
                    rule = tr.find_size_rule(
                        size_rules, cat_enum, length,
                        tr.get_family_name(el), tr.get_type_name(el))
                    matched_by_size = rule is not None

            if rule is None:
                rule = tr.find_rule_for_element(rules, el)

            if rule is None:
                key = u"{} / {} / {}".format(
                    tr.display_name(cat_enum) or u"?",
                    tr.get_family_name(el) or u"?",
                    tr.get_type_name(el) or u"?")
                uncovered_types[key] = uncovered_types.get(key, 0) + 1

                if size_capable and length:
                    key = u"{} / {} / надпись {} знаков".format(
                        tr.display_name(cat_enum) or u"?",
                        tr.get_family_name(el) or u"?", length)
                    skipped_no_range[key] = skipped_no_range.get(key, 0) + 1
                elif size_capable:
                    no_text += 1
                continue

            covered_by_rule += 1

            sym = tr.resolve_tag_symbol(
                doc, cat_enum, rule.get("tag_family"), rule.get("tag_type"))
            if sym is None:
                key = u"{} / {} / {}".format(
                    tr.display_name(cat_enum) or u"?",
                    rule.get("tag_family") or u"?",
                    rule.get("tag_type") or u"?")
                skipped_no_tag[key] = skipped_no_tag.get(key, 0) + 1
                continue

            if tag.GetTypeId() == sym.Id:
                already += 1
                continue

            if not sym.IsActive:
                sym.Activate()
                doc.Regenerate()

            tag.ChangeTypeId(sym.Id)
            changed += 1

            if matched_by_size:
                changed_by_size += 1

        except Exception as ex:
            errors.append(u"{}: {}".format(tag.Id.IntegerValue, unicode(ex)))

    t.Commit()
except Exception as ex:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    fail(unicode(ex), u"Ошибка")


# ── Отчёт ────────────────────────────────────────────────────────────────────

if allowed_enums is None:
    cats_line = u"Категории: все (работа по выделению)"
elif len(allowed_enums) == len(tr.ENUM_NAMES):
    cats_line = u"Категории: все"
else:
    cats_line = u"Категории: {}".format(u", ".join(
        [tr.display_name(name) for name in tr.ENUM_NAMES if name in allowed_enums]))

lines = [u"Область: {}".format(scope),
         cats_line,
         u"Марок обработано: {}".format(processed),
         u"Изменён тип: {}".format(changed)]

if size_rules:
    lines.append(u"  из них по размеру надписи: {}".format(changed_by_size))

lines.append(u"Уже по шаблону: {}".format(already))

if skipped_by_cat:
    lines.append(u"Пропущено — категория снята: {}".format(skipped_by_cat))

if size_rules:
    lines.append(u"Пресет по размеру: «{}» (строк {})".format(
        size_active or u"—", len(size_rules)))

coverage_percent = (100.0 * covered_by_rule / processed) if processed else 0.0
coverage_text = u"{:.1f}".format(coverage_percent).replace(u".", u",")

lines.append(u"")
lines.append(u"Покрытие правилами:")
lines.append(u"  Покрыто элементов: {} из {} ({}%)".format(
    covered_by_rule, processed, coverage_text))

if uncovered_types:
    lines.append(u"  Семейства и типы без применимого правила:")
    for k in sorted(uncovered_types.keys()):
        lines.append(u"    • {}  (×{})".format(k, uncovered_types[k]))
else:
    lines.append(u"  Семейства и типы без применимого правила: нет")

if skipped_no_tag:
    lines.append(u"  Отсутствующие типы меток (не загружены в проект):")
    for k in sorted(skipped_no_tag.keys()):
        lines.append(u"    • {}  (×{})".format(k, skipped_no_tag[k]))
else:
    lines.append(u"  Отсутствующие типы меток: нет")

if skipped_no_range:
    lines.append(u"")
    lines.append(u"Нет строки под такую длину надписи "
                 u"(добавьте её в пресет):")
    for k in sorted(skipped_no_range.keys()):
        lines.append(u"  • {}  (×{})".format(k, skipped_no_range[k]))

if no_text:
    lines.append(u"")
    lines.append(u"Надпись прочитать не удалось: {}. Обычно это пустая марка — "
                 u"параметр не заполнен.".format(no_text))

if errors:
    lines.append(u"")
    lines.append(u"Ошибки:")
    for msg in errors[:10]:
        lines.append(u"  • {}".format(msg))

pp_wpf.show_report(u"\n".join(lines), title=u"Готово", subtitle=TOOL_TITLE)
