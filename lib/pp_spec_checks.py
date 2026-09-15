# -*- coding: utf-8 -*-
"""Фасад проверок инструмента «Проверка спецификации» (панель «Проверка»).

Весь код переехал в пакет lib/spec_checks/ — по файлу на проверку,
см. lib/spec_checks/CHECKS.md. Этот модуль оставлен, чтобы script.py
импортировал те же имена, что и раньше:

    from pp_spec_checks import (
        create_element_cache, get_check_definitions,
        get_check_option_definitions, get_default_config, run_checks,
    )

Сюда новые проверки НЕ дописывать.
"""

from spec_checks.core import (
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
    ElementCache,
    create_element_cache,
)

from spec_checks.registry import (
    CHECK_MODULES,
    DEFAULT_SPEC_CONFIG,
    LOAD_ERRORS,
    get_check_definitions,
    get_check_option_definitions,
    get_default_config,
    run_checks,
)
