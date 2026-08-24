# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

# «Марки помещений Номер» — приводит план к маркам помещений типа «Номер».
#
# Делает два действия за один клик:
#   1) все марки помещений на активном виде (и связи, и свои) переводит
#      на тип марки «Номер»;
#   2) непомеченные помещения из RVT-связей маркирует этим же типом.
#
# Тип марки ищется по имени «Номер». Если такого нет — предлагается выбрать
# из загруженных типов, выбор запоминается в конфиге pyRevit.
#
# Важно: для ссылок на связь TagMode.TM_ADDBY_CATEGORY не работает
# («no loaded tag type…»), поэтому используется перегрузка IndependentTag.Create
# с явным tagTypeId.

import clr

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    Transaction, IndependentTag, Reference,
    TagOrientation, ElementId,
    FilteredElementCollector, BuiltInCategory, RevitLinkInstance,
    Level, FamilySymbol, BuiltInParameter
)

from pyrevit import script

import sys

import pp_param_picker
import pp_wpf


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument
view = doc.ActiveView

TITLE = u"Марки помещений Номер"


def fail(message, title):
    u"""Показать окно ошибки и выйти (скрипт без общего try — грабля 13 UI.md)."""
    pp_wpf.show_report(message, title=title, subtitle=TITLE, is_error=True)
    sys.exit(0)
WANTED_NAME = u"Номер"

cfg = script.get_config()

# допуск по высоте под уровнем вида (в футах): ~1 м вниз
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
        u"Откройте план этажа и повторите.",
        u"Неподходящий вид")


# ── Тип марки «Номер» ────────────────────────────────────────────────────────

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

_by_name = {}
for s in _room_tag_syms:
    _by_name[_sym_name(s).strip()] = s

tag_sym = None

# 1) точное имя «Номер»
tag_sym = _by_name.get(WANTED_NAME)

# 2) без учёта регистра
if tag_sym is None:
    for nm, s in _by_name.items():
        if nm.lower() == WANTED_NAME.lower():
            tag_sym = s
            break

# 3) ранее сохранённый выбор
if tag_sym is None:
    try:
        saved = cfg.get_option("tag_type_name", u"")
    except Exception:
        saved = u""
    if saved:
        tag_sym = _by_name.get(saved)

# 4) спросить у пользователя и запомнить
if tag_sym is None:
    _names = sorted([n for n in _by_name.keys() if n])
    chosen = pp_param_picker.ask(
        _names,
        title=u"Тип марки «Номер» не найден — выберите тип",
        subtitle=TITLE,
        empty_text=u"В проекте нет ни одного типа марки помещений")

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
    _band_high = _view_elev + 328.0


# ── Область подрезки ─────────────────────────────────────────────────────────

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


# ── Существующие марки помещений на виде ─────────────────────────────────────

_room_tags = list(
    FilteredElementCollector(doc, view.Id)
    .OfCategory(BuiltInCategory.OST_RoomTags)
    .WhereElementIsNotElementType().ToElements())

# ── Связи ────────────────────────────────────────────────────────────────────

links = []
for li in FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements():
    try:
        if li.IsHidden(view):
            continue
        if li.GetLinkDocument() is None:
            continue
        links.append(li)
    except Exception:
        pass


# ── Уже помеченные помещения связи ───────────────────────────────────────────
# Связь «марка → помещение связи» читается двумя путями:
#   1) GetTaggedReferences().LinkedElementId — прямая ссылка;
#   2) геометрия — какое помещение связи лежит под точкой марки.
# Второй путь нужен после перепланировок: помещения у архитектора удаляются и
# создаются заново, прямая ссылка у старой марки перестаёт читаться, и без
# геометрии инструмент ставил вторую марку поверх уже стоящей.

_existing_linked = set()   # (link_instance_id, linked_room_id)
_unresolved_tags = 0

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


for tag in _room_tags:
    _found = False

    # 1) прямая ссылка на элемент связи
    try:
        for ref in tag.GetTaggedReferences():
            lid = ref.LinkedElementId
            if lid and lid != ElementId.InvalidElementId:
                _existing_linked.add(
                    (ref.ElementId.IntegerValue, lid.IntegerValue))
                _found = True
    except Exception:
        pass

    # 2) геометрия — что лежит под маркой
    if not _found:
        pair = _linked_room_at(_tag_point(tag))
        if pair is not None:
            _existing_linked.add(pair)
            _found = True

    if not _found:
        _unresolved_tags += 1


# ── Основной проход ──────────────────────────────────────────────────────────

retyped = 0
already_ok = 0
placed = 0
errors = []

t = Transaction(doc, u"PP: Марки помещений Номер")
t.Start()
try:
    if not tag_sym.IsActive:
        tag_sym.Activate()
        doc.Regenerate()

    # 1) перетипизация существующих марок помещений
    for tag in _room_tags:
        try:
            if tag.GetTypeId() == tag_type_id:
                already_ok += 1
                continue
            tag.ChangeTypeId(tag_type_id)
            retyped += 1
        except Exception as ex:
            errors.append(u"марка {}: {}".format(
                tag.Id.IntegerValue, unicode(ex)))

    # 2) простановка марок на непомеченные помещения связи
    for li in links:
        ldoc = li.GetLinkDocument()
        transform = li.GetTotalTransform()

        rooms = FilteredElementCollector(ldoc) \
            .OfCategory(BuiltInCategory.OST_Rooms) \
            .WhereElementIsNotElementType().ToElements()

        for room in rooms:
            try:
                if room.Area <= 0:
                    continue
                loc = room.Location
                if loc is None:
                    continue

                pt_host = transform.OfPoint(loc.Point)

                if not (_band_low <= pt_host.Z <= _band_high):
                    continue
                if not _in_crop(pt_host):
                    continue
                if (li.Id.IntegerValue, room.Id.IntegerValue) in _existing_linked:
                    continue

                link_ref = Reference(room).CreateLinkReference(li)

                tag = IndependentTag.Create(
                    doc, tag_type_id, view.Id, link_ref,
                    False, TagOrientation.Horizontal, pt_host)

                if tag is not None:
                    tag.TagHeadPosition = pt_host
                    placed += 1
                    _existing_linked.add(
                        (li.Id.IntegerValue, room.Id.IntegerValue))
            except Exception as ex:
                errors.append(u"{}: {}".format(
                    room.Id.IntegerValue, unicode(ex)))

    t.Commit()
except Exception as ex:
    if t.HasStarted() and not t.HasEnded():
        t.RollBack()
    fail(unicode(ex), u"Ошибка выполнения")


# ── Отчёт ────────────────────────────────────────────────────────────────────

lines = [u"Тип марки: {}".format(tag_type_name),
         u"",
         u"Перетипизировано марок: {}".format(retyped),
         u"Поставлено новых марок: {}".format(placed)]

if already_ok:
    lines.append(u"Уже были нужного типа: {}".format(already_ok))

lines.append(u"")
lines.append(u"Марок на виде было: {}".format(len(_room_tags)))
if _unresolved_tags:
    lines.append(
        u"из них не удалось связать с помещением: {}".format(_unresolved_tags))

if retyped == 0 and placed == 0 and already_ok == 0:
    lines.append(u"")
    lines.append(u"На этом виде не нашлось марок и помещений связи.")
    lines.append(u"Проверьте уровень плана и область подрезки.")

if errors:
    lines.append(u"")
    lines.append(u"Ошибки ({}):".format(len(errors)))
    for msg in errors[:10]:
        lines.append(u"  • {}".format(msg))

pp_wpf.show_report(u"\n".join(lines), title=u"Готово", subtitle=TITLE)
