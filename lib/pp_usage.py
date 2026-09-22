# -*- coding: utf-8 -*-
u"""Своя телеметрия PP Tools — учёт запусков инструментов плагина.

Зачем своя, а не встроенная в pyRevit:
    Встроенная телеметрия pyRevit оказалась ненадёжной — создавала файл на
    каждую сессию, но записи практически не писала, а путь к папке сохраняла
    в экранированном виде и молча отключалась. Здесь всё под нашим контролем.

Как работает:
    В начало каждого script.py кнопки добавлена защищённая строка:

        try: import pp_usage; pp_usage.log(__file__)
        except Exception: pass

    Универсального хука «на любую кнопку» в pyRevit нет, поэтому вызов
    явный. Строка обёрнута в try/except, а сам log() ничего не бросает —
    любая проблема логирования не должна ломать работу инструмента.

Формат хранения:
    Append-only JSONL (одна JSON-запись на строку) в файлах
    telemetry/pp_usage_YYYY-MM.jsonl внутри папки расширения.
    JSONL выбран намеренно: дописывание одной строки атомарно, файл не
    портится при одновременной работе нескольких сессий Revit, а битая
    строка не убивает весь файл (в отличие от единого JSON-массива).

Состав записи:
    ts, date, time  — локальное время запуска
    tool            — имя инструмента (без служебного префикса «06_»)
    bundle          — имя папки кнопки как есть
    panel, tab      — панель и вкладка
    user            — имя пользователя из настроек Revit
    winuser         — пользователь Windows
    revit           — версия Revit
    doc             — заголовок открытого документа как есть (Document.Title)
    model           — «чистое» имя модели: без расширения и без суффикса
                      пользователя, который Revit добавляет локальной копии
                      («12345_ОВ_ivanov» и «12345_ОВ_petrov» → «12345_ОВ»)
    model_key       — устойчивый ключ модели: путь центральной модели, для
                      несовместных — путь файла. Заголовок для этого не годится
                      (у каждого пользователя свой), а путь общий для всех
    doc_guid        — Document.CreationGUID, запасной ключ на случай
                      переименования или переноса файла
    session         — идентификатор запуска Revit (pid + время старта
                      процесса), одинаков у всех кнопок одного сеанса работы

    Зачем model_key/session: по ним сырые логи фильтруются по конкретному
    объекту и раскладываются по временной шкале модели — видно, какие
    инструменты идут в начале моделирования, а какие ближе к оформлению.
"""

import os
import io
import json
import glob
import time as _time
import datetime


LOG_DIR_NAME = "telemetry"
FILE_PREFIX = "pp_usage_"
FILE_EXT = ".jsonl"

# Суффиксы папок pyRevit, которые нужно распознавать при разборе пути
BUNDLE_SUFFIXES = (".pushbutton", ".smartbutton", ".togglebutton",
                   ".urlbutton", ".linkbutton", ".invokebutton")


# ---------------------------------------------------------------------------
# ПУТИ
# ---------------------------------------------------------------------------

def get_extension_root():
    u"""Корень расширения (папка *.extension). Модуль лежит в <root>/lib."""
    lib_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(lib_dir)


def get_log_dir(create=True):
    u"""Папка с логами использования внутри расширения."""
    folder = os.path.join(get_extension_root(), LOG_DIR_NAME)
    if create and not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except Exception:
            pass
    return folder


def get_log_path(moment=None):
    u"""Файл лога за месяц указанного момента (по умолчанию — сейчас)."""
    if moment is None:
        moment = datetime.datetime.now()
    name = FILE_PREFIX + moment.strftime("%Y-%m") + FILE_EXT
    return os.path.join(get_log_dir(), name)


# ---------------------------------------------------------------------------
# РАЗБОР ПУТИ КНОПКИ
# ---------------------------------------------------------------------------

def strip_index(name):
    u"""Убирает служебный числовой префикс: '06_Копировать марки' → 'Копировать марки'."""
    if not name:
        return name
    if "_" in name:
        head = name.split("_", 1)[0]
        if head.isdigit():
            return name.split("_", 1)[1]
    return name


def _strip_suffix(name, suffixes):
    low = name.lower()
    for suffix in suffixes:
        if low.endswith(suffix):
            return name[:-len(suffix)]
    return name


