# -*- coding: utf-8 -*-
"""Каркас проверок теплопотерь для инструмента «Проверка теплопотерь».

Устроен по образцу pp_model_checks.py: набор независимых проверок
(«стратегий»), у каждой свои опции; script.py рисует окна и запускает
отмеченные.

Как добавить проверку:
  * дописать CheckDefinition в get_check_definitions();
  * написать runner(doc, config, context) -> CheckResult или список
    CheckResult и, при нужде, опции в get_check_option_definitions()
    + дефолты в DEFAULT_HEATLOSS_CONFIG.

Отличия от pp_model_checks:
  * контекст запуска (RunContext) кроме кэша элементов несёт область
    проверки (вся модель / выделение), SpaceFinder с его кэшами и
    колбэк прогресса — поиск пространства долгий, без прогресса и
    отмены на большой модели работать нельзя;
  * runner может вернуть НЕСКОЛЬКО CheckResult — одна проверка разносит
    находки по типам проблем (расхождение / пусто / не найдено), чтобы
    их можно было выделять и красить по отдельности.
"""

import re

import clr

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    FilteredElementCollector,
    StorageType,
)

import pp_heatloss_spaces as spaces


# --------------------------------------------------------------------------- #
#                                конфигурация                                 #
# --------------------------------------------------------------------------- #

# Параметр, который «Перенос данных из пространств» пишет в стены/окна/двери.
PARAM_NUM_NAME = u"PP_Номер имя помещения"


DEFAULT_HEATLOSS_CONFIG = {
    # Проверка «Имя и номер помещения».
    # Имя проверяемого параметра (общий с «Переносом данных»).
    "numname_param": PARAM_NUM_NAME,
    # Ключи категорий через запятую (см. CATEGORY_OPTIONS).
    "numname_categories": u"walls, curtain, windows, doors",
    # Мягкое сравнение: регистр, лишние пробелы, похожие латинские буквы.
    "numname_soft_compare": u"да",
    # Показывать элементы с пустым параметром (перенос не делали).
    "numname_report_empty": u"да",
    # Показывать элементы, для которых пространство не найдено.
    "numname_report_missing": u"да",
    # Показывать элементы, где пространство найдено ненадёжным способом.
    "numname_report_unreliable": u"да",
    # Показывать окна/двери, у которых значение разошлось с хост-стеной.
    "numname_report_host": u"да",
}


# Категории проверки: (ключ, подпись). Стены и витражи — одна категория
# Revit (OST_Walls), но проверяются по-разному, поэтому ключи разные.
CATEGORY_OPTIONS = [
    (u"walls", u"Стены (обычные)"),
    (u"curtain", u"Витражи"),
    (u"windows", u"Окна"),
    (u"doors", u"Двери"),
    (u"floors", u"Перекрытия"),
]

CATEGORY_KEYS = [key for key, _label in CATEGORY_OPTIONS]

CATEGORY_LABELS = dict(CATEGORY_OPTIONS)

# Единственное число — для сообщений об элементе.
CATEGORY_SINGULAR = {
    u"walls": u"Стена",
    u"curtain": u"Витраж",
    u"windows": u"Окно",
    u"doors": u"Дверь",
    u"floors": u"Перекрытие",
}


class CheckCancelled(Exception):
    """Пользователь нажал «Отмена» в окне прогресса."""


# --------------------------------------------------------------------------- #
#                                модель данных                                #
# --------------------------------------------------------------------------- #

class CheckIssue(object):
    def __init__(self, message, element_ids=None):
        self.message = message
        self.element_ids = []

        if element_ids is None:
            return

        for element_id in element_ids:
            if element_id is None:
                continue
            try:
                self.element_ids.append(int(element_id))
            except:
                pass


class CheckResult(object):
    def __init__(self, key, title, checked_count, issues, info=None):
        self.key = key
        self.title = title
        self.checked_count = checked_count
        self.issues = issues or []
        # Свободный текст итога (показывается первой строкой в отчёте).
        self.info = info or u""


class CheckDefinition(object):
    def __init__(self, key, title, description, runner=None, option_keys=None):
        self.key = key
        self.title = title
        self.description = description
        self.runner = runner
        self.option_keys = option_keys or []


