# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

# «Номер Имя» (марки пространств) — в один клик маркирует все
# пространства текущей модели, видимые на активном плане, маркой типа
# «Номер_Наименование». Уже помеченные пространства пропускаются.
#
# Пространства — свои (не из связи), поэтому берём их view-фильтром
# (что видно на виде, то и маркируем) и ставим родной SpaceTag через
# doc.Create.NewSpaceTag, затем переводим на нужный тип.
#
# Тип марки ищется по имени «Номер_Наименование». Если такого нет —
# предлагается выбрать из загруженных, выбор запоминается в конфиге pyRevit.

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, ElementId, UV,
    FilteredElementCollector, BuiltInCategory,
    FamilySymbol, BuiltInParameter,
    IndependentTag, Reference, TagOrientation
)

from pyrevit import script

import sys

import pp_param_picker
import pp_wpf


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

TITLE = u"Марки пространств: Номер Имя"


def fail(message, title):
    u"""Показать окно ошибки и выйти (скрипт без общего try — грабля 13 UI.md)."""
    pp_wpf.show_report(message, title=title, subtitle=TITLE, is_error=True)
    sys.exit(0)
WANTED_NAME = u"Номер_Наименование"

cfg = script.get_config()


# ── Проверка вида ────────────────────────────────────────────────────────────

gen_level = None
try:
    gen_level = view.GenLevel
except Exception:
    gen_level = None

if gen_level is None:
    fail(
        u"Активный вид не является планом с уровнем.\n\n"
        u"Откройте план этажа и повторите.",
        u"Неподходящий вид")


# ── Тип марки пространств ────────────────────────────────────────────────────

def _sym_name(sym):
    try:
        p = sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p:
            return p.AsString() or u""
    except Exception:
        pass
    try:
        return sym.Name or u""
    except Exception:
        return u""


_tag_syms = list(
    FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_MEPSpaceTags)
    .OfClass(FamilySymbol).ToElements())

if not _tag_syms:
    fail(
        u"В проекте не загружено ни одного типа марки пространств.\n\n"
        u"Загрузите семейство марки пространств и повторите.",
        u"Нет типов марок")

_by_name = {}
for s in _tag_syms:
    _by_name[_sym_name(s).strip()] = s

tag_sym = _by_name.get(WANTED_NAME)

# без учёта регистра
if tag_sym is None:
    for nm, s in _by_name.items():
        if nm.lower() == WANTED_NAME.lower():
            tag_sym = s
            break

# ранее сохранённый выбор
if tag_sym is None:
    try:
        saved = cfg.get_option("tag_type_name", u"")
    except Exception:
        saved = u""
    if saved:
        tag_sym = _by_name.get(saved)

# спросить и запомнить
if tag_sym is None:
    _names = sorted([n for n in _by_name.keys() if n])
    chosen = pp_param_picker.ask(
        _names,
        title=u"Тип марки «Номер_Наименование» не найден — выберите тип",
        subtitle=TITLE,
        empty_text=u"В проекте нет ни одного типа марки пространств")

    if not chosen:
        sys.exit(0)

    tag_sym = _by_name.get(chosen)
    try:
        cfg.tag_type_name = chosen
        script.save_config()
    except Exception:
        pass

if tag_sym is None:
    fail(u"Не удалось определить тип марки.", u"Тип марки не выбран")

tag_type_name = _sym_name(tag_sym)
tag_type_id = tag_sym.Id


# ── Пространства, видимые на виде ────────────────────────────────────────────

spaces = list(
    FilteredElementCollector(doc, view.Id)
    .OfCategory(BuiltInCategory.OST_MEPSpaces)
    .WhereElementIsNotElementType().ToElements())

if not spaces:
    fail(
        u"На активном виде не найдено пространств.\n\n"
        u"Проверьте, что пространства размещены и видимы на этом плане.",
        u"Нет пространств на виде")


# ── Уже помеченные пространства ──────────────────────────────────────────────

# Связь «марка → пространство» читается несколькими путями подряд:
#   1) SpaceTag.Space               — родная марка пространства;
#   2) IndependentTag.TaggedLocalElementId;
#   3) IndependentTag.GetTaggedReferences();
#   4) геометрия — какое пространство лежит под точкой марки.
# Последний путь страхует случаи, когда прямая связь не читается (например,
# после перепланировок), иначе марка не засчитывается и встаёт дубль.

_existing_tags = list(
    FilteredElementCollector(doc, view.Id)
    .OfCategory(BuiltInCategory.OST_MEPSpaceTags)
    .WhereElementIsNotElementType().ToElements())