def parse_script_path(script_path):
    u"""Из пути script.py достаёт (bundle, panel, tab). Любое поле может быть None."""
    result = {"bundle": None, "panel": None, "tab": None}
    if not script_path:
        return result

    path = os.path.abspath(script_path)
    # Идём вверх по дереву: кнопка → (стек) → панель → вкладка
    while path and os.path.basename(path):
        base = os.path.basename(path)
        low = base.lower()
        if result["bundle"] is None and low.endswith(BUNDLE_SUFFIXES):
            result["bundle"] = _strip_suffix(base, BUNDLE_SUFFIXES)
        elif result["panel"] is None and low.endswith(".panel"):
            result["panel"] = _strip_suffix(base, (".panel",))
        elif result["tab"] is None and low.endswith(".tab"):
            result["tab"] = _strip_suffix(base, (".tab",))
            break
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return result


# ---------------------------------------------------------------------------
# КОНТЕКСТ REVIT
# ---------------------------------------------------------------------------

def _get_central_path(doc):
    u"""Путь центральной модели (только для совместной работы), иначе None."""
    try:
        if not doc.IsWorkshared:
            return None
    except Exception:
        return None
    try:
        from Autodesk.Revit.DB import ModelPathUtils
        model_path = doc.GetWorksharingCentralModelPath()
        if model_path is None:
            return None
        visible = ModelPathUtils.ConvertModelPathToUserVisiblePath(model_path)
        return visible or None
    except Exception:
        return None


def _strip_extension(name):
    u"""Убирает расширение файла модели, если оно есть."""
    if not name:
        return name
    low = name.lower()
    for ext in (".rvt", ".rte", ".rfa"):
        if low.endswith(ext):
            return name[:-len(ext)]
    return name


def strip_user_suffix(title, names):
    u"""Убирает хвост «_<пользователь>» у заголовка локальной копии.

    Revit называет локальную копию центральной модели «Имя_Пользователь», из-за
    чего одна и та же модель у разных людей выглядит как разные. Для группировки
    этот хвост нужно снять.
    """
    if not title:
        return title
    for name in names:
        if not name:
            continue
        suffix = "_" + name
        if len(title) > len(suffix) and title.lower().endswith(suffix.lower()):
            return title[:-len(suffix)]
    return title


def get_model_info(doc, user_names):
    u"""Имя модели, устойчивый ключ и GUID документа. Любое поле может быть None."""
    info = {"doc": None, "model": None, "model_key": None, "doc_guid": None}
    if doc is None:
        return info

    try:
        info["doc"] = doc.Title
    except Exception:
        pass

    central = _get_central_path(doc)
    file_path = None
    try:
        file_path = doc.PathName or None
    except Exception:
        pass

    # Ключ: путь центральной модели → путь файла → заголовок.
    # Первый вариант общий для всех участников, последний — на крайний случай.
    info["model_key"] = central or file_path or info["doc"]

    # Имя: из имени файла центральной модели (там нет суффикса пользователя),
    # иначе — из заголовка со снятым суффиксом.
    name = None
    if central:
        try:
            name = _strip_extension(os.path.basename(central))
        except Exception:
            name = None
    if not name:
        name = strip_user_suffix(_strip_extension(info["doc"]), user_names)
    info["model"] = name or None

    try:
        guid = doc.CreationGUID
        if guid is not None:
            info["doc_guid"] = str(guid)
    except Exception:
        pass
    return info


def get_session_id():
    u"""Идентификатор запуска Revit — общий для всех кнопок одного сеанса.

    Берём pid и время старта процесса: модуль может быть перезагружен движком
    pyRevit между командами, поэтому хранить счётчик в памяти нельзя.
    """
    try:
        from System.Diagnostics import Process
        process = Process.GetCurrentProcess()
        return "%s-%s" % (process.Id,
                          process.StartTime.ToString("yyyyMMddHHmmss"))
    except Exception:
        pass
    try:
        return str(os.getpid())
    except Exception:
        return None


def get_revit_context():
    u"""Пользователь, версия Revit и сведения о модели. Всё необязательно."""
    context = {"user": None, "revit": None, "doc": None,
               "model": None, "model_key": None, "doc_guid": None}
    try:
        from pyrevit import HOST_APP
    except Exception:
        return context

    try:
        context["user"] = HOST_APP.username
    except Exception:
        pass
    try:
        context["revit"] = str(HOST_APP.version)
    except Exception:
        pass
    try:
        doc = HOST_APP.doc
    except Exception:
        doc = None
    try:
        info = get_model_info(doc, (context.get("user"), _get_windows_user()))
        context.update(info)
    except Exception:
        pass
    return context


