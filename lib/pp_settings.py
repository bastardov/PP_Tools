# -*- coding: utf-8 -*-

import os
import io
import json
import copy
import shutil
import datetime


SETTINGS_SCHEMA_VERSION = 1
SCHEMA_KEY = "_schema_version"

try:
    unicode
except NameError:
    unicode = str

try:
    unicode
except NameError:
    unicode = str


DEFAULT_SETTINGS = {
    SCHEMA_KEY: SETTINGS_SCHEMA_VERSION,
    "show_success_report": False,
    "show_warning_report": True,
    "pipe_pair_distance_mm": 600.0,
    "detect_orientation": True,
    "heatloss_show_report": True,
    "mep_transfer_show_pyrevit_report": True,
    "sheet_name_prefixes": [
        u"Изометрическая схема систем",
        u"Схема систем",
        u"Системы"
    ],
    # Параметр штампа, по которому окно выбора листов делит их на разделы
    # (см. lib/pp_sheet_picker.py). Нет такого параметра в проекте —
    # строка выбора раздела в окне просто прячется.
    "sheet_section_param": u"ADSK_Штамп Раздел проекта",
    "sheet_plan_location_map": [
        u"Подвал = План подвала",
        u"Кровля = План кровли",
        u"Технический этаж = План технического этажа",
        u"Чердак = План чердака",
        u"Антресоль = План антресоли"
    ],
    "sheet_plan_section_map": [
        u"Вентиляция = Вентиляция",
        u"Отопление = Отопление",
        u"ОВ = Отопление и вентиляция",
        u"ПД = Противодымная вентиляция",
        u"ВК = Водоснабжение и канализация"
    ],
    "proxy_rule_codes": [
        u"РУЧНАЯ_ПОЗИЦИЯ",
        u"КРАСКА_ГВ031",
        u"КРАСКА_ПФ115",
        u"МЕТАЛЛ_КРЕПЛЕНИЙ_ТРУБ",
        u"МЕТАЛЛ_КРЕПЛЕНИЙ_ВОЗДУХОВОДОВ"
    ],
    "proxy_position_types": [
        u"Ручная позиция",
        u"Краска",
        u"Металл",
        u"Крепеж",
        u"Расходник"
    ],
    "spec_check_settings": {},
    "model_check_settings": {},
    "mep_param_transfer_settings": {},
    # Таблица правил для инструмента «Инструменты меток».
    # Каждое правило — словарь:
    #   cat        — имя BuiltInCategory элемента (напр. "OST_DuctTerminal")
    #   family     — имя семейства элемента (обязательно)
    #   type       — имя типа элемента (необязательно, None = правило на всё семейство)
    #   tag_family — имя семейства марки (аннотации)
    #   tag_type   — имя типа марки
    #   leader     — ставить марку с выноской (True/False)
    "tag_rules": [],
    # Подбор типа марки по длине надписи — режим «По размеру» того же окна.
    # Пресет: {"name": u"Планы 1:100", "rules": [строка, ...]}
    # Строка правила:
    #   cat        — имя BuiltInCategory элемента (воздуховоды, трубы, изоляция)
    #   min_len    — длина надписи от, знаков
    #   max_len    — длина надписи до, знаков (None = «и больше»)
    #   tag_family — имя семейства марки
    #   tag_type   — имя типа марки
    "tag_size_presets": [],
    # Имя пресета, по которому работает «Применить по шаблону».
    "tag_size_active": u"",
    # «Применить по шаблону»: категории, отмеченные в прошлый раз
    # (имена BuiltInCategory). Пустой список = все категории.
    "tag_apply_categories": [],
    # «Длина коридора»: откуда брали помещения в прошлый раз.
    # host — пространства и помещения текущего файла, link — из RVT-связи.
    "corridor_length_sources": {"host": False, "link": True}
}


def get_extension_root():
    current_dir = os.path.dirname(__file__)
    return os.path.dirname(current_dir)


def get_settings_path():
    return os.path.join(get_extension_root(), "lib", "pp_settings.json")


def get_settings_backup_path(path=None):
    if path is None:
        path = get_settings_path()
    return path + ".bak"