class CheckOptionDefinition(object):
    # option_type:
    #   "text"                 — текстовое поле (по умолчанию)
    #   "param"                — имя параметра: поле плюс кнопка
    #                            «Выбрать…» (см. lib/pp_param_picker)
    #   "category_multiselect" — список категорий с галочками (choices)
    def __init__(self, key, label, check_keys, option_type=u"text", choices=None):
        self.key = key
        self.label = label
        self.check_keys = check_keys
        self.option_type = option_type
        self.choices = choices or []


class RunContext(object):
    """Контекст одного запуска: кэш элементов, поиск пространств, прогресс.

    scope_element_ids:
        None      — проверяем всю модель;
        множество — проверяем только эти элементы (текущее выделение).

    progress:
        вызываемый объект progress(current, total); если он вернёт False,
        проверка прерывается исключением CheckCancelled.
    """

    def __init__(self, doc, scope_element_ids=None, progress=None):
        self.doc = doc
        self.finder = spaces.SpaceFinder(doc)
        self.scope_element_ids = scope_element_ids
        self.progress = progress
        self._by_category = {}

    @property
    def whole_model(self):
        return self.scope_element_ids is None

    def get_by_categories(self, categories):
        key = tuple(sorted(unicode(category) for category in categories))
        cached = self._by_category.get(key)
        if cached is not None:
            return cached

        result = _collect_elements(self.doc, categories)
        self._by_category[key] = result
        return result

    def report(self, current, total):
        if self.progress is None:
            return
        if self.progress(current, total) is False:
            raise CheckCancelled()


def create_run_context(doc, scope_element_ids=None, progress=None):
    return RunContext(doc, scope_element_ids, progress)


def _collect_elements(doc, categories):
    result = []
    for category in categories:
        try:
            collector = FilteredElementCollector(doc) \
                .OfCategory(category) \
                .WhereElementIsNotElementType()
            result.extend(list(collector.ToElements()))
        except:
            pass
    return result


# --------------------------------------------------------------------------- #
#                             определения проверок                            #
# --------------------------------------------------------------------------- #

def get_default_config():
    return dict(DEFAULT_HEATLOSS_CONFIG)


