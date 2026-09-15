# Проверки «Проверки теплопотерь» — пакет `lib/heatloss_checks/`

Инструмент «Проверка теплопотерь» (панель «Теплопотери»). Раньше код лежал одним
файлом `lib/pp_heatloss_checks.py` (~700 строк). 15.09.2026 он механически разнесён
по файлам по образцу `lib/model_checks/` и `lib/spec_checks/`. Логика, тексты, ключи
настроек и порядок опций не менялись. Модель только читается, в неё ничего не пишется.

## Устройство

| Файл | Что внутри |
|---|---|
| `lib/pp_heatloss_checks.py` | **Фасад.** Отдаёт те же имена, что и раньше: `CheckCancelled`, `create_run_context`, `get_check_definitions`, `get_check_option_definitions`, `get_default_config`, `run_report_checks` (плюс `DEFAULT_HEATLOSS_CONFIG`, `normalize_value`). Из него импортирует `script.py` кнопки. Новый код сюда не писать. |
| `core.py` | Каркас: `CheckIssue`, `CheckResult`, `CheckDefinition`, `CheckOptionDefinition`, `RunContext`, `create_run_context`, `CheckCancelled`. |
| `common.py` | Общая инфраструктура: разбор опций, мягкое сравнение значений, категории, подписи элементов для отчёта, сбор проверяемых элементов. |
| `registry.py` | Явный список `CHECK_MODULES`, загрузка каждого модуля в своём `try/except`, сборка значений по умолчанию, опций и определений, `run_report_checks`. |
| `check_*.py` | Одна проверка = один файл. Сейчас одна: `check_numname.py`. |
| `validate_checks.py` | Самопроверка без Revit (см. ниже). |
| `lib/pp_heatloss_spaces.py` | **Вне пакета, оставлен на месте.** `SpaceFinder` — поиск пространства рядом с элементом, портирован из «Переноса данных из пространств». Если меняется алгоритм поиска в одном из них, править и во втором, иначе проверка начнёт ругаться на значения, которые перенос сам же записал. |

Зависимости идут только вниз: `check_*.py` → `common.py` → `core.py`;
`registry.py` → `check_*.py`. `core.py` и `common.py` импортируют `pp_heatloss_spaces`.

**Почему `common.py` не пустой при одной проверке.** В двух других пакетах правило
такое: в `common.py` лежит только то, что нужно двум и больше проверкам. Здесь
сознательное исключение (решение 15.09.2026): разделы «разбор опций», «сравнение»,
«категории», «подписи» и «сбор элементов» уже в исходном файле были общими, и любая
следующая проверка теплопотерь их использует. Хелпер, нужный только одной проверке,
по-прежнему кладётся в её `check_*.py`.

## Чем каркас отличается от «Проверки Модели»

* Вместо кэша элементов runner получает **`RunContext`**: область проверки
  (`whole_model` или `scope_element_ids` — текущее выделение), `finder`
  (`SpaceFinder` со своими кэшами), `get_by_categories()` и `report(current, total)`.
  `report()` бросает `CheckCancelled`, если пользователь нажал «Отмена». В долгих
  циклах его надо вызывать.
* Runner может вернуть **`CheckResult` или список `CheckResult`**: одна проверка
  разносит находки по строкам отчёта (расхождение / пусто / не найдено / ненадёжно /
  окно≠хост), чтобы их выделять и красить по отдельности. `run_report_checks`
  разворачивает список.
* У `check_numname.py` runner задан лямбдой `lambda doc, config, context: _run_numname_check(...)`,
  так было в исходнике. Валидатор это понимает.

## Из чего состоит `check_*.py`

```python
<свои константы, нужные DEFAULTS (например PARAM_NUM_NAME)>
DEFAULTS = {...}          # значения по умолчанию опций этой проверки
def _run_..._check(doc, config, context): ...   # -> CheckResult или [CheckResult]
def get_options():        # [CheckOptionDefinition(...)]
def get_definition():     # CheckDefinition(key, title, description, runner=..., option_keys=[...])
```

Настройки хранятся в `pp_settings.json` → `heatloss_check_settings`. `script.py` берёт
`get_default_config()` и накладывает сохранённое, поэтому новые ключи в `DEFAULTS`
подхватываются без миграции. **Ключи опций и проверок не переименовывать**, иначе у
пользователей потеряются настройки и отметки проверок.

Опция описывается ровно в одном файле, там же её значение по умолчанию. Если опция
понадобится двум проверкам, её описывает первая, а вторая только перечисляет ключ в
`option_keys`.

## Как добавить проверку

1. Создать `check_<имя>.py` по образцу `check_numname.py` с новым ключом и новыми
   ключами опций.
2. Для обхода элементов использовать `common._collect_targets`, для подписей в отчёте
   `_element_label` / `_with_level`, для сравнения `normalize_value`. В долгом цикле
   вызывать `context.report(i, total)`.
3. Дописать модуль в `CHECK_MODULES` в `registry.py`: порядок = порядок в окне.
   Номер в заголовке («2. …») ставится руками.
4. Дописать строку в таблицу ниже.
5. Запустить `validate_checks.py`.

Окно (`pp_heatloss_check_windows.py` рядом со `script.py`) строит опции по
`option_type`. Пока хватает существующих типов, окно и `script.py` не трогать.
Кнопка работает на постоянном движке: после правки файлов пакета нужна перезагрузка
pyRevit (Reload).

## Если проверка пропала из окна

Реестр грузит каждый модуль отдельно. Если в файле ошибка, в окне вывода pyRevit
печатается «не загрузился модуль …» с трассировкой; тот же текст лежит в
`registry.LOAD_ERRORS`.

## Самопроверка

```
python lib/heatloss_checks/validate_checks.py
```

Обычный Python 3, Revit не нужен. Проверяет синтаксис, реестр ↔ файлы, наличие
`DEFAULTS`/`get_options`/`get_definition`, runner (в том числе через лямбду), ключи
(дубли, опция без значения по умолчанию и наоборот, `option_keys` ↔ `check_keys`),
чтение `DEFAULTS["ключ"]`, импорты между проверками, забытые импорты. В конце
импортирует пакет вместе с `pp_heatloss_spaces` с заглушками вместо Revit API.
Код выхода 0 — ошибок нет.

## Проверки

| № | Ключ | Файл | Опции |
|---|---|---|---|
| 1 | `numname_mismatch` | `check_numname.py` | `numname_categories`, `numname_param`, `numname_soft_compare`, `numname_report_empty`, `numname_report_missing`, `numname_report_unreliable`, `numname_report_host` |

## Что в `common.py`

* Категории: `CATEGORY_OPTIONS`, `CATEGORY_KEYS` (сейчас не используется, перенесён как был), `CATEGORY_LABELS`, `CATEGORY_SINGULAR`.
* Разбор опций: `_is_yes`, `_parse_category_keys`.
* Сравнение: `_CONFUSABLES`, `_SPACE_RE`, `_fold_confusables`, `normalize_value`.
* Подписи: `_builtins`, `_TYPE_NAME_PARAMS`, `_LEVEL_PARAMS`, `_get_type_name`, `_category_key_for`, `_element_label`, `_level_name`, `_with_level`.
* Сбор элементов: `_read_text_param`, `_collect_targets`.

`_is_yes` продублирован как `is_yes` в `pp_heatloss_check_windows.py` (тот же набор значений «да»).
Меняете один — поменяйте второй.
