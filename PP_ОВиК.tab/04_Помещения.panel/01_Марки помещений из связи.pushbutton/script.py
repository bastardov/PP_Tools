# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

# «Марки помещений из связи» — в один клик маркирует все помещения из RVT-связей,
# видимые на активном плане, маркой типом по умолчанию (аналог родного
# «Маркировать по категории» с галочкой «Включить элементы из связи»).
#
# Работа без диалогов: берём активный вид (должен быть план с уровнем),
# все не скрытые RVT-связи, из каждой связанной модели — размещённые помещения,
# отбираем те, что лежат на уровне вида и в области подрезки (если она включена),
# пропускаем уже помеченные и ставим IndependentTag через link-reference.

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, IndependentTag, Reference,
    TagMode, TagOrientation, ElementId,
    FilteredElementCollector, BuiltInCategory, RevitLinkInstance,
    Level, SpatialElement, FamilySymbol, BuiltInParameter
)
from Autodesk.Revit.Exceptions import OperationCanceledException

import sys

import pp_wpf


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

TITLE = u"Марки помещений из связи"


def fail(message, title):
    u"""Показать окно ошибки и выйти (скрипт без общего try — грабля 13 UI.md)."""
    pp_wpf.show_report(message, title=title, subtitle=TITLE, is_error=True)
    sys.exit(0)

# допуск по высоте под/над уровнем (в футах): ~1 м вниз, чтобы поймать
# точку помещения, лежащую чуть ниже уровня
_BAND_BELOW = 3.28


# ── Проверка вида ────────────────────────────────────────────────────────────

gen_level = None
try:
    gen_level = view.GenLevel
except Exception:
    gen_level = None

if gen_level is None:
    fail(
        u"Активный вид не является планом с уровнем.\n\n"
        u"Откройте план этажа и повторите — инструмент ставит марки на\n"
        u"помещения, видимые на текущем плане.",
        u"Неподходящий вид")


# ── Диапазон высот уровня вида ───────────────────────────────────────────────

_all_levels = sorted(
    FilteredElementCollector(doc).OfClass(Level).ToElements(),
    key=lambda l: l.Elevation)

_view_elev = gen_level.Elevation
_band_low = _view_elev - _BAND_BELOW
_band_high = None
for lvl in _all_levels:
    if lvl.Elevation > _view_elev + 0.01:
        _band_high = lvl.Elevation - 0.01
        break
if _band_high is None:
    _band_high = _view_elev + 328.0  # верхний уровень — «до бесконечности» (~100 м)


# ── Область подрезки (если активна) ──────────────────────────────────────────

_crop_active = False
_crop_inv = None
_crop_min = None
_crop_max = None
try:
    if view.CropBoxActive:
        cb = view.CropBox
        _crop_active = True
        _crop_inv = cb.Transform.Inverse
        _crop_min = cb.Min
        _crop_max = cb.Max
except Exception:
    _crop_active = False


def _in_crop(pt_host):
    if not _crop_active:
        return True
    p = _crop_inv.OfPoint(pt_host)
    return (_crop_min.X - 0.01 <= p.X <= _crop_max.X + 0.01 and
            _crop_min.Y - 0.01 <= p.Y <= _crop_max.Y + 0.01)


# ── Тип марки помещений ──────────────────────────────────────────────────────
# TM_ADDBY_CATEGORY не работает для ссылок на связь («no loaded tag type…»),
# поэтому тип марки определяем явно: сначала default проекта, иначе — первый
# загруженный тип марки помещений.

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


_room_tag_syms = list(
    FilteredElementCollector(doc)
    .OfCategory(BuiltInCategory.OST_RoomTags)
    .OfClass(FamilySymbol).ToElements())

if not _room_tag_syms:
    fail(
        u"В проекте не загружено ни одного типа марки помещений.\n\n"
        u"Загрузите семейство марки помещений и повторите.",
        u"Нет типов марок")

WANTED_NAME = u"Номер Имя"

_by_name = {}
for _s in _room_tag_syms:
    _by_name[_sym_name(_s).strip()] = _s

tag_sym = None

# 1) тип с именем «Номер Имя» (к нему приводим весь план)
tag_sym = _by_name.get(WANTED_NAME)

if tag_sym is None:
    for _nm, _s in _by_name.items():
        if _nm.lower() == WANTED_NAME.lower():
            tag_sym = _s
            break

# 2) тип по умолчанию для категории
if tag_sym is None:
    try:
        _def_id = doc.GetDefaultFamilyTypeId(
            ElementId(BuiltInCategory.OST_RoomTags))
        if _def_id and _def_id != ElementId.InvalidElementId:
            _cand = doc.GetElement(_def_id)
            if isinstance(_cand, FamilySymbol):
                tag_sym = _cand
    except Exception:
        tag_sym = None

# 3) иначе — первый загруженный
if tag_sym is None:
    tag_sym = sorted(_room_tag_syms, key=lambda s: _sym_name(s))[0]

tag_type_name = _sym_name(tag_sym)


# ── Сбор связей ──────────────────────────────────────────────────────────────

links = []
for li in FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements():
    try:
        if li.IsHidden(view):
            continue
        ldoc = li.GetLinkDocument()
        if ldoc is None:
            continue          # связь выгружена
        links.append(li)
    except Exception:
        pass

if not links:
    fail(
        u"На активном виде нет загруженных и видимых RVT-связей.",
        u"Нет связей на виде")


