# Проверки «Проверки Модели» — пакет `lib/model_checks/`

Инструмент «Проверка Модели» (панель «Проверка»). Раньше весь код лежал одним
файлом `lib/pp_model_checks.py` (~2300 строк). 15.09.2026 он механически
разнесён по файлам: логика, тексты, ключи настроек и порядок проверок не менялись.

## Устройство

| Файл | Что внутри |
|---|---|
| `lib/pp_model_checks.py` | **Фасад.** Отдаёт те же имена, что и раньше (`create_element_cache`, `get_check_definitions`, `get_check_option_definitions`, `get_default_config`, `run_report_checks`). Из него импортирует `script.py` кнопки. Новый код сюда не писать. |
| `core.py` | Каркас: `CheckIssue`, `CheckResult`, `CheckDefinition`, `CheckOptionDefinition`, `ElementCache`, `create_element_cache`, `FEET_TO_MM`. О конкретных проверках ничего не знает. |
| `common.py` | Хелперы, которыми пользуются **2 и больше** проверок (маски, парсеры, подписи, геометрия). |
| `registry.py` | Явный список `CHECK_MODULES`, загрузка каждого модуля в своём `try/except`, сборка дефолтов/опций/определений, `run_report_checks`. |
| `check_*.py` | Одна проверка = один файл. |
| `validate_checks.py` | Самопроверка без Revit (см. ниже). |

Зависимости идут только в одну сторону: `check_*.py` → `common.py` → `core.py`;
`registry.py` → `check_*.py`. Проверки друг друга не импортируют.

## Из чего состоит `check_*.py`

```python
DEFAULTS = {...}          # значения по умолчанию для опций, ОПИСАННЫХ в этом файле
<свои константы и хелперы>
def _run_..._check(doc, config, cache): ...   # runner -> CheckResult
def get_options():        # [CheckOptionDefinition(...)] — может быть []
def get_definition():     # CheckDefinition(key, title, description, runner=..., option_keys=[...])
```

Настройки хранятся в `pp_settings.json` → `model_check_settings.config` плоским
словарём по ключам опций. `script.py` делает `get_default_config()` + `update(сохранённое)`,
поэтому новые ключи в `DEFAULTS` подхватываются без миграции.
**Ключи опций и проверок не переименовывать** — у пользователей потеряются настройки
и отметки проверок.

## Общая опция на несколько проверок

Опция описывается **ровно в одном** файле — у первой проверки, которой она нужна.
Там же её значение в `DEFAULTS`, а в `check_keys` перечислены все проверки-пользователи.
Остальные проверки только упоминают ключ в своём `option_keys` (и комментарием
в шапке файла — где описана). Пример: `position_param` описана в
`check_position_category.py`, используется ещё `check_position_duplicate.py`.
Связь сторожит `validate_checks.py`.

## Как добавить проверку

1. Скопировать ближайший по смыслу `check_*.py`, дать новый ключ и новые ключи опций.
2. Хелпер, нужный только этой проверке, держать в её файле. Если он уже есть в чужом
   `check_*.py` и нужен второй проверке — перенести в `common.py` (имя не менять) и
   импортировать в обоих файлах.
3. Дописать модуль в `CHECK_MODULES` в `registry.py` — порядок списка = порядок в окне.
   Номер в заголовке («14. …») ставится руками.
4. Дописать строку в таблицу ниже.
5. Запустить `validate_checks.py`.

Типы опций, которые окно (`lib/pp_check_windows.py`) уже умеет: `text`, `param`,
`multiline`, `category_multiselect`. Пока хватает их — `script.py` и окно не трогать.

Кнопка работает на постоянном движке (`__persistentengine__`): после правки файлов
пакета нужна перезагрузка pyRevit (Reload), иначе в Revit останется старый код.

## Если проверка пропала из окна

Реестр грузит каждый модуль отдельно. Если в файле ошибка, остальные проверки
работают, а в окне вывода pyRevit печатается «не загрузился модуль …» с трассировкой;
тот же текст лежит в `registry.LOAD_ERRORS`.

## Самопроверка

```
python lib/model_checks/validate_checks.py
```

Обычный Python 3, Revit не нужен. Проверяет синтаксис, реестр ↔ файлы, наличие
`DEFAULTS`/`get_options`/`get_definition`, ключи (дубли, опция без дефолта и наоборот,
`option_keys` ↔ `check_keys`, владелец общей опции), импорты между проверками,
забытые импорты и в конце импортирует пакет с заглушками вместо Revit API.
Код выхода 0 — ошибок нет.

## Проверки

| № | Ключ | Файл | Опции |
|---|---|---|---|
| 1 | `pipe_flow_missing` | `check_pipe_flow.py` | `pipe_flow_param`, `pipe_flow_include_systems`, `pipe_flow_exclude_systems` |
| 2 | `slope_unexpected` | `check_slope.py` | `slope_param`, `slope_exclude_values`, `slope_tolerance` |
| 3 | `orphan_no_system` | `check_orphan.py` | `orphan_categories`, `orphan_include_partial` |
| 4 | `near_connectors` | `check_near_connectors.py` | `near_tolerance_mm` |
| 5 | `short_segment` | `check_short_segment.py` | `short_segment_mm`, `short_segment_categories` |
| 6 | `degenerate_geometry` | `check_degenerate.py` | `degenerate_tolerance_mm` |
| 7 | `duplicate_at_point` | `check_duplicate.py` | `duplicate_tolerance_mm`, `duplicate_same_type`, `duplicate_categories` |
| 8 | `pipe_insulation` | `check_pipe_insulation.py` | `insul_rules`, `insul_include_systems`, `insul_exclude_systems`, `insul_report_unclassified` |
| 9 | `position_wrong_category` | `check_position_category.py` | `position_param` (владелец) |
| 10 | `position_duplicate` | `check_position_duplicate.py` | `position_param` (из №9) |
| 11 | `grids_halftone` | `check_grids_halftone.py` | `grids_halftone_expected`, `grids_tpl_include`, `grids_tpl_exclude` |
| 12 | `smoke_distance` | `check_smoke_distance.py` | `smoke_categories`, `smoke_supply_masks`, `smoke_exhaust_masks`, `smoke_min_distance_mm`, `smoke_name_fallback` |
| 13 | `smoke_inlet_height` | `check_smoke_inlet_height.py` | `inlet_categories`, `inlet_masks`, `inlet_min_height_mm`, `inlet_floor_offset_mm`, `inlet_base`, `inlet_name_fallback` |

## Что в `common.py`

`CURVE_CATEGORY_OPTIONS`, `_parse_masks`, `_matches_any`, `_pipe_system_name`,
`_pipe_size_text`, `_pipe_label`, `_parse_float`, `_element_size_text`, `_element_label`,
`_parse_category_keys`, `_is_yes`, `_location_curve`, `_element_length_mm`,
`_location_point`, `_cell`, `_safe_name`, `_type_name`, `_parse_category_map`,
`_category_id`, `_duplicate_label`, `_param_text_from`, `_smoke_search_text`, `_smoke_label`.

Имена исторические: часть названа по проверке, где хелпер появился первым
(`_duplicate_label`, `_smoke_*`, `_pipe_*`), но используется шире. Переименование —
отдельная задача, не смешивать с переносом.
