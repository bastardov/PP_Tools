# -*- coding: utf-8 -*-
"""Фасад проверок инструмента «Проверка теплопотерь» (панель «Теплопотери»).

Весь код переехал в пакет lib/heatloss_checks/ — по файлу на проверку,
см. lib/heatloss_checks/CHECKS.md. Этот модуль оставлен, чтобы script.py
импортировал те же имена, что и раньше:

    from pp_heatloss_checks import (
        CheckCancelled, create_run_context, get_check_definitions,
        get_check_option_definitions, get_default_config, run_report_checks,
    )

Сюда новые проверки НЕ дописывать. Поиск пространств остался отдельным
модулем lib/pp_heatloss_spaces.py.
"""

from heatloss_checks.core import (
    CheckCancelled,
    CheckDefinition,
    CheckIssue,
    CheckOptionDefinition,
    CheckResult,
    RunContext,
    create_run_context,
)

from heatloss_checks.common import (
    normalize_value,
)

from heatloss_checks.registry import (
    CHECK_MODULES,
    DEFAULT_HEATLOSS_CONFIG,
    LOAD_ERRORS,
    get_check_definitions,
    get_check_option_definitions,
    get_default_config,
    run_report_checks,
)
