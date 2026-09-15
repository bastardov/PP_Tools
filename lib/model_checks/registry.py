# -*- coding: utf-8 -*-
"""Реестр «Проверки Модели»: собирает проверки из файлов check_*.py.

Список модулей — явный: порядок в CHECK_MODULES = порядок проверок и их
опций в окне. Каждый модуль грузится в своём try/except: если один файл
сломан, остальные проверки продолжают работать, а причина пишется в
LOAD_ERRORS и в окно вывода pyRevit.

Каждый модуль check_*.py отдаёт:
  DEFAULTS         — значения по умолчанию для опций, описанных в этом файле;
  get_options()    — список CheckOptionDefinition (может быть пустым);
  get_definition() — CheckDefinition с runner(doc, config, cache).
"""

import sys
import traceback

from model_checks.core import create_element_cache


# Новую проверку дописать сюда (и в таблицу в CHECKS.md).
CHECK_MODULES = [
    "check_pipe_flow",             # 1. Трубы без расхода
    "check_slope",                 # 2. Уклон у труб и воздуховодов
    "check_orphan",                # 3. Элементы без системы (висящие)
    "check_near_connectors",       # 4. Почти соединённые концы
    "check_short_segment",         # 5. Короткие обрезки
    "check_degenerate",            # 6. Вырожденная геометрия
    "check_duplicate",             # 7. Дубли в одной точке
    "check_pipe_insulation",       # 8. Изоляция труб (наличие и тип)
    "check_position_category",     # 9. ADSK_Позиция не на своей категории
    "check_position_duplicate",    # 10. Дубли ADSK_Позиция у оборудования
    "check_grids_halftone",        # 11. Полутон у Осей в шаблонах
    "check_smoke_distance",        # 12. Расстояние между ДП и ДВ
    "check_smoke_inlet_height",    # 13. Высота дымоприёмного устройства
]

# [(имя модуля, текст ошибки)] — что не загрузилось.
LOAD_ERRORS = []


def _load_modules():
    modules = []
    for name in CHECK_MODULES:
        full_name = "model_checks." + name
        try:
            __import__(full_name)
            modules.append(sys.modules[full_name])
        except:
            details = traceback.format_exc()
            LOAD_ERRORS.append((name, details))
            try:
                print(u"Проверка Модели: не загрузился модуль {0}.py — "
                      u"проверка пропущена.\n{1}".format(name, details))
            except:
                pass
    return modules


CHECK_MODULE_OBJECTS = _load_modules()


def _build_default_config():
    config = {}
    for module in CHECK_MODULE_OBJECTS:
        config.update(getattr(module, "DEFAULTS", None) or {})
    return config


DEFAULT_MODEL_CONFIG = _build_default_config()


# --------------------------------------------------------------------------- #
#                             определения проверок                            #
# --------------------------------------------------------------------------- #

def get_default_config():
    return dict(DEFAULT_MODEL_CONFIG)


def get_check_option_definitions():
    result = []
    for module in CHECK_MODULE_OBJECTS:
        get_options = getattr(module, "get_options", None)
        if get_options is not None:
            result.extend(get_options())
    return result


def get_check_definitions():
    return [module.get_definition() for module in CHECK_MODULE_OBJECTS]


def run_report_checks(doc, selected_keys, config, cache=None):
    """Запускает «читающие» проверки (kind == "report")."""
    selected_keys = selected_keys or []
    merged = get_default_config()
    if config:
        merged.update(config)

    if cache is None:
        cache = create_element_cache(doc)

    key_map = {}
    for definition in get_check_definitions():
        key_map[definition.key] = definition

    results = []
    for key in selected_keys:
        definition = key_map.get(key)
        if definition is None or definition.kind != u"report":
            continue
        if definition.runner is None:
            continue
        results.append(definition.runner(doc, merged, cache))

    return results