def _default_copy():
    return copy.deepcopy(DEFAULT_SETTINGS)


def _read_json(path):
    with io.open(path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(u"Файл настроек должен содержать JSON-словарь.")
    return data


def _write_json(path, data):
    with io.open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=4)
        handle.write(u"\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except Exception:
            pass


try:
    _INTEGER_TYPES = (int, long)
except NameError:
    _INTEGER_TYPES = (int,)

try:
    _STRING_TYPES = (basestring,)
except NameError:
    _STRING_TYPES = (str,)


def _value_matches_default(value, default):
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, _INTEGER_TYPES + (float,)) \
            and not isinstance(value, bool)
    if isinstance(default, _INTEGER_TYPES):
        return isinstance(value, _INTEGER_TYPES) and not isinstance(value, bool)
    if isinstance(default, _STRING_TYPES):
        return isinstance(value, _STRING_TYPES)
    if isinstance(default, list):
        return isinstance(value, list)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return isinstance(value, type(default))


def normalize_settings(data, strict=False):
    u"""Дополняет настройки дефолтами и проверяет типы известных ключей."""
    if not isinstance(data, dict):
        raise ValueError(u"Файл настроек должен содержать JSON-словарь.")

    result = _default_copy()
    invalid_keys = []

    for key, value in data.items():
        if key == SCHEMA_KEY:
            continue
        if key in DEFAULT_SETTINGS \
                and not _value_matches_default(value, DEFAULT_SETTINGS[key]):
            invalid_keys.append(unicode(key))
            continue
        result[key] = value

    if strict and invalid_keys:
        raise ValueError(
            u"Неверный тип значений настроек: {}".format(
                u", ".join(sorted(invalid_keys))
            )
        )

    result[SCHEMA_KEY] = SETTINGS_SCHEMA_VERSION
    return result, invalid_keys


def _settings_log(message):
    u"""Диагностика восстановления; никогда не мешает запуску инструмента."""
    try:
        log_dir = os.path.join(get_extension_root(), "_logs")
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        log_path = os.path.join(log_dir, "settings.log")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with io.open(log_path, "a", encoding="utf-8") as handle:
            handle.write(u"[{}] {}\n".format(stamp, unicode(message)))
    except Exception:
        pass


def _invalid_archive_path(path):
    base, ext = os.path.splitext(path)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = base + ".invalid_" + stamp + ext
    index = 1
    while os.path.exists(candidate):
        candidate = base + ".invalid_" + stamp + "_" + str(index) + ext
        index += 1
    return candidate


def _archive_invalid(path):
    u"""Убирает повреждённый JSON в отдельный файл. Возвращает его путь."""
    if not os.path.exists(path):
        return None
    archive = _invalid_archive_path(path)
    try:
        os.rename(path, archive)
        return archive
    except Exception:
        try:
            shutil.copy2(path, archive)
            os.remove(path)
            return archive
        except Exception as ex:
            _settings_log(
                u"Не удалось сохранить повреждённый файл {}: {}".format(
                    path, unicode(ex)
                )
            )
            return None


def _load_candidate(path):
    data = _read_json(path)
    return normalize_settings(data, strict=False)


def load_settings():
    path = get_settings_path()
    backup_path = get_settings_backup_path(path)

    if not os.path.exists(path):
        if os.path.exists(backup_path):
            try:
                recovered, invalid_keys = _load_candidate(backup_path)
                save_settings(recovered)
                _settings_log(u"Основной файл восстановлен из резервной копии.")
                return recovered
            except Exception as ex:
                _settings_log(
                    u"Резервная копия настроек повреждена: {}".format(unicode(ex))
                )
        defaults = _default_copy()
        save_settings(defaults)
        return defaults

    try:
        result, invalid_keys = _load_candidate(path)
        if invalid_keys:
            _settings_log(
                u"Ключи с неверным типом заменены значениями по умолчанию: {}".format(
                    u", ".join(sorted(invalid_keys))
                )
            )
        return result

    except Exception as ex:
        archive = _archive_invalid(path)
        if archive:
            _settings_log(
                u"Повреждённый файл настроек сохранён как {}: {}".format(
                    archive, unicode(ex)
                )
            )

        if os.path.exists(backup_path):
            try:
                recovered, invalid_keys = _load_candidate(backup_path)
                if archive:
                    save_settings(recovered)
                _settings_log(u"Настройки восстановлены из резервной копии.")
                return recovered
            except Exception as backup_ex:
                _settings_log(
                    u"Не удалось прочитать резервную копию: {}".format(
                        unicode(backup_ex)
                    )
                )

        defaults = _default_copy()
        if archive:
            save_settings(defaults)
        return defaults


