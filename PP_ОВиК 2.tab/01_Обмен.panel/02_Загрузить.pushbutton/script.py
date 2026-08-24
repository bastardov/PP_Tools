# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass
# ИИ Агент → Загрузить.
#
# Применяет к модели команды ИИ-агента: записывает параметры в элементы
# по их id. Читает <корень плагина>\AI_обмен\commands.json; если файла нет —
# предлагает выбрать вручную.
#
# Формат файла:
# {
#   "commands": [
#     {"id": 123456, "params": {
#         "PP_Номер имя помещения": "101 Кабинет",
#         "ADSK_Температура в помещении": "20 °C",
#         "PP_Ориентация по стороне света": "С",
#         "PP_Добавка на сторону света": "1.1"
#     }},
#     ...
#   ]
# }
#
# Значения задаются как в интерфейсе Revit (display). Для числовых параметров
# сначала пробуется SetValueString (с учётом единиц проекта), при неудаче —
# запись как внутреннего значения. Пишутся только параметры экземпляра,
# read-only пропускаются.

import clr
import os
import json
import codecs

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit.DB import (
    ElementId,
    StorageType,
    Transaction,
)

from pyrevit import forms, script

from pp_settings import get_extension_root


doc = __revit__.ActiveUIDocument.Document


# ─── ЗАПИСЬ ОДНОГО ПАРАМЕТРА ─────────────────────────────────

def set_param(el, name, value):
    """Возвращает ('OK'|причина). Пишет только параметр экземпляра."""
    try:
        p = el.LookupParameter(name)
    except:
        p = None

    if p is None:
        return u"нет параметра"
    if p.IsReadOnly:
        return u"read-only"

    try:
        st = p.StorageType

        if st == StorageType.String:
            p.Set(u"" if value is None else unicode(value))
            return u"OK"

        if st == StorageType.Integer:
            p.Set(int(round(float(value))))
            return u"OK"

        if st == StorageType.ElementId:
            p.Set(ElementId(int(value)))
            return u"OK"

        if st == StorageType.Double:
            # Сначала как отображаемое значение (учитывает единицы проекта),
            # затем как внутреннее значение Revit.
            ok = False
            try:
                ok = p.SetValueString(unicode(value))
            except:
                ok = False
            if not ok:
                p.Set(float(value))
            return u"OK"
    except Exception as ex:
        return u"ERR:{}".format(unicode(ex))

    return u"тип не поддержан"


# ─── ГЛАВНЫЙ ЗАПУСК ──────────────────────────────────────────

try:
    data_dir = os.path.join(get_extension_root(), u"AI_обмен")
    cmd_path = os.path.join(data_dir, u"commands.json")

    if not os.path.exists(cmd_path):
        picked = forms.pick_file(file_ext="json",
                                 title=u"Выберите файл команд ИИ-агента")
        if not picked:
            script.exit()
        cmd_path = picked

    with codecs.open(cmd_path, "r", "utf-8") as fh:
        payload = json.load(fh)

    if isinstance(payload, list):
        commands = payload
    else:
        commands = payload.get("commands", [])

    if not commands:
        forms.alert(u"В файле нет команд (commands).", title=u"Загрузить")
        script.exit()

    log            = []
    applied_params = 0
    applied_elems  = 0
    skipped_elems  = 0
    warn_params    = 0

    t = Transaction(doc, u"ИИ Агент: Загрузить данные")
    t.Start()

    for cmd in commands:
        try:
            eid = int(cmd.get("id"))
        except:
            log.append(u"Пропуск: нет корректного id — {}".format(cmd))
            skipped_elems += 1
            continue

        el = doc.GetElement(ElementId(eid))
        if el is None:
            log.append(u"Элемент {}: не найден в модели".format(eid))
            skipped_elems += 1
            continue

        params = cmd.get("params", {}) or {}
        if not params:
            log.append(u"Элемент {}: пустой список параметров".format(eid))
            skipped_elems += 1
            continue

        parts    = []
        el_wrote = False
        for pname, pval in params.items():
            res = set_param(el, pname, pval)
            parts.append(u"{}={}".format(pname, res))
            if res == u"OK":
                applied_params += 1
                el_wrote = True
            else:
                warn_params += 1

        if el_wrote:
            applied_elems += 1
        else:
            skipped_elems += 1

        log.append(u"Элемент {}: {}".format(eid, u", ".join(parts)))

    t.Commit()

    message = (
        u"Готово.\n\n"
        u"Команд в файле:     {}\n"
        u"Элементов записано: {}\n"
        u"Элементов пропущено: {}\n"
        u"Параметров записано: {}\n"
        u"Параметров с проблемой: {}\n\n"
        u"Файл:\n{}"
    ).format(len(commands), applied_elems, skipped_elems,
             applied_params, warn_params, cmd_path)

    if warn_params > 0 or skipped_elems > 0:
        message += u"\n\nПодробности — в окне pyRevit."
        try:
            output = script.get_output()
            output.print_md(u"### Загрузить — отчёт")
            for line in log:
                print(line)
        except:
            pass

    forms.alert(message, title=u"Загрузить")

except Exception as ex:
    try:
        t.RollBack()
    except:
        pass
    forms.alert(u"Ошибка загрузки:\n\n{}".format(unicode(ex)), title=u"Загрузить")