# ── Уже поставленные марки помещений (для пропуска) ──────────────────────────
# Связь «марка → помещение связи» читается двумя путями:
#   1) GetTaggedReferences().LinkedElementId — прямая ссылка;
#   2) геометрия — какое помещение связи лежит под точкой марки.
# Второй путь нужен после перепланировок: у архитектора помещения удаляются и
# создаются заново, прямая ссылка у старой марки перестаёт читаться, и без
# геометрии инструмент ставил вторую марку поверх уже стоящей.

_existing = set()        # (link_instance_id, linked_room_id)
_unresolved_tags = 0

_room_tags_on_view = list(
    FilteredElementCollector(doc, view.Id)
    .OfCategory(BuiltInCategory.OST_RoomTags)
    .WhereElementIsNotElementType().ToElements())

# обратные трансформации связей — для перевода точки марки в координаты связи
_link_inv = []
for li in links:
    try:
        _link_inv.append((li, li.GetTotalTransform().Inverse,
                          li.GetLinkDocument()))
    except Exception:
        pass


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


def _linked_room_at(pt_host):
    u"""Ищет (link, room) под точкой во всех связях."""
    if pt_host is None:
        return None
    for li, inv, ldoc in _link_inv:
        try:
            room = ldoc.GetRoomAtPoint(inv.OfPoint(pt_host))
            if room is not None:
                return (li.Id.IntegerValue, room.Id.IntegerValue)
        except Exception:
            pass
    return None


for tag in _room_tags_on_view:
    _found = False

    # 1) прямая ссылка на элемент связи
    try:
        for ref in tag.GetTaggedReferences():
            lid = ref.LinkedElementId
            if lid and lid != ElementId.InvalidElementId:
                _existing.add((ref.ElementId.IntegerValue, lid.IntegerValue))
                _found = True
    except Exception:
        pass

    # 2) геометрия — что лежит под маркой
    if not _found:
        pair = _linked_room_at(_tag_point(tag))
        if pair is not None:
            _existing.add(pair)
            _found = True

    if not _found:
        _unresolved_tags += 1


# ── Основной проход ──────────────────────────────────────────────────────────

placed = 0
skipped_existing = 0
skipped_offview = 0
retyped = 0
already_ok = 0
errors = []

_tag_type_id = tag_sym.Id

t = Transaction(doc, u"PP: Марки помещений из связи")
t.Start()
try:
    if not tag_sym.IsActive:
        tag_sym.Activate()
        doc.Regenerate()

    # 1) существующие марки приводим к одному типу «Номер Имя»
    # (на плане встречаются марки только с номером или только с именем)
    for _tg in _room_tags_on_view:
        try:
            if _tg.GetTypeId() == _tag_type_id:
                already_ok += 1
                continue
            _tg.ChangeTypeId(_tag_type_id)
            retyped += 1
        except Exception as ex:
            errors.append(u"марка {}: {}".format(
                _tg.Id.IntegerValue, unicode(ex)))

    # 2) непомеченные помещения связи маркируем этим же типом
    for li in links:
        ldoc = li.GetLinkDocument()
        transform = li.GetTotalTransform()

        rooms = FilteredElementCollector(ldoc) \
            .OfCategory(BuiltInCategory.OST_Rooms) \
            .WhereElementIsNotElementType().ToElements()

        for room in rooms:
            try:
                # только размещённые помещения с площадью
                if room.Area <= 0:
                    continue
                loc = room.Location
                if loc is None:
                    continue
                pt_link = loc.Point
                pt_host = transform.OfPoint(pt_link)

                # уровень вида
                if not (_band_low <= pt_host.Z <= _band_high):
                    skipped_offview += 1
                    continue
                # область подрезки
                if not _in_crop(pt_host):
                    skipped_offview += 1
                    continue
                # уже помечено
                if (li.Id.IntegerValue, room.Id.IntegerValue) in _existing:
                    skipped_existing += 1
                    continue

                link_ref = Reference(room).CreateLinkReference(li)

                tag = IndependentTag.Create(
                    doc, tag_sym.Id, view.Id, link_ref,
                    False, TagOrientation.Horizontal, pt_host)

                if tag is not None:
                    tag.TagHeadPosition = pt_host
                    placed += 1
                    _existing.add((li.Id.IntegerValue, room.Id.IntegerValue))
            except Exception as ex:
                errors.append(u"{}: {}".format(room.Id.IntegerValue, unicode(ex)))

    t.Commit()
except Exception as ex:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    fail(unicode(ex), u"Ошибка выполнения")


# ── Отчёт ────────────────────────────────────────────────────────────────────

lines = [u"Поставлено марок: {}".format(placed),
         u"Перетипизировано марок: {}".format(retyped),
         u"Тип марки: {}".format(tag_type_name)]
if already_ok:
    lines.append(u"Уже были нужного типа: {}".format(already_ok))
if skipped_existing:
    lines.append(u"Пропущено (уже помечены): {}".format(skipped_existing))

lines.append(u"")
lines.append(u"Марок на виде было: {}".format(len(_room_tags_on_view)))
if _unresolved_tags:
    lines.append(
        u"из них не удалось связать с помещением: {}".format(_unresolved_tags))
if placed == 0 and skipped_existing == 0 and retyped == 0 and already_ok == 0:
    lines.append(u"")
    lines.append(u"На этом виде не нашлось помещений связи для маркировки.")
    lines.append(u"Проверьте уровень плана и область подрезки.")
if errors:
    lines.append(u"")
    lines.append(u"Ошибки ({}):".format(len(errors)))
    for msg in errors[:10]:
        lines.append(u"  • {}".format(msg))

pp_wpf.show_report(u"\n".join(lines), title=u"Готово", subtitle=TITLE)
