# -*- coding: utf-8 -*-
"""Реестр «Проверки теплопотерь»: собирает проверки из файлов check_*.py.

Список модулей — явный: порядок в CHECK_MODULES = порядок проверок и их
опций в окне. Каждый модуль грузится в своём try/except: если один файл
сломан, остальные проверки продолжают работать, а причина пишется в
LOAD_ERRORS и в окно вывода pyRevit.

Каждый модуль check_*.py отдаёт:
  DEFAULTS         — значения по умолчанию для опций этой проверки;
  get_options()    — список CheckOptionDefinition (может быть пустым);
  get_definition() — CheckDefinition с runner(doc, config, context),
                     runner может вернуть CheckResult или список CheckResult.
"""

import sys
import traceback

from heatloss_checks.core import create_run_context


# Новую проверку дописать сюда (и в таблицу в CHECKS.md).
CHECK_MODULES = [
    "check_numname",    # 1. Имя и номер помещения ≠ пространство рядом
]

# [(имя модуля, текст ошибки)] — что не загрузилось.
LOAD_ERRORS = []


def _load_modules():
    modules = []
    for name in CHECK_MODULES:
        full_name = "heatloss_checks." + name
        try:
            __import__(full_name)
            modules.append(sys.modules[full_name])
        except:
            details = traceback.format_exc()
            LOAD_ERRORS.append((name, details))
            try:
                print(u"Проверка теплопотерь: не загрузился модуль {0}.py — "
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


# Общий словарь значений по умолчанию (прежний DEFAULT_HEATLOSS_CONFIG целиком).
DEFAULT_HEATLOSS_CONFIG = _build_default_config()


# --------------------------------------------------------------------------- #
#                             определения проверок                            #
# --------------------------------------------------------------------------- #

def get_default_config():
    return dict(DEFAULT_HEATLOSS_CONFIG)


def get_check_option_definitions():
    result = []
    for module in CHECK_MODULE_OBJECTS:
        get_options = getattr(module, "get_options", None)
        if get_options is not None:
            result.extend(get_options())
    return result


def get_check_definitions():
    return [module.get_definition() for module in CHECK_MODULE_OBJECTS]


def run_report_checks(doc, selected_keys, config, context=None):
    """Запускает отмеченные проверки. Runner может вернуть список результатов."""
    selected_keys = selected_keys or []
    merged = get_default_config()
    if config:
        merged.update(config)

    if context is None:
        context = create_run_context(doc)

    key_map = {}
    for definition in get_check_definitions():
        key_map[definition.key] = definition

    results = []
    for key in selected_keys:
        definition = key_map.get(key)
        if definition is None or definition.runner is None:
            continue

        produced = definition.runner(doc, merged, context)
        if produced is None:
            continue
        if isinstance(produced, (list, tuple)):
            results.extend([item for item in produced if item is not None])
        else:
            results.append(produced)

    return results
