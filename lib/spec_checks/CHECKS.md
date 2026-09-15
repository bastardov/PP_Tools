# Проверки «Проверки спецификации» — пакет `lib/spec_checks/`

Инструмент «Проверка спецификации» (панель «Проверка»). Раньше весь код лежал одним
файлом `lib/pp_spec_checks.py` (~2300 строк). 15.09.2026 он механически разнесён по
файлам по образцу `lib/model_checks/`. Логика, тексты, ключи настроек и порядок
проверок и опций не менялись.

В коде и переписке проверки этого инструмента исторически называются
**стратегиями**. В окнах для пользователя они по-прежнему «проверки», эти подписи не менять.

## Устройство

| Файл | Что внутри |
|---|---|
| `lib/pp_spec_checks.py` | **Фасад.** Отдаёт те же имена, что и раньше (`create_element_cache`, `get_check_definitions`, `get_check_option_definitions`, `get_default_config`, `run_checks`, плюс `DEFAULT_SPEC_CONFIG`). Из него импортирует `script.py` кнопки. Новый код сюда не писать. |
| `core.py` | Каркас: `CheckIssue`, `CheckResult`, `CheckDefinition`, `CheckOptionDefinition`, `ElementCache`, `create_element_cache`. |
| `common.py` | Хелперы, которыми пользуются **2 и больше** проверок: категории, чтение параметров, имена элементов, парсеры. |
| `common_size.py` | «Размер в ADSK_Наименование»: общее у проверок 9 и 10. |
| `common_nested.py` | «Вложенные семейства»: общее у проверок 14 и 15. |
| `registry.py` | Явный список `CHECK_MODULES`, загрузка каждого модуля в своём `try/except`, сборка общего `DEFAULT_SPEC_CONFIG`, опций и определений, `run_checks`. |
| `check_*.py` | Одна проверка = один файл. |
| `validate_checks.py` | Самопроверка без Revit (см. ниже). |

Зависимости идут только вниз: `check_*.py` → `common_size.py` / `common_nested.py` →
`common.py` → `core.py`; `registry.py` → `check_*.py`. Проверки друг друга не
импортируют. Внешние модули — как и раньше: `pp_mep_filter` (проверки 8 и 13) и `re`.

## Из чего состоит `check_*.py`

```python
DEFAULTS = {...}               # значения по умолчанию опций этой проверки
DEFAULT_SPEC_CONFIG = DEFAULTS # прежнее имя — см. ниже
<свои константы и хелперы>
def _run_..._check(doc, config, cache): ...   # runner -> CheckResult
def get_options():             # [CheckOptionDefinition(...)] — может быть []
def get_definition():          # CheckDefinition(key, title, description, runner, option_keys=[...])
```

**Про `DEFAULT_SPEC_CONFIG` внутри файла проверки.** Проверки читают значения
по умолчанию так: `config.get("ключ", DEFAULT_SPEC_CONFIG["ключ"])`. Этот код
оставлен без изменений (решение 15.09.2026). Поэтому в каждом файле, где он
встречается, `DEFAULT_SPEC_CONFIG` — второе имя для `DEFAULTS` **этого файла**, а не
общий словарь. Следствие: проверка может читать только свои ключи. Общий словарь
целиком собирает `registry.py`, он же отдаётся через фасад. Если в новой проверке
этот приём не нужен, второе имя не заводить.

Настройки хранятся в `pp_settings.json` → `spec_check_settings.config` плоским
словарём по ключам опций (значение опции `mep_filter` — вложенный словарь).
`script.py` берёт `get_default_config()` и накладывает сохранённое, поэтому новые
ключи в `DEFAULTS` подхватываются без миграции.
**Ключи опций и проверок не переименовывать**, иначе у пользователей потеряются
настройки и отметки проверок.

Опция описывается ровно в одном файле, там же её значение по умолчанию. Если
опция когда-нибудь понадобится двум проверкам, её описывает первая, а вторая только
перечисляет ключ в `option_keys` (как `position_param` в `lib/model_checks/`).

## Как добавить проверку

1. Скопировать ближайший по смыслу `check_*.py`, дать новый ключ и новые ключи опций.
2. Хелпер, нужный только этой проверке, держать в её файле. Если он уже есть в чужом
   `check_*.py` и понадобился второй проверке, перенести его в `common.py` (или в
   профильный `common_*.py`), имя не менять.
3. Дописать модуль в `CHECK_MODULES` в `registry.py`: порядок списка = порядок в окне.
   Номер в заголовке («16. …») ставится руками.
4. Дописать строку в таблицу ниже.
5. Запустить `validate_checks.py`.