def _get_windows_user():
    for key in ("USERNAME", "USER"):
        try:
            value = os.getenv(key)
            if value:
                return value
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# ЗАПИСЬ
# ---------------------------------------------------------------------------

def build_record(script_path=None, moment=None):
    u"""Собирает запись о запуске инструмента."""
    if moment is None:
        moment = datetime.datetime.now()

    parts = parse_script_path(script_path)
    context = get_revit_context()

    bundle = parts.get("bundle")
    record = {
        "ts": moment.strftime("%Y-%m-%dT%H:%M:%S"),
        "date": moment.strftime("%Y-%m-%d"),
        "time": moment.strftime("%H:%M:%S"),
        "tool": strip_index(bundle) if bundle else None,
        "bundle": bundle,
        "panel": strip_index(parts.get("panel")),
        "tab": parts.get("tab"),
        "user": context.get("user"),
        "winuser": _get_windows_user(),
        "revit": context.get("revit"),
        "doc": context.get("doc"),
        "model": context.get("model"),
        "model_key": context.get("model_key"),
        "doc_guid": context.get("doc_guid"),
        "session": get_session_id(),
    }
    return record


def append_record(record, attempts=3):
    u"""Дописывает запись строкой в месячный файл. Возвращает True при успехе."""
    line = json.dumps(record, ensure_ascii=False)
    if isinstance(line, bytes):
        line = line.decode("utf-8")

    path = get_log_path()
    for number in range(attempts):
        handle = None
        try:
            handle = io.open(path, "a", encoding="utf-8")
            handle.write(line + u"\n")
            return True
        except Exception:
            # файл может быть занят другой сессией Revit — коротко подождать
            if number < attempts - 1:
                try:
                    _time.sleep(0.05)
                except Exception:
                    pass
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
    return False


def log(script_path=None):
    u"""Точка входа для кнопок. Никогда не бросает исключений."""
    try:
        append_record(build_record(script_path))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ЧТЕНИЕ
# ---------------------------------------------------------------------------

def _read_text(path):
    u"""Читает файл целиком байтами и декодирует в unicode.

    Читаем именно в бинарном режиме и декодируем сами, а НЕ построчно через
    текстовый io.open: в IronPython ленивое построчное чтение utf-8 с кириллицей
    срывается посреди файла, и вызывающий цикл терял все строки после сбоя.
    """
    handle = None
    try:
        handle = io.open(path, "rb")
        raw = handle.read()
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
    if raw is None:
        return u""
    # utf-8-sig снимает BOM, если он есть; ошибочные байты не роняют чтение
    return raw.decode("utf-8-sig", "replace")


def read_records(folder=None):
    u"""Читает все записи из pp_usage_*.jsonl. Битые строки пропускаются.

    Каждая строка парсится в отдельном try/except — одна плохая строка или сбой
    на файле НЕ должны отбрасывать уже прочитанные записи.
    """
    if folder is None:
        folder = get_log_dir(create=False)
    records = []
    if not folder or not os.path.isdir(folder):
        return records

    pattern = os.path.join(folder, FILE_PREFIX + "*" + FILE_EXT)
    for path in sorted(glob.glob(pattern)):
        try:
            text = _read_text(path)
        except Exception:
            continue
        text = text.replace(u"\r\n", u"\n").replace(u"\r", u"\n")
        for line in text.split(u"\n"):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                records.append(item)
    return records


def get_model_label(record):
    u"""Имя модели для группировки — работает и со старыми записями.

    В записях до августа 2026 поля «model» нет, есть только «doc» с суффиксом
    пользователя. Здесь суффикс снимается по именам из самой записи, поэтому
    старые и новые записи по одной модели попадают в одну группу.
    """
    if not isinstance(record, dict):
        return None
    model = record.get("model")
    if model:
        return model
    title = record.get("doc")
    if not title:
        return None
    return strip_user_suffix(_strip_extension(title),
                             (record.get("user"), record.get("winuser")))


def get_model_id(record):
    u"""Устойчивый ключ модели: model_key → doc_guid → имя модели."""
    if not isinstance(record, dict):
        return None
    for key in ("model_key", "doc_guid"):
        value = record.get(key)
        if value:
            return value
    return get_model_label(record)
