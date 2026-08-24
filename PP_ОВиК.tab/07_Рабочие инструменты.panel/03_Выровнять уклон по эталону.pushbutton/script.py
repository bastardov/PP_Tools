# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

# =====================================================================
#  Выровнять уклон по эталону
#  Приводит выбранные трубы к одной высоте (горизонтали) без уклона.
#  Целевая отметка Z берётся по коннектору трубы-эталона.
#  Отводы/фитинги внутри выборки подстраиваются: временно отсоединяются,
#  сдвигаются на нужную dZ и подключаются обратно.
#  Первая версия: только трубы (OST_PipeCurves) и их фитинги.
# =====================================================================

import clr
import os
import datetime

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    XYZ,
    Line,
    Transaction,
    LocationCurve,
    ElementTransformUtils,
    ElementId,
    BuiltInCategory,
    FailureProcessingResult,
    FailureSeverity,
    IFailuresPreprocessor,
)
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException

from pyrevit import script

import pp_paint_dialog
import pp_wpf


doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

TITLE = u"Выровнять уклон по эталону"
logger = script.get_logger()
output = script.get_output()

MM_TO_FT = 1.0 / 304.8
# допуск совпадения коннекторов (мм) — в пределах него считаем «одна точка»
CONNECT_TOL_FT = 1.0 * MM_TO_FT
# порог, ниже которого сдвиг считаем нулевым (мм)
EPS_FT = 0.05 * MM_TO_FT


# ---------------------------------------------------------------------
# Логирование в файл _logs/flatten_slope.log
# ---------------------------------------------------------------------

def _log_path():
    try:
        here = os.path.dirname(__file__)
        # поднимаемся до корня расширения (…/PP_Tools.extension)
        root = here
        for _ in range(6):
            if os.path.isdir(os.path.join(root, "_logs")):
                break
            parent = os.path.dirname(root)
            if not parent or parent == root:
                break
            root = parent
        logs_dir = os.path.join(root, "_logs")
        if not os.path.isdir(logs_dir):
            try:
                os.makedirs(logs_dir)
            except:
                return None
        return os.path.join(logs_dir, "flatten_slope.log")
    except:
        return None


def flog(message):
    path = _log_path()
    if not path:
        return
    try:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f = open(path, "a")
        try:
            f.write(u"[{0}] {1}\n".format(stamp, message).encode("utf-8"))
        finally:
            f.close()
    except:
        pass


# ---------------------------------------------------------------------
# Гашение предупреждений/ошибок Revit, чтобы не всплывали модальные окна
# ---------------------------------------------------------------------