# фаза вида — нужна для поиска пространства по точке
_view_phase = None
try:
    _ph = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
    if _ph:
        _view_phase = doc.GetElement(_ph.AsElementId())
except Exception:
    _view_phase = None


def _tag_point(tg):
    u"""Точка марки: голова, иначе позиция размещения."""
    try:
        p = tg.TagHeadPosition
        if p is not None:
            return p
    except Exception:
        pass
    try:
        loc = tg.Location
        if loc is not None:
            return loc.Point
    except Exception:
        pass
    return None


def _space_at(pt):
    u"""Пространство под точкой (с учётом фазы вида)."""
    if pt is None:
        return None
    if _view_phase is not None:
        try:
            sp = doc.GetSpaceAtPoint(pt, _view_phase)
            if sp is not None:
                return sp
        except Exception:
            pass
    try:
        return doc.GetSpaceAtPoint(pt)
    except Exception:
        return None


_tagged_ids = set()
_unresolved_tags = 0

for tg in _existing_tags:
    _ids = []

    # 1) родная марка пространства
    try:
        sp = tg.Space
        if sp is not None:
            _ids.append(sp.Id.IntegerValue)
    except Exception:
        pass

    # 2) прямая ссылка IndependentTag
    if not _ids:
        try:
            eid = tg.TaggedLocalElementId
            if eid and eid != ElementId.InvalidElementId:
                _ids.append(eid.IntegerValue)
        except Exception:
            pass

    # 3) все ссылки марки
    if not _ids:
        try:
            for ref in tg.GetTaggedReferences():
                if ref.ElementId and ref.ElementId != ElementId.InvalidElementId:
                    _ids.append(ref.ElementId.IntegerValue)
        except Exception:
            pass

    # 4) геометрия — что лежит под маркой
    if not _ids:
        sp = _space_at(_tag_point(tg))
        if sp is not None:
            _ids.append(sp.Id.IntegerValue)

    if _ids:
        for _i in _ids:
            _tagged_ids.add(_i)
    else:
        _unresolved_tags += 1


# ── Основной проход ──────────────────────────────────────────────────────────

placed = 0
skipped_existing = 0
skipped_unplaced = 0
errors = []

t = Transaction(doc, u"PP: Марки пространств Номер_Наименование")
t.Start()
try:
    if not tag_sym.IsActive:
        tag_sym.Activate()
        doc.Regenerate()

    for sp in spaces:
        try:
            # только размещённые
            try:
                if sp.Area <= 0:
                    skipped_unplaced += 1
                    continue
            except Exception:
                pass

            if sp.Id.IntegerValue in _tagged_ids:
                skipped_existing += 1
                continue

            loc = sp.Location
            if loc is None:
                skipped_unplaced += 1
                continue

            pt = loc.Point

            tag = None
            try:
                tag = doc.Create.NewSpaceTag(sp, UV(pt.X, pt.Y), view)
            except Exception:
                tag = None

            # запасной путь — через IndependentTag
            if tag is None:
                tag = IndependentTag.Create(
                    doc, tag_type_id, view.Id, Reference(sp),
                    False, TagOrientation.Horizontal, pt)

            if tag is None:
                raise Exception(u"не удалось создать марку")

            try:
                if tag.GetTypeId() != tag_type_id:
                    tag.ChangeTypeId(tag_type_id)
            except Exception:
                pass

            placed += 1
            _tagged_ids.add(sp.Id.IntegerValue)

        except Exception as ex:
            errors.append(u"{}: {}".format(sp.Id.IntegerValue, unicode(ex)))

    t.Commit()
except Exception as ex:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    fail(unicode(ex), u"Ошибка выполнения")


# ── Отчёт ────────────────────────────────────────────────────────────────────

lines = [u"Тип марки: {}".format(tag_type_name),
         u"",
         u"Поставлено марок: {}".format(placed)]

if skipped_existing:
    lines.append(u"Пропущено (уже помечены): {}".format(skipped_existing))
if skipped_unplaced:
    lines.append(u"Пропущено (не размещены): {}".format(skipped_unplaced))

lines.append(u"")
lines.append(u"Марок на виде было: {}".format(len(_existing_tags)))
if _unresolved_tags:
    lines.append(
        u"из них не удалось связать с пространством: {}".format(
            _unresolved_tags))

if placed == 0 and skipped_existing == 0:
    lines.append(u"")
    lines.append(u"Непомеченных пространств на этом виде не нашлось.")

if errors:
    lines.append(u"")
    lines.append(u"Ошибки ({}):".format(len(errors)))
    for msg in errors[:10]:
        lines.append(u"  • {}".format(msg))

pp_wpf.show_report(u"\n".join(lines), title=u"Готово", subtitle=TITLE)