Типы опций, которые окно уже умеет: `text`, `param`, `multiline`,
`category_multiselect` и `mep_filter` (построитель фильтра, подключён в `script.py`
через `option_builders`). Пока хватает их, `script.py` и `lib/pp_check_windows.py` не трогать.

Кнопка работает на постоянном движке (`__persistentengine__`): после правки файлов
пакета нужна перезагрузка pyRevit (Reload).

## Если проверка пропала из окна

Реестр грузит каждый модуль отдельно. Если в файле ошибка, остальные проверки
работают, а в окне вывода pyRevit печатается «не загрузился модуль …» с трассировкой;
тот же текст лежит в `registry.LOAD_ERRORS`.

## Самопроверка

```
python lib/spec_checks/validate_checks.py
```

Обычный Python 3, Revit не нужен. Проверяет синтаксис, реестр ↔ файлы, наличие
`DEFAULTS`/`get_options`/`get_definition`, ключи (дубли, опция без значения по умолчанию
и наоборот, `option_keys` ↔ `check_keys`), что `DEFAULT_SPEC_CONFIG["ключ"]` в файле
проверки читает свой ключ, импорты между проверками, забытые импорты. В конце
импортирует пакет с заглушками вместо Revit API. Код выхода 0 — ошибок нет.

## Проверки

| № | Ключ | Файл | Опции |
|---|---|---|---|
| 1 | `name_presence` | `check_name_presence.py` | `name_presence_param` |
| 2 | `quantity_presence` | `check_quantity_presence.py` | `quantity_param` |
| 3 | `duct_bzero` | `check_duct_bzero.py` | `bzero_name_param`, `bzero_text`, `bzero_categories` |
| 4 | `duct_metal_thickness` | `check_duct_metal_thickness.py` | `metal_thickness_param` |
| 5 | `copper_pipe_sizes` | `check_copper_pipe_sizes.py` | `copper_keyword_source`, `copper_keywords`, `copper_size_param`, `copper_standard_sizes_mm` |
| 6 | `system_grouping` | `check_system_grouping.py` | `grouping_param` |
| 7 | `sort_order` | `check_sort_order.py` | `sort_order_param`, `sort_order_categories` |
| 8 | `mep_name_match` | `check_mep_name_match.py` | `mep_source_categories`, `mep_source_param`, `mep_receiver_categories`, `mep_receiver_param`, `mep_filter` |
| 9 | `size_in_name` | `check_size_in_name.py` | `size_name_name_param`, `size_name_size_param`, `size_name_categories` |
| 10 | `pipe_size_in_name` | `check_pipe_size_in_name.py` | `pipe_size_name_param`, `pipe_size_categories`, `pipe_gost1_keyword`, `pipe_gost1_size_param`, `pipe_gost2_keyword`, `pipe_gost2_size_param` |
| 11 | `proxy_controller` | `check_proxy_controller.py` | `proxy_ctrl_family`, `proxy_ctrl_proxy_family`, `proxy_ctrl_code_param`, `proxy_ctrl_value_param`, `proxy_ctrl_mapping` |
| 12 | `forbidden_text` | `check_forbidden_text.py` | `badtext_params`, `badtext_phrases`, `badtext_categories` |
| 13 | `param_rules` | `check_param_rules.py` | `param_rules_target`, `param_rules_text` |
| 14 | `nested_heat` | `check_nested_heat.py` | `nested_heat_param`, `nested_heat_categories` |
| 15 | `nested_vent` | `check_nested_vent.py` | `nested_vent_param1`, `nested_vent_param2`, `nested_vent_categories` |

## Что в `common*.py`

**`common.py`:** `MM_PER_FOOT`, `PIPE_AND_DUCT_CATEGORIES`, `SORT_ORDER_CATEGORY_OPTIONS`, `SORT_ORDER_CATEGORY_MAP`, `SORT_ORDER_ALL_CATEGORY_KEYS`, `_get_type_element`, `_get_family_name`, `_get_type_name`, `_get_category_name`, `_get_element_label`, `_get_lookup_parameter`, `_get_parameter_text_from_param`, `_get_parameter_text`, `_get_parameter_text_from_element_or_type`, `_has_nonempty_text`, `_normalize_space`, `_parse_category_keys`, `_parse_float`, `_get_length_param_mm`, `_get_number_param`, `_is_zero_number`, `_make_simple_result`, `_resolve_categories`.

`SORT_ORDER_ALL_CATEGORY_KEYS` сейчас нигде не используется; перенесён как был.

**`common_size.py`:** `_get_size_text`, `_normalize_size`, `_size_end_in_name`, `_size_matches_name`.

**`common_nested.py`:** `_get_super_component`, `_show_value`.

Имена исторические, переименование — отдельная задача, не смешивать с переносом.