class SlopeFailurePreprocessor(IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        try:
            failuresAccessor.DeleteAllWarnings()
        except:
            pass
        try:
            for fm in failuresAccessor.GetFailureMessages():
                if fm.GetSeverity() == FailureSeverity.Error:
                    try:
                        failuresAccessor.ResolveFailure(fm)
                    except:
                        pass
        except:
            pass
        return FailureProcessingResult.Continue


# ---------------------------------------------------------------------
# Фильтр выбора: только трубы
# ---------------------------------------------------------------------

class PipeSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        try:
            return isinstance(elem, Pipe)
        except:
            return False

    def AllowReference(self, reference, point):
        return False


# ---------------------------------------------------------------------
# Работа с коннекторами
# ---------------------------------------------------------------------

def get_connectors(el):
    result = []
    try:
        for c in el.ConnectorManager.Connectors:
            result.append(c)
        return result
    except:
        pass
    try:
        for c in el.MEPModel.ConnectorManager.Connectors:
            result.append(c)
        return result
    except:
        pass
    return result


def get_conn_by_id(el, cid):
    for c in get_connectors(el):
        try:
            if c.Id == cid:
                return c
        except:
            pass
    return None


def is_pipe_fitting(el):
    try:
        cat = el.Category
        if cat is None:
            return False
        return cat.Id.IntegerValue == int(BuiltInCategory.OST_PipeFitting) or \
            cat.Id.IntegerValue == int(BuiltInCategory.OST_PipeAccessory)
    except:
        return False


# ---------------------------------------------------------------------
# Целевая отметка по коннектору эталона
# ---------------------------------------------------------------------

def reference_target_z(ref_pipe):
    """Средняя Z коннекторов эталонной трубы (для горизонтальной — её ось)."""
    zs = []
    for c in get_connectors(ref_pipe):
        try:
            zs.append(c.Origin.Z)
        except:
            pass
    if zs:
        return sum(zs) / float(len(zs))
    # запасной путь — ось трубы
    try:
        line = ref_pipe.Location.Curve
        return (line.GetEndPoint(0).Z + line.GetEndPoint(1).Z) / 2.0
    except:
        return None


def pipe_axis_z_range(pipe):
    try:
        line = pipe.Location.Curve
        z0 = line.GetEndPoint(0).Z
        z1 = line.GetEndPoint(1).Z
        return min(z0, z1), max(z0, z1)
    except:
        return None, None


# ---------------------------------------------------------------------
# Сбор сети: трубы + подключённые к ним фитинги
# ---------------------------------------------------------------------

def collect_network(pipe_ids):
    """Возвращает (fitting_ids, external_ids).
    fitting_ids — фитинги, все соседи которых внутри сети (можно двигать);
    external_ids — фитинги, у которых есть связь наружу (двигать нельзя)."""
    pipe_set = set(pid.IntegerValue for pid in pipe_ids)

    candidate_ids = set()
    for pid in pipe_ids:
        p = doc.GetElement(pid)
        if p is None:
            continue
        for c in get_connectors(p):
            try:
                for ref in c.AllRefs:
                    owner = ref.Owner
                    if owner is None:
                        continue
                    oid = owner.Id.IntegerValue
                    if oid in pipe_set:
                        continue
                    if is_pipe_fitting(owner):
                        candidate_ids.add(oid)
            except:
                pass

    safe_fittings = set()
    external = set()
    known = pipe_set | candidate_ids
    for fid in candidate_ids:
        fel = doc.GetElement(ElementId(fid))
        if fel is None:
            continue
        has_external = False
        for c in get_connectors(fel):
            try:
                if not c.IsConnected:
                    continue
                for ref in c.AllRefs:
                    owner = ref.Owner
                    if owner is None:
                        continue
                    oid = owner.Id.IntegerValue
                    if oid == fid:
                        continue
                    if oid not in known:
                        has_external = True
            except:
                pass
        if has_external:
            external.add(fid)
        else:
            safe_fittings.add(fid)
    return safe_fittings, external


# ---------------------------------------------------------------------
# Запоминание/восстановление внутренних соединений
# ---------------------------------------------------------------------

def record_internal_pairs(all_ids):
    """Список кортежей (aid, aConnId, bid, bConnId) — соединённые пары
    коннекторов внутри сети (без дублей)."""
    id_set = set(x.IntegerValue for x in all_ids)
    pairs = []
    seen = set()
    for aid in all_ids:
        ael = doc.GetElement(aid)
        if ael is None:
            continue
        for ca in get_connectors(ael):
            try:
                if not ca.IsConnected:
                    continue
                for ref in ca.AllRefs:
                    owner = ref.Owner
                    if owner is None:
                        continue
                    bid = owner.Id.IntegerValue
                    if bid == aid.IntegerValue:
                        continue
                    if bid not in id_set:
                        continue
                    key = tuple(sorted([
                        (aid.IntegerValue, ca.Id),
                        (bid, ref.Id),
                    ]))
                    if key in seen:
                        continue
                    seen.add(key)
                    pairs.append((aid.IntegerValue, ca.Id, bid, ref.Id))
            except:
                pass
    return pairs


def disconnect_pairs(pairs):
    for (aid, acid, bid, bcid) in pairs:
        try:
            ca = get_conn_by_id(doc.GetElement(ElementId(aid)), acid)
            cb = get_conn_by_id(doc.GetElement(ElementId(bid)), bcid)
            if ca is None or cb is None:
                continue
            if ca.IsConnectedTo(cb):
                ca.DisconnectFrom(cb)
        except Exception as ex:
            flog(u"disconnect fail {0}-{1}: {2}".format(aid, bid, unicode(ex)))


def reconnect_pairs(pairs):
    ok = 0
    fail = 0
    for (aid, acid, bid, bcid) in pairs:
        try:
            ca = get_conn_by_id(doc.GetElement(ElementId(aid)), acid)
            cb = get_conn_by_id(doc.GetElement(ElementId(bid)), bcid)
            if ca is None or cb is None:
                fail += 1
                continue
            if ca.IsConnectedTo(cb):
                ok += 1
                continue
            if ca.Origin.DistanceTo(cb.Origin) > CONNECT_TOL_FT:
                fail += 1
                flog(u"reconnect gap {0}-{1}: {2:.2f}mm".format(
                    aid, bid, ca.Origin.DistanceTo(cb.Origin) / MM_TO_FT))
                continue
            ca.ConnectTo(cb)
            ok += 1
        except Exception as ex:
            fail += 1
            flog(u"reconnect fail {0}-{1}: {2}".format(aid, bid, unicode(ex)))
    return ok, fail


# ---------------------------------------------------------------------
# Перестройка трубы в горизонталь на целевой Z
# ---------------------------------------------------------------------

def flatten_pipe(pipe, target_z):
    loc = pipe.Location
    if not isinstance(loc, LocationCurve):
        return False, u"нет осевой линии"
    line = loc.Curve
    p0 = line.GetEndPoint(0)
    p1 = line.GetEndPoint(1)
    n0 = XYZ(p0.X, p0.Y, target_z)
    n1 = XYZ(p1.X, p1.Y, target_z)
    if n0.DistanceTo(n1) < CONNECT_TOL_FT:
        return False, u"нулевая длина в плане"
    try:
        loc.Curve = Line.CreateBound(n0, n1)
        return True, None
    except Exception as ex:
        return False, unicode(ex)


def move_fitting_to_z(fid, target_z):
    fel = doc.GetElement(ElementId(fid))
    if fel is None:
        return False
    zs = []
    for c in get_connectors(fel):
        try:
            zs.append(c.Origin.Z)
        except:
            pass
    if not zs:
        return False
    cur_z = sum(zs) / float(len(zs))
    dz = target_z - cur_z
    if abs(dz) < EPS_FT:
        return True
    try:
        ElementTransformUtils.MoveElement(doc, ElementId(fid), XYZ(0, 0, dz))
        return True
    except Exception as ex:
        flog(u"move fitting {0} fail: {1}".format(fid, unicode(ex)))
        return False


# ---------------------------------------------------------------------
# Основной сценарий
# ---------------------------------------------------------------------

def main():
    # 1. Труба-эталон
    try:
        ref_sel = uidoc.Selection.PickObject(
            ObjectType.Element,
            PipeSelectionFilter(),
            u"Укажите трубу-ЭТАЛОН (по её высоте выровняем остальные)")
    except OperationCanceledException:
        return
    ref_pipe = doc.GetElement(ref_sel.ElementId)
    target_z = reference_target_z(ref_pipe)
    if target_z is None:
        pp_wpf.show_report(
            u"Не удалось определить высоту эталона.",
            title=u"Эталон не читается",
            subtitle=TITLE,
            is_error=True)
        return

    zmin, zmax = pipe_axis_z_range(ref_pipe)
    if zmin is not None and (zmax - zmin) > CONNECT_TOL_FT:
        cont = pp_paint_dialog.ask({
            u"title": u"Эталон сам с уклоном",
            u"subtitle": u"Труба-эталон имеет перепад {0:.1f} мм. "
                         u"Выровнять остальные трубы по её средней "
                         u"высоте?".format((zmax - zmin) / MM_TO_FT),
            u"categories": [],
            u"reset": False,
            u"run_label": u"Взять среднюю высоту",
            u"ready_label": u"Выравнивание по средней высоте эталона",
        })
        if not cont:
            return

    # 2. Трубы для выравнивания
    try:
        picks = uidoc.Selection.PickObjects(
            ObjectType.Element,
            PipeSelectionFilter(),
            u"Выберите ТРУБЫ для выравнивания (Готово по завершении)")
    except OperationCanceledException:
        return

    pipe_ids = [pr.ElementId for pr in picks]
    if not pipe_ids:
        pp_wpf.show_report(
            u"Трубы не выбраны.",
            title=u"Нечего выравнивать",
            subtitle=TITLE,
            is_error=True)
        return

    # 3. Сеть фитингов
    safe_fittings, external_fittings = collect_network(pipe_ids)
    all_ids = list(pipe_ids) + [ElementId(fid) for fid in safe_fittings]

    flog(u"START ref={0} target_z={1:.1f}mm pipes={2} safe_fit={3} ext_fit={4}".format(
        ref_pipe.Id.IntegerValue, target_z / MM_TO_FT,
        len(pipe_ids), len(safe_fittings), len(external_fittings)))

    # 4. Транзакция
    t = Transaction(doc, u"PP: Выровнять уклон по эталону")
    t.Start()
    opts = t.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(SlopeFailurePreprocessor())
    opts.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(opts)

    pairs = record_internal_pairs(all_ids)
    disconnect_pairs(pairs)

    flattened = 0
    skipped = []
    for pid in pipe_ids:
        pipe = doc.GetElement(pid)
        if pipe is None:
            continue
        ok, err = flatten_pipe(pipe, target_z)
        if ok:
            flattened += 1
        else:
            skipped.append((pid.IntegerValue, err))
            flog(u"skip pipe {0}: {1}".format(pid.IntegerValue, err))

    moved_fit = 0
    for fid in safe_fittings:
        if move_fitting_to_z(fid, target_z):
            moved_fit += 1

    rec_ok, rec_fail = reconnect_pairs(pairs)

    try:
        doc.Regenerate()
    except:
        pass

    t.Commit()

    flog(u"DONE flattened={0} moved_fit={1} reconnect ok={2} fail={3} skipped={4}".format(
        flattened, moved_fit, rec_ok, rec_fail, len(skipped)))

    # 5. Отчёт
    lines = []
    lines.append(u"Выровнено труб: {0}".format(flattened))
    lines.append(u"Подстроено фитингов: {0}".format(moved_fit))
    lines.append(u"Восстановлено соединений: {0} (не удалось: {1})".format(
        rec_ok, rec_fail))
    if external_fittings:
        lines.append(u"")
        lines.append(u"⚠ Фитингов со связью наружу (не тронуты, проверьте "
                     u"вручную): {0}".format(len(external_fittings)))
    if skipped:
        lines.append(u"")
        lines.append(u"Пропущено труб: {0}".format(len(skipped)))
        for eid, err in skipped[:10]:
            lines.append(u"  • id {0}: {1}".format(eid, err))
    if rec_fail:
        lines.append(u"")
        lines.append(u"Подробности несостыковок — в _logs/flatten_slope.log")

    pp_wpf.show_report(u"\n".join(lines), title=u"Готово", subtitle=TITLE)


main()
