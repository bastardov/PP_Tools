# -*- coding: utf-8 -*-
"""Реестр «Проверки спецификации»: собирает проверки из файлов check_*.py.

Список модулей — явный: порядок в CHECK_MODULES = порядок проверок и их
опций в окне. Каждый модуль грузится в своём try/except: если один файл
сломан, остальные проверки продолжают работать, а причина пишется в
LOAD_ERRORS и в окно вывода pyRevit.

Каждый модуль check_*.py отдаёт:
  DEFAULTS         — значения по умолчанию для опций этой проверки;
  get_options()    — список CheckOptionDefinition (может быть пустым);
  get_definition() — CheckDefinition с runner(doc, config, cache).
"""

import sys
import traceback

from spec_checks.core import create_element_cache


# Новую проверку дописать сюда (и в таблицу в CHECKS.md).
CHECK_MODULES = [
    "check_name_presence",          # 1. Наименования у труб и воздуховодов
    "check_quantity_presence",      # 2. Пустое или нулевое ADSK_количество
    "check_duct_bzero",             # 3. Запрещенные тексты в наименовании
    "check_duct_metal_thickness",   # 4. Пустая или нулевая толщина металла
    "check_copper_pipe_sizes",      # 5. Нестандартные размеры медной трубы
    "check_system_grouping",        # 6. Имя системы и ADSK_Группирование
    "check_sort_order",             # 7. Заполнение PP_Порядок сортировки
    "check_mep_name_match",         # 8. Совпадение имени по MEP-соединениям
    "check_size_in_name",           # 9. Размер в ADSK_Наименование на ВЕНТИЛЯЦИИ
    "check_pipe_size_in_name",      # 10. Размер в ADSK_Наименование на ОТОПЛЕНИЕ
    "check_proxy_controller",       # 11. Прокси-позиции по контроллеру
    "check_forbidden_text",         # 12. Запрещенные фразы в наименовании и марке
    "check_param_rules",            # 13. ADSK_Система_Сокращение
    "check_nested_heat",            # 14. Вложенные семейства (отопление)
    "check_nested_vent",            # 15. Вложенные семейства (вентиляция)
]

# [(имя модуля, текст ошибки)] — что не загрузилось.
LOAD_ERRORS = []


def _load_modules():
    modules = []
    for name in CHECK_MODULES:
        full_name = "spec_checks." + name
        try:
            __import__(full_name)
            modules.append(sys.modules[full_name])
        except:
            details = traceback.format_exc()
            LOAD_ERRORS.append((name, details))
            try:
                print(u"Проверка спецификации: не загрузился модуль {0}.py — "
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


# Общий словарь значений по умолчанию (прежний DEFAULT_SPEC_CONFIG целиком).
DEFAULT_SPEC_CONFIG = _build_default_config()


# --------------------------------------------------------------------------- #
#                             определения проверок                            #
# --------------------------------------------------------------------------- #

def get_default_config():
    return dict(DEFAULT_SPEC_CONFIG)


def get_check_option_definitions():
    result = []
    for module in CHECK_MODULE_OBJECTS:
        get_options = getattr(module, "get_options", None)
        if get_options is not None:
            result.extend(get_options())
    return result


def get_check_definitions():
    return [module.get_definition() for module in CHECK_MODULE_OBJECTS]


def run_checks(doc, selected_keys, config, cache=None):
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
        if definition is None:
            continue
        results.append(definition.runner(doc, merged, cache))

    return results
