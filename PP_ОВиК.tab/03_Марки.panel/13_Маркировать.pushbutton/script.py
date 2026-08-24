# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

# «Маркировать по шаблону» — ручная расстановка марок.
#
# Аналог родного «Маркировать по категории», но тип марки выбирается по
# правилам шаблона (Инструменты меток / кнопка «Шаблон марок»):
#   - для элемента ищем правило по семейству/типу (find_rule_for_element);
#   - есть правило и марка загружена — ставим марку нужного типа;
#   - правила нет — ставим дефолтной маркой категории (TM_ADDBY_CATEGORY).
#
# Работа: клик по элементу -> клик по точке головы марки. Esc на элементе —
# выход; Esc на точке — пропустить текущий элемент.

import sys

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, IndependentTag, Reference,
    TagMode, TagOrientation, ElementId
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

import pp_wpf
import pp_paint_dialog
from pp_settings import load_settings
import pp_tagrules as tr


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView


TOOL_TITLE = u"Маркировать по шаблону"


settings = load_settings()
rules = settings.get("tag_rules", [])


# ── Выноска: один переключатель на всю сессию ────────────────────────────────

# Пустой шаблон — не ошибка, а режим работы: инструмент всё поставит типом
# по умолчанию. Поэтому предупреждение живёт в описании окна, а не отдельным
# сообщением перед ним.
if rules:
    _subtitle = (
        u"Тип марки берётся из шаблона «Инструменты меток». Если правила для "
        u"элемента нет, ставится тип по умолчанию для категории."
    )
else:
    _subtitle = (
        u"Шаблон меток пуст — марки будут ставиться типом по умолчанию для "
        u"категории. Чтобы марки ставились по правилам, откройте «Инструменты "
        u"меток» и назначьте типы марок."
    )

_options = pp_paint_dialog.ask({
    u"title": TOOL_TITLE,
    u"subtitle": _subtitle,
    u"reset": False,
    u"switches": [{
        u"key": u"leader",
        u"label": u"Ставить марки с выноской",
        u"hint": u"Переключатель действует до конца работы инструмента.",
        u"value": True,
    }],
    u"ready_label": u"Маркировка",
        u"run_label": u"Начать",
})

if _options is None:
    sys.exit(0)

add_leader = bool(_options[u"switches"][u"leader"])


# ── Фильтр выбора: только категории шаблона ──────────────────────────────────

class TemplateElementFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return tr.is_allowed_element(elem)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


_filter = TemplateElementFilter()


# ── Счётчики отчёта ──────────────────────────────────────────────────────────

placed_by_rule = 0
placed_default = 0
no_rule = {}          # cat/family/type -> кол-во (нет правила)
tag_not_loaded = {}   # tag_family/tag_type -> кол-во (правило есть, марки нет)
errors = []


def _place_one(ref, point):
    """Ставит одну марку. Возвращает строку-категорию результата."""
    global placed_by_rule, placed_default

    el = doc.GetElement(ref.ElementId)

    tag = IndependentTag.Create(
        doc,
        view.Id,
        Reference(el),
        add_leader,
        TagMode.TM_ADDBY_CATEGORY,
        TagOrientation.Horizontal,
        point)

    if tag is None:
        raise Exception(u"Не удалось создать марку")

    tag.TagHeadPosition = point

    rule = tr.find_rule_for_element(rules, el)

    if rule is None:
        key = u"{} / {} / {}".format(
            tr.display_name(tr.get_element_category_enum(el)) or u"?",
            tr.get_family_name(el) or u"?",
            tr.get_type_name(el) or u"?")
        no_rule[key] = no_rule.get(key, 0) + 1
        placed_default += 1
        return

    cat_enum = tr.get_element_category_enum(el)
    sym = tr.resolve_tag_symbol(
        doc, cat_enum, rule.get("tag_family"), rule.get("tag_type"))

    if sym is None:
        key = u"{} / {}".format(
            rule.get("tag_family") or u"?", rule.get("tag_type") or u"?")
        tag_not_loaded[key] = tag_not_loaded.get(key, 0) + 1
        placed_default += 1
        return

    if not sym.IsActive:
        sym.Activate()
        doc.Regenerate()

    tag.ChangeTypeId(sym.Id)
    placed_by_rule += 1


# ── Основной цикл ────────────────────────────────────────────────────────────

while True:
    # 1) выбор элемента (Esc — завершить инструмент)
    try:
        ref = uidoc.Selection.PickObject(
            ObjectType.Element, _filter,
            u"Выберите элемент для маркировки (Esc — завершить)")
    except OperationCanceledException:
        break
    except Exception:
        break

    # 2) точка головы марки (Esc — пропустить этот элемент)
    try:
        point = uidoc.Selection.PickPoint(
            u"Укажите положение марки (Esc — пропустить элемент)")
    except OperationCanceledException:
        continue
    except Exception as ex:
        pp_wpf.show_report(
            u"В этом виде нельзя указать точку марки.\n\n{}".format(unicode(ex)),
            title=u"Точку указать нельзя",
            subtitle=TOOL_TITLE,
            is_error=True)
        break

    # 3) создание марки
    t = Transaction(doc, u"PP: Маркировать по шаблону")
    t.Start()
    try:
        _place_one(ref, point)
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        errors.append(u"{}: {}".format(ref.ElementId.IntegerValue, unicode(ex)))


# ── Отчёт ────────────────────────────────────────────────────────────────────

total = placed_by_rule + placed_default

if total == 0 and not errors:
    # ничего не поставили и без ошибок — тихо выходим
    sys.exit(0)

lines = [u"Поставлено марок: {}".format(total),
         u"  по правилам шаблона: {}".format(placed_by_rule),
         u"  типом по умолчанию: {}".format(placed_default)]

if no_rule:
    lines.append(u"")
    lines.append(u"Нет правила — поставлено по умолчанию")
    lines.append(u"(добавьте в «Инструменты меток»):")
    for k in sorted(no_rule.keys()):
        lines.append(u"  • {}  (×{})".format(k, no_rule[k]))

if tag_not_loaded:
    lines.append(u"")
    lines.append(u"Тип марки из правила не загружен в проект")
    lines.append(u"(поставлено по умолчанию):")
    for k in sorted(tag_not_loaded.keys()):
        lines.append(u"  • {}  (×{})".format(k, tag_not_loaded[k]))

if errors:
    lines.append(u"")
    lines.append(u"Ошибки:")
    for msg in errors[:10]:
        lines.append(u"  • {}".format(msg))

pp_wpf.show_report(u"\n".join(lines), title=u"Готово", subtitle=TOOL_TITLE)