def _replace_settings_file(temp_path, path, backup_path):
    u"""Атомарная замена через .NET; ниже — восстанавливаемый fallback."""
    if not os.path.exists(path):
        os.rename(temp_path, path)
        return

    try:
        from System.IO import File
        if File.Exists(backup_path):
            File.Delete(backup_path)
        File.Replace(temp_path, path, backup_path, True)
        return
    except Exception:
        pass

    # Fallback нужен для автономных тестов вне IronPython/.NET. Между двумя
    # rename основной файл может на миг отсутствовать, но .bak уже существует,
    # поэтому следующий load_settings() восстановит его автоматически.
    if os.path.exists(backup_path):
        os.remove(backup_path)
    os.rename(path, backup_path)
    try:
        os.rename(temp_path, path)
    except Exception:
        if not os.path.exists(path) and os.path.exists(backup_path):
            shutil.copy2(backup_path, path)
        raise


def save_settings(settings):
    path = get_settings_path()
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)

    normalized, invalid_keys = normalize_settings(settings, strict=True)

    # save_settings() может быть вызван напрямую, без предварительного
    # load_settings(). Не позволяем такой записи превратить повреждённый JSON
    # в единственную резервную копию вместо последней рабочей версии.
    if os.path.exists(path):
        try:
            _read_json(path)
        except Exception as ex:
            archive = _archive_invalid(path)
            if not archive:
                raise IOError(u"Не удалось сохранить повреждённый файл настроек.")
            _settings_log(
                u"Перед записью повреждённый файл сохранён как {}: {}".format(
                    archive, unicode(ex)
                )
            )

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    temp_path = path + ".tmp_" + str(os.getpid()) + "_" + stamp
    backup_path = get_settings_backup_path(path)

    try:
        _write_json(temp_path, normalized)
        # Проверяем, что на диск действительно записан валидный словарь.
        _read_json(temp_path)
        _replace_settings_file(temp_path, path, backup_path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def export_settings(dest_path):
    u"""Сохраняет текущие (записанные на диск) настройки в файл .json."""
    settings = load_settings()
    _write_json(dest_path, settings)


def import_settings(src_path):
    u"""Читает настройки из файла, объединяет с дефолтами и записывает в
    основной конфиг. Возвращает применённые настройки. Бросает исключение,
    если файл не является корректным JSON-словарём."""
    data = _read_json(src_path)
    result, invalid_keys = normalize_settings(data, strict=True)
    save_settings(result)
    return result


def show_report(forms, title, success_message, warning_message=None, has_warnings=False):
    u"""Итоговый отчёт инструмента с учётом настроек показа.

    Первый аргумент оставлен для совместимости: раньше сюда передавали модуль
    pyrevit.forms, теперь окно рисует pp_wpf. Ничего менять в вызовах не нужно,
    можно передавать None.
    """
    settings = load_settings()

    show_success_report = settings.get("show_success_report", False)
    show_warning_report = settings.get("show_warning_report", True)

    if has_warnings and show_warning_report:
        text = warning_message if warning_message else success_message
        heading = u"Выполнено с замечаниями"
        is_error = True

    elif show_success_report and not has_warnings:
        text = success_message
        heading = u"Готово"
        is_error = False

    else:
        return

    # Импорт внутри функции: pp_settings читают и те скрипты, которым окна
    # не нужны вовсе, и тянуть ради них сборки WPF незачем.
    import pp_wpf

    pp_wpf.show_report(text, title=heading, subtitle=title, is_error=is_error)