def get_check_option_definitions():
    return [
        CheckOptionDefinition(
            "numname_categories",
            u"Какие элементы проверять",
            ["numname_mismatch"],
            option_type=u"category_multiselect",
            choices=list(CATEGORY_OPTIONS)
        ),
        CheckOptionDefinition(
            "numname_param",
            u"Проверяемый параметр",
            ["numname_mismatch"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "numname_soft_compare",
            u"Мягкое сравнение: регистр, лишние пробелы, латиница (да/нет)",
            ["numname_mismatch"]
        ),
        CheckOptionDefinition(
            "numname_report_empty",
            u"Показывать элементы с пустым параметром (да/нет)",
            ["numname_mismatch"]
        ),
        CheckOptionDefinition(
            "numname_report_missing",
            u"Показывать элементы без найденного пространства (да/нет)",
            ["numname_mismatch"]
        ),
        CheckOptionDefinition(
            "numname_report_unreliable",
            u"Показывать ненадёжно найденные пространства (да/нет)",
            ["numname_mismatch"]
        ),
        CheckOptionDefinition(
            "numname_report_host",
            u"Показывать окна/двери, разошедшиеся с хост-стеной (да/нет)",
            ["numname_mismatch"]
        ),
    ]


def get_check_definitions():
    return [
        CheckDefinition(
            "numname_mismatch",
            u"1. Имя и номер помещения ≠ пространство рядом",
            u"Сверяет параметр «PP_Номер имя помещения» на стенах, витражах, "
            u"окнах, дверях и перекрытиях со значением пространства, которое "
            u"находится рядом с элементом. Пространство ищется тем же "
            u"алгоритмом, что и в «Переносе данных из пространств» (зонды по "
            u"обе стороны элемента), поэтому эталон совпадает с тем, что "
            u"перенос записал бы сейчас. Для окон и дверей эталон берётся с "
            u"хост-стены — так же, как это делает перенос.\n\n"
            u"Зачем: архитектор переименовал или перенумеровал помещение, вы "
            u"обновили пространства родным инструментом Revit — а на стенах и "
            u"проёмах осталось старое имя. Проверка показывает, где именно.\n\n"
            u"Находки разносятся по отдельным строкам отчёта: расхождение, "
            u"пустой параметр, пространство не найдено, пространство найдено "
            u"ненадёжно (2D-ближайшее — эталон под вопросом), окно/дверь "
            u"разошлись с хост-стеной. Лишние строки отключаются опциями.\n\n"
            u"Проверяются только элементы, у которых этот параметр вообще "
            u"есть: остальные к теплопотерям отношения не имеют.",
            runner=lambda doc, config, context: _run_numname_check(
                doc, config, context),
            option_keys=[
                "numname_categories",
                "numname_param",
                "numname_soft_compare",
                "numname_report_empty",
                "numname_report_missing",
                "numname_report_unreliable",
                "numname_report_host",
            ]
        ),
    ]


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


# --------------------------------------------------------------------------- #
#                             разбор значений опций                           #
# --------------------------------------------------------------------------- #

def _is_yes(raw_text):
    value = unicode(raw_text or u"").strip().lower()
    return value in (u"да", u"yes", u"1", u"true", u"истина", u"+")


def _parse_category_keys(raw_text):
    """Ключи категорий из строки настройки; пусто = набор по умолчанию."""
    result = []
    seen = set()
    cleaned = unicode(raw_text or u"").replace(u";", u",").replace(u"/", u",")
    for part in cleaned.split(u","):
        token = part.strip()
        if not token or token in seen:
            continue
        if token not in CATEGORY_LABELS:
            continue
        seen.add(token)
        result.append(token)

    if not result:
        result = [u"walls", u"curtain", u"windows", u"doors"]
    return result


# Похожие латинские буквы -> кириллица (номера помещений часто набирают
# в неверной раскладке: "С101" латинской C и кириллической С — разные строки).
_CONFUSABLES = {
    u"A": u"А", u"B": u"В", u"C": u"С", u"E": u"Е", u"H": u"Н",
    u"K": u"К", u"M": u"М", u"O": u"О", u"P": u"Р", u"T": u"Т",
    u"X": u"Х", u"Y": u"У",
    u"a": u"а", u"c": u"с", u"e": u"е", u"o": u"о", u"p": u"р",
    u"x": u"х", u"y": u"у",
}

_SPACE_RE = re.compile(u"\\s+", re.UNICODE)


def _fold_confusables(text):
    return u"".join([_CONFUSABLES.get(char, char) for char in text])


def normalize_value(text, soft=True):
    """Приведение строки к виду для сравнения."""
    value = unicode(text or u"")
    value = value.replace(u" ", u" ")   # неразрывный пробел
    value = _SPACE_RE.sub(u" ", value).strip()
    if soft:
        value = _fold_confusables(value)
        value = value.lower()
        value = value.replace(u"ё", u"е")
    return value


# --------------------------------------------------------------------------- #
#                          подписи элементов для отчёта                       #
# --------------------------------------------------------------------------- #

def _builtins(*names):
    """BuiltInParameter по именам; отсутствующие в этой версии Revit пропускаем."""
    result = []
    for name in names:
        try:
            value = getattr(BuiltInParameter, name)
        except:
            value = None
        if value is not None:
            result.append(value)
    return result


_TYPE_NAME_PARAMS = _builtins(u"SYMBOL_NAME_PARAM", u"ALL_MODEL_TYPE_NAME")

_LEVEL_PARAMS = _builtins(
    u"WALL_BASE_CONSTRAINT",
    u"FAMILY_LEVEL_PARAM",
    u"LEVEL_PARAM",
    u"SCHEDULE_LEVEL_PARAM",
)


def _get_type_name(doc, element):
    try:
        type_element = doc.GetElement(element.GetTypeId())
    except:
        type_element = None

    if type_element is None:
        return u""

    for builtin in _TYPE_NAME_PARAMS:
        try:
            param = type_element.get_Parameter(builtin)
            if param and param.HasValue:
                value = param.AsString()
                if value:
                    return value
        except:
            pass

    try:
        return type_element.Name or u""
    except:
        return u""


def _category_key_for(element):
    category_id = spaces.get_category_id(element)
    if category_id == spaces.WALL_CAT:
        return u"curtain" if spaces.is_curtain_wall(element) else u"walls"
    if category_id == spaces.WINDOW_CAT:
        return u"windows"
    if category_id == spaces.DOOR_CAT:
        return u"doors"
    if category_id == spaces.FLOOR_CAT:
        return u"floors"
    return None


def _element_label(doc, element):
    key = _category_key_for(element)
    prefix = CATEGORY_SINGULAR.get(key, u"Элемент")
    type_name = _get_type_name(doc, element)

    try:
        element_id = element.Id.IntegerValue
    except:
        element_id = 0

    if type_name:
        return u"{0} «{1}» id {2}".format(prefix, type_name, element_id)
    return u"{0} id {1}".format(prefix, element_id)


def _level_name(doc, element):
    """Имя уровня — по нему удобно ориентироваться в длинном отчёте."""
    for builtin in _LEVEL_PARAMS:
        try:
            param = element.get_Parameter(builtin)
            if param and param.HasValue and param.StorageType == StorageType.ElementId:
                level = doc.GetElement(param.AsElementId())
                if level is not None:
                    name = level.Name
                    if name:
                        return name
        except:
            pass
    return u""


def _with_level(doc, element, text):
    level = _level_name(doc, element)
    if level:
        return u"{0} [{1}]".format(text, level)
    return text


# --------------------------------------------------------------------------- #
#                         сбор элементов для проверки                         #
# --------------------------------------------------------------------------- #

def _read_text_param(element, param_name):
    """Значение текстового параметра или None, если параметра нет.

    Возвращает (есть_параметр, текст).
    """
    try:
        param = element.LookupParameter(param_name)
    except:
        param = None

    if param is None:
        return False, u""

    try:
        if param.StorageType == StorageType.String:
            return True, unicode(param.AsString() or u"")
        value = param.AsValueString()
        return True, unicode(value or u"")
    except:
        return True, u""


def _collect_targets(doc, context, category_keys, param_name):
    """Элементы для проверки: нужные категории + наличие параметра.

    Возвращает список кортежей (элемент, ключ_категории).
    """
    wanted = set(category_keys)
    candidates = []

    if context.whole_model:
        categories = []
        if u"walls" in wanted or u"curtain" in wanted:
            categories.append(BuiltInCategory.OST_Walls)
        if u"windows" in wanted:
            categories.append(BuiltInCategory.OST_Windows)
        if u"doors" in wanted:
            categories.append(BuiltInCategory.OST_Doors)
        if u"floors" in wanted:
            categories.append(BuiltInCategory.OST_Floors)
        if categories:
            candidates = context.get_by_categories(categories)
    else:
        for element_id in context.scope_element_ids:
            try:
                element = doc.GetElement(element_id)
            except:
                element = None
            if element is not None:
                candidates.append(element)

    targets = []
    for element in candidates:
        key = _category_key_for(element)
        if key is None or key not in wanted:
            continue
        has_param, _text = _read_text_param(element, param_name)
        if not has_param:
            continue
        targets.append((element, key))

    return targets


# --------------------------------------------------------------------------- #
#          проверка №1: имя и номер помещения против пространства рядом       #
# --------------------------------------------------------------------------- #

def _run_numname_check(doc, config, context):
    param_name = unicode(config.get("numname_param") or PARAM_NUM_NAME).strip()
    if not param_name:
        param_name = PARAM_NUM_NAME

    category_keys = _parse_category_keys(config.get("numname_categories"))
    soft = _is_yes(config.get("numname_soft_compare"))
    report_empty = _is_yes(config.get("numname_report_empty"))
    report_missing = _is_yes(config.get("numname_report_missing"))
    report_unreliable = _is_yes(config.get("numname_report_unreliable"))
    report_host = _is_yes(config.get("numname_report_host"))

    targets = _collect_targets(doc, context, category_keys, param_name)
    total = len(targets)

    mismatch = []
    empty = []
    missing = []
    unreliable = []
    host_mismatch = []

    finder = context.finder
    checked = 0

    for index, pair in enumerate(targets):
        element, category_key = pair
        context.report(index, total)

        try:
            hit, host = finder.find(element)
        except CheckCancelled:
            raise
        except Exception as ex:
            missing.append(CheckIssue(
                _with_level(doc, element, u"{0}: сбой поиска пространства ({1})".format(
                    _element_label(doc, element), unicode(ex))),
                [element.Id.IntegerValue]
            ))
            continue

        checked += 1
        _has_param, actual = _read_text_param(element, param_name)
        label = _element_label(doc, element)

        # Окно/дверь против хост-стены — сравнение без геометрии.
        if report_host and category_key in (u"windows", u"doors") and host is not None:
            host_has, host_value = _read_text_param(host, param_name)
            if host_has and normalize_value(host_value, soft) != normalize_value(actual, soft):
                host_mismatch.append(CheckIssue(
                    _with_level(doc, element, (
                        u"{0}: у элемента «{1}», у хост-стены id {2} — «{3}»"
                    ).format(label, actual, host.Id.IntegerValue, host_value)),
                    [element.Id.IntegerValue, host.Id.IntegerValue]
                ))

        if not hit.found:
            if report_missing:
                note = hit.note or u"пространство рядом не найдено"
                missing.append(CheckIssue(
                    _with_level(doc, element, u"{0}: {1}; записано «{2}»".format(
                        label, note, actual)),
                    [element.Id.IntegerValue]
                ))
            continue

        expected = spaces.build_num_name(hit.space)
        space_id = hit.space.Id.IntegerValue

        if not normalize_value(actual, soft):
            if report_empty:
                empty.append(CheckIssue(
                    _with_level(doc, element, (
                        u"{0}: параметр пуст, рядом пространство «{1}»"
                    ).format(label, expected)),
                    [element.Id.IntegerValue, space_id]
                ))
        elif normalize_value(actual, soft) != normalize_value(expected, soft):
            mismatch.append(CheckIssue(
                _with_level(doc, element, (
                    u"{0}: записано «{1}» → рядом «{2}» ({3})"
                ).format(label, actual, expected, hit.method_label())),
                [element.Id.IntegerValue, space_id]
            ))

        if report_unreliable and not hit.reliable:
            unreliable.append(CheckIssue(
                _with_level(doc, element, (
                    u"{0}: пространство «{1}» найдено 2D-ближайшим — "
                    u"проверьте вручную; записано «{2}»"
                ).format(label, expected, actual)),
                [element.Id.IntegerValue, space_id]
            ))

    context.report(total, total)

    scope_text = u"вся модель" if context.whole_model else u"текущее выделение"
    base_info = u"Проверено элементов: {0} ({1}). Параметр: {2}.".format(
        checked, scope_text, param_name)

    results = [
        CheckResult(
            "numname_mismatch",
            u"1. Расхождение с пространством",
            checked,
            mismatch,
            info=base_info
        )
    ]

    if report_empty:
        results.append(CheckResult(
            "numname_empty",
            u"1а. Параметр не заполнен",
            checked,
            empty,
            info=u"Перенос данных для этих элементов не делали."
        ))

    if report_missing:
        results.append(CheckResult(
            "numname_missing",
            u"1б. Пространство рядом не найдено",
            checked,
            missing,
            info=u"Проверьте расчёт объёмов, фазу и наличие пространства."
        ))

    if report_unreliable:
        results.append(CheckResult(
            "numname_unreliable",
            u"1в. Пространство найдено ненадёжно",
            checked,
            unreliable,
            info=u"Эталон получен 2D-ближайшим пространством — может быть неверным."
        ))

    if report_host:
        results.append(CheckResult(
            "numname_host",
            u"1г. Окно/дверь ≠ хост-стена",
            checked,
            host_mismatch,
            info=u"Значение проёма разошлось со стеной, в которую он вставлен."
        ))

    return results
