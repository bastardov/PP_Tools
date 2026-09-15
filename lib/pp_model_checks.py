# -*- coding: utf-8 -*-
"""Фасад проверок инструмента «Проверка Модели» (панель «Проверка»).

Весь код переехал в пакет lib/model_checks/ — по файлу на проверку,
см. lib/model_checks/CHECKS.md. Этот модуль оставлен, чтобы script.py и
остальные места импортировали те же имена, что и раньше:

    from pp_model_checks import (
        create_element_cache, get_check_definitions,
        get_check_option_definitions, get_default_config, run_report_checks,
    )

Сюда новые проверки НЕ дописывать.
"""

from model_checks.core import (
    FEET_TO_MM,
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
    ElementCache,
    create_element_cache,
)

from model_checks.registry import (
    CHECK_MODULES,
    DEFAULT_MODEL_CONFIG,
    LOAD_ERRORS,
    get_check_definitions,
    get_check_option_definitions,
    get_default_config,
    run_report_checks,
)
