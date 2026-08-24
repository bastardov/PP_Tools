# -*- coding: utf-8 -*-

import clr
import re

clr.AddReference("RevitAPI")

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    FilteredElementCollector,
    StorageType,
)

import pp_mep_filter


MM_PER_FOOT = 304.8
PIPE_AND_DUCT_CATEGORIES = [
    BuiltInCategory.OST_PipeCurves,
    BuiltInCategory.OST_FlexPipeCurves,
    BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_FlexDuctCurves,
]
DUCT_ONLY_CATEGORIES = [
    BuiltInCategory.OST_DuctCurves,
]
SYSTEM_GROUP_CATEGORIES = [
    BuiltInCategory.OST_DuctCurves,
    BuiltInCategory.OST_DuctAccessory,
    BuiltInCategory.OST_DuctInsulations,
    BuiltInCategory.OST_DuctFitting,
]

# Категории, доступные для выбора в стратегиях с мультивыбором категорий.
# (ключ, подпись в интерфейсе, BuiltInCategory)
SORT_ORDER_CATEGORY_OPTIONS = [
    (u"OST_MechanicalEquipment", u"Оборудование", BuiltInCategory.OST_MechanicalEquipment),
    (u"OST_PipeCurves", u"Трубы", BuiltInCategory.OST_PipeCurves),
    (u"OST_DuctCurves", u"Воздуховоды", BuiltInCategory.OST_DuctCurves),
    (u"OST_PipeAccessory", u"Арматура трубопроводов", BuiltInCategory.OST_PipeAccessory),
    (u"OST_DuctAccessory", u"Арматура воздуховодов", BuiltInCategory.OST_DuctAccessory),
    (u"OST_DuctTerminal", u"Воздухораспределители", BuiltInCategory.OST_DuctTerminal),
    (u"OST_PipeFitting", u"Соединительные детали трубопроводов", BuiltInCategory.OST_PipeFitting),
    (u"OST_FlexPipeCurves", u"Гибкие трубы", BuiltInCategory.OST_FlexPipeCurves),
    (u"OST_PipeInsulations", u"Материалы изоляции труб", BuiltInCategory.OST_PipeInsulations),
    (u"OST_DuctFitting", u"Соединительные детали воздуховодов", BuiltInCategory.OST_DuctFitting),
    (u"OST_FlexDuctCurves", u"Гибкие воздуховоды", BuiltInCategory.OST_FlexDuctCurves),
    (u"OST_DuctInsulations", u"Материалы изоляции воздуховодов", BuiltInCategory.OST_DuctInsulations),
    (u"OST_GenericModel", u"Обобщенные модели", BuiltInCategory.OST_GenericModel),
]

# ключ категории -> BuiltInCategory
SORT_ORDER_CATEGORY_MAP = {
    key: built_in for key, _label, built_in in SORT_ORDER_CATEGORY_OPTIONS
}

# Список ключей всех категорий (используется как значение по умолчанию).
SORT_ORDER_ALL_CATEGORY_KEYS = [
    key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
]

# Категории для стратегии сверки по MEP (как в инструменте «Передача по MEP»).
# Ключи совпадают с SORT_ORDER_CATEGORY_MAP, поэтому маппинг общий.
MEP_TRANSFER_CATEGORY_OPTIONS = [
    (u"OST_PipeCurves", u"Трубы"),
    (u"OST_PipeFitting", u"Соединительные детали трубопроводов"),
    (u"OST_PipeAccessory", u"Арматура трубопроводов"),
    (u"OST_MechanicalEquipment", u"Оборудование"),
]


DEFAULT_SPEC_CONFIG = {
    "name_presence_param": u"ADSK_Наименование",
    "quantity_param": u"ADSK_количество",
    "bzero_name_param": u"ADSK_Наименование",
    "bzero_text": u"b=0 мм; класс герметичности ,",
    "bzero_categories": u"OST_DuctCurves, OST_DuctFitting",
    "metal_thickness_param": u"ADSK_Толщина металла",
    "copper_keyword_source": u"Имя семейства",
    "copper_size_param": u"Диаметр",
    "copper_standard_sizes_mm": u"6, 8, 10, 12, 15, 18, 22, 28, 35, 42, 54, 64, 76, 89, 108",
    "copper_keywords": u"мед, copper",
    "grouping_param": u"ADSK_Группирование",
    "sort_order_param": u"PP_Порядок сортировки",
    "sort_order_categories": u", ".join([
        key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
    ]),
    "mep_source_categories": u"OST_PipeCurves",
    "mep_source_param": u"ADSK_Имя системы сокращенное",
    "mep_receiver_categories": u"OST_PipeAccessory",
    "mep_receiver_param": u"ADSK_Имя системы сокращенное",
    "size_name_name_param": u"ADSK_Наименование",
    "size_name_size_param": u"Размер",
    "size_name_categories": u"OST_DuctCurves",
    "pipe_size_name_param": u"ADSK_Наименование",
    "pipe_size_categories": u"OST_PipeCurves",
    "pipe_gost1_keyword": u"3262-75",
    "pipe_gost1_size_param": u"Диаметр",
    "pipe_gost2_keyword": u"10704-91",
    "pipe_gost2_size_param": u"Внешний диаметр",
    "proxy_ctrl_family": u"PP_Семейство контроллер 1",
    "proxy_ctrl_proxy_family": u"PP_Прокси позиция 1",
    "proxy_ctrl_code_param": u"PP_Код правила",
    "proxy_ctrl_value_param": u"ADSK_Количество",
    "proxy_ctrl_mapping": (
        u"PP_Итоговое кол-во металла для воздуховодов = МЕТАЛЛ_КРЕПЛЕНИЙ_ВОЗДУХОВОДОВ\n"
        u"PP_Итоговое кол-во металла для труб = МЕТАЛЛ_КРЕПЛЕНИЙ_ТРУБ\n"
        u"PP_Утеплитель для окожушивания = Утеплитель для окожушивания\n"
        u"PP_Металл для окожушивания = Металл для окожушивания\n"
        u"PP_Клей для огнезащиты = Клей для огнезащиты"
    ),
    "badtext_params": u"ADSK_Наименование, ADSK_Марка",
    "badtext_phrases": u"Не найдено\nНет в каталоге",
    "badtext_categories": u", ".join([
        key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
    ]),
    "param_rules_target": u"ADSK_Система_Сокращение",
    "param_rules_text": (
        u"# формат: Значение | Категории через запятую | все|любое | условие ; условие\n"
        u"# операторы: ~ содержит, !~ не содержит, = равно, != не равно, ^= начинается с\n"
        u"# проверяются только элементы, подходящие под условие правила\n"
        u"Отопление | Трубы, Арматура трубопроводов, Соединительные детали трубопроводов, Материалы изоляции труб, Оборудование | любое | Имя системы ~ T11 ; Имя системы ~ T21\n"
        u"Вентснабжение | Трубы, Арматура трубопроводов, Соединительные детали трубопроводов, Материалы изоляции труб, Оборудование | любое | Имя системы ~ T12 ; Имя системы ~ T22\n"
        u"Теплоснабжение | Трубы, Арматура трубопроводов, Соединительные детали трубопроводов, Материалы изоляции труб, Оборудование | все | Имя системы ~ T1 ; Имя системы !~ T11 ; Имя системы !~ T12\n"
        u"Теплоснабжение | Трубы, Арматура трубопроводов, Соединительные детали трубопроводов, Материалы изоляции труб, Оборудование | все | Имя системы ~ T2 ; Имя системы !~ T21 ; Имя системы !~ T22\n"
        u"Дренаж | Арматура трубопроводов, Соединительные детали трубопроводов, Трубы | все | Имя системы ~ Др"
    ),
    "nested_heat_param": u"ADSK_Система_Сокращение",
    "nested_heat_categories": u", ".join([
        key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
    ]),
    "nested_vent_param1": u"Имя системы",
    "nested_vent_param2": u"ADSK_Группирование",
    "nested_vent_categories": u", ".join([
        key for key, _label, _built_in in SORT_ORDER_CATEGORY_OPTIONS
    ]),
    "mep_filter": {
        "enabled": False,
        "match": "all",
        "group_combine": "and",
        "apply_source": True,
        "apply_receiver": False,
        "conditions": [],
    },
}


class CheckIssue(object):
    def __init__(self, message, element_ids):
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
    def __init__(self, key, title, checked_count, issues):
        self.key = key
        self.title = title
        self.checked_count = checked_count
        self.issues = issues or []


class CheckDefinition(object):
    def __init__(self, key, title, description, runner, option_keys=None):
        self.key = key
        self.title = title
        self.description = description
        self.runner = runner
        self.option_keys = option_keys or []


class CheckOptionDefinition(object):
    # option_type:
    #   "text"                 - обычное текстовое поле (по умолчанию)
    #   "param"                - имя параметра: поле плюс кнопка
    #                            "Выбрать..." (см. lib/pp_param_picker)
    #   "category_multiselect" - список категорий с галочками; choices задает
    #                            пункты как список кортежей (ключ, подпись)
    def __init__(self, key, label, check_keys, option_type=u"text", choices=None):
        self.key = key
        self.label = label
        self.check_keys = check_keys
        self.option_type = option_type
        self.choices = choices or []


class ElementCache(object):
    """Кэш коллекций элементов на время одного запуска инструмента.

    Создается заново на каждый запуск (в script.py), поэтому на
    __persistentengine__ не остается устаревших данных между вызовами.
    """

    def __init__(self, doc):
        self.doc = doc
        self._by_category = {}
        self._all = None

    def get_by_categories(self, categories):
        key = tuple(sorted(unicode(category) for category in categories))
        cached = self._by_category.get(key)
        if cached is not None:
            return cached

        result = _collect_elements(self.doc, categories)
        self._by_category[key] = result
        return result

    def get_all(self):
        if self._all is None:
            self._all = _collect_all_elements(self.doc)
        return self._all


def create_element_cache(doc):
    return ElementCache(doc)


def get_default_config():
    return dict(DEFAULT_SPEC_CONFIG)


def get_check_option_definitions():
    return [
        CheckOptionDefinition(
            "name_presence_param",
            u"Параметр для проверки",
            ["name_presence"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "quantity_param",
            u"Параметр для проверки",
            ["quantity_presence"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "bzero_name_param",
            u"Параметр для проверки",
            ["duct_bzero"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "bzero_text",
            u"Тексты для поиска через ; (логика ИЛИ)",
            ["duct_bzero"]
        ),
        CheckOptionDefinition(
            "bzero_categories",
            u"Категории для проверки",
            ["duct_bzero"],
            option_type=u"category_multiselect",
            choices=[
                (u"OST_DuctCurves", u"Воздуховоды"),
                (u"OST_DuctFitting", u"Соединительные детали воздуховодов"),
            ]
        ),
        CheckOptionDefinition(
            "metal_thickness_param",
            u"Параметр для проверки",
            ["duct_metal_thickness"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "copper_keyword_source",
            u"Где искать ключевые слова (Имя семейства / Имя типа / параметр / Авто)",
            ["copper_pipe_sizes"]
        ),
        CheckOptionDefinition(
            "copper_keywords",
            u"Ключевые слова медной трубы",
            ["copper_pipe_sizes"]
        ),
        CheckOptionDefinition(
            "copper_size_param",
            u"Параметр размера для проверки (например Диаметр или ADSK_Размер)",
            ["copper_pipe_sizes"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "copper_standard_sizes_mm",
            u"Стандартные размеры, мм",
            ["copper_pipe_sizes"]
        ),
        CheckOptionDefinition(
            "grouping_param",
            u"Параметр группирования",
            ["system_grouping"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "sort_order_param",
            u"Параметр для проверки",
            ["sort_order"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "sort_order_categories",
            u"Категории для проверки",
            ["sort_order"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "mep_source_categories",
            u"Категории источника",
            ["mep_name_match"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label in MEP_TRANSFER_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "mep_source_param",
            u"Параметр источника",
            ["mep_name_match"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "mep_receiver_categories",
            u"Категории приемника",
            ["mep_name_match"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label in MEP_TRANSFER_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "mep_receiver_param",
            u"Параметр приемника",
            ["mep_name_match"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "mep_filter",
            u"Фильтр элементов",
            ["mep_name_match"],
            option_type=u"mep_filter"
        ),
        CheckOptionDefinition(
            "size_name_name_param",
            u"Параметр наименования",
            ["size_in_name"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "size_name_size_param",
            u"Параметр размера",
            ["size_in_name"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "size_name_categories",
            u"Категории для проверки",
            ["size_in_name"],
            option_type=u"category_multiselect",
            choices=[
                (u"OST_DuctCurves", u"Воздуховоды"),
                (u"OST_DuctFitting", u"Соединительные детали воздуховодов"),
            ]
        ),
        CheckOptionDefinition(
            "pipe_size_name_param",
            u"Параметр наименования",
            ["pipe_size_in_name"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "pipe_size_categories",
            u"Категории для проверки",
            ["pipe_size_in_name"],
            option_type=u"category_multiselect",
            choices=[
                (u"OST_PipeCurves", u"Трубы"),
            ]
        ),
        CheckOptionDefinition(
            "pipe_gost1_keyword",
            u"Тип трубы 1: текст в имени типа",
            ["pipe_size_in_name"]
        ),
        CheckOptionDefinition(
            "pipe_gost1_size_param",
            u"Тип трубы 1: параметр размера",
            ["pipe_size_in_name"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "pipe_gost2_keyword",
            u"Тип трубы 2: текст в имени типа",
            ["pipe_size_in_name"]
        ),
        CheckOptionDefinition(
            "pipe_gost2_size_param",
            u"Тип трубы 2: параметр размера",
            ["pipe_size_in_name"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "proxy_ctrl_family",
            u"Семейство контроллера",
            ["proxy_controller"]
        ),
        CheckOptionDefinition(
            "proxy_ctrl_proxy_family",
            u"Семейство прокси-позиции",
            ["proxy_controller"]
        ),
        CheckOptionDefinition(
            "proxy_ctrl_code_param",
            u"Параметр кода правила в прокси",
            ["proxy_controller"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "proxy_ctrl_value_param",
            u"Параметр значения для сверки (в прокси)",
            ["proxy_controller"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "proxy_ctrl_mapping",
            u"Связки: параметр контроллера = КОД (по одной на строку)",
            ["proxy_controller"],
            option_type=u"multiline"
        ),
        CheckOptionDefinition(
            "badtext_params",
            u"Параметры для проверки (через запятую)",
            ["forbidden_text"]
        ),
        CheckOptionDefinition(
            "badtext_phrases",
            u"Запрещенные фразы (по одной на строку)",
            ["forbidden_text"],
            option_type=u"multiline"
        ),
        CheckOptionDefinition(
            "badtext_categories",
            u"Категории для проверки",
            ["forbidden_text"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "param_rules_target",
            u"Целевой параметр",
            ["param_rules"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "param_rules_text",
            u"Правила: Значение | Категории | все/любое | условия (по одному на строку)",
            ["param_rules"],
            option_type=u"multiline"
        ),
        CheckOptionDefinition(
            "nested_heat_param",
            u"Параметр для показа",
            ["nested_heat"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "nested_heat_categories",
            u"Категории для проверки",
            ["nested_heat"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS]
        ),
        CheckOptionDefinition(
            "nested_vent_param1",
            u"Параметр 1 для показа",
            ["nested_vent"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "nested_vent_param2",
            u"Параметр 2 для показа",
            ["nested_vent"],
            option_type=u"param"
        ),
        CheckOptionDefinition(
            "nested_vent_categories",
            u"Категории для проверки",
            ["nested_vent"],
            option_type=u"category_multiselect",
            choices=[(key, label) for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS]
        ),
    ]


def get_check_definitions():
    return [
        CheckDefinition(
            "name_presence",
            u"1. Наименования у труб и воздуховодов",
            u"Ищет трубы и воздуховоды с пустым параметром ADSK_Наименование.",
            _run_name_presence_check,
            option_keys=["name_presence_param"]
        ),
        CheckDefinition(
            "quantity_presence",
            u"2. Пустое или нулевое ADSK_количество",
            u"Ищет трубы и воздуховоды с пустым или нулевым параметром количества.",
            _run_quantity_presence_check,
            option_keys=["quantity_param"]
        ),
        CheckDefinition(
            "duct_bzero",
            u"3. Запрещенные тексты в наименовании",
            u"Ищет любой фрагмент из списка. Несколько значений пишите через ; "
            u"например: b=0 мм; класс герметичности ,",
            _run_duct_bzero_check,
            option_keys=["bzero_name_param", "bzero_text", "bzero_categories"]
        ),
        CheckDefinition(
            "duct_metal_thickness",
            u"4. Пустая или нулевая толщина металла",
            u"Ищет воздуховоды с пустым или нулевым параметром ADSK_Толщина металла.",
            _run_duct_metal_thickness_check,
            option_keys=["metal_thickness_param"]
        ),
        CheckDefinition(
            "copper_pipe_sizes",
            u"5. Нестандартные размеры медной трубы",
            u"Ищет медные трубы по выбранному полю и проверяет выбранный размер по списку стандартов.",
            _run_copper_pipe_sizes_check,
            option_keys=[
                "copper_keyword_source",
                "copper_keywords",
                "copper_size_param",
                "copper_standard_sizes_mm"
            ]
        ),
        CheckDefinition(
            "system_grouping",
            u"6. Имя системы и ADSK_Группирование",
            u"Сравнивает имя системы и ADSK_Группирование у воздуховодов и связанных категорий.",
            _run_system_grouping_check,
            option_keys=["grouping_param"]
        ),
        CheckDefinition(
            "sort_order",
            u"7. Заполнение PP_Порядок сортировки",
            u"Ищет элементы выбранных категорий с пустым параметром PP_Порядок сортировки.",
            _run_sort_order_check,
            option_keys=["sort_order_param", "sort_order_categories"]
        ),
        CheckDefinition(
            "mep_name_match",
            u"8. Совпадение имени по MEP-соединениям",
            u"Сверяет параметр приемника со значением подключенных источников "
            u"(проверка после «Передача по MEP»): ищет потерянные или несовпадающие значения.",
            _run_mep_name_match_check,
            option_keys=[
                "mep_source_categories",
                "mep_source_param",
                "mep_receiver_categories",
                "mep_receiver_param",
                "mep_filter"
            ]
        ),
        CheckDefinition(
            "size_in_name",
            u"9. Размер в ADSK_Наименование на ВЕНТИЛЯЦИИ",
            u"Проверяет, что значение параметра «Размер» присутствует в "
            u"ADSK_Наименование (после сборки имени размер должен совпадать).",
            _run_size_in_name_check,
            option_keys=[
                "size_name_name_param",
                "size_name_size_param",
                "size_name_categories"
            ]
        ),
        CheckDefinition(
            "pipe_size_in_name",
            u"10. Размер в ADSK_Наименование на ОТОПЛЕНИЕ",
            u"Проверяет совпадение размера трубы с ADSK_Наименование. Тип трубы "
            u"определяется по имени типа: для «3262-75» берется «Диаметр», для "
            u"«10704-91» — «Внешний диаметр». Отчет общий по обоим типам.",
            _run_pipe_size_in_name_check,
            option_keys=[
                "pipe_size_name_param",
                "pipe_size_categories",
                "pipe_gost1_keyword",
                "pipe_gost1_size_param",
                "pipe_gost2_keyword",
                "pipe_gost2_size_param"
            ]
        ),
        CheckDefinition(
            "proxy_controller",
            u"11. Прокси-позиции по контроллеру",
            u"Для заполненных позиций контроллера проверяет наличие прокси с "
            u"нужным кодом и совпадение значения (по таблице связок).",
            _run_proxy_controller_check,
            option_keys=[
                "proxy_ctrl_family",
                "proxy_ctrl_proxy_family",
                "proxy_ctrl_code_param",
                "proxy_ctrl_value_param",
                "proxy_ctrl_mapping"
            ]
        ),
        CheckDefinition(
            "forbidden_text",
            u"12. Запрещенные фразы в наименовании и марке",
            u"Ищет элементы, у которых в выбранных параметрах (по умолчанию "
            u"ADSK_Наименование и ADSK_Марка) встречается одна из запрещенных фраз.",
            _run_forbidden_text_check,
            option_keys=[
                "badtext_params",
                "badtext_phrases",
                "badtext_categories"
            ]
        ),
        CheckDefinition(
            "param_rules",
            u"13. ADSK_Система_Сокращение",
            u"Ищет элементы по условиям правил (как в параметризации) и проверяет, "
            u"что целевой параметр точно равен значению правила. Не подходящие под "
            u"условия элементы игнорируются.",
            _run_param_rules_check,
            option_keys=[
                "param_rules_target",
                "param_rules_text"
            ]
        ),
        CheckDefinition(
            "nested_heat",
            u"14. Вложенные семейства (отопление)",
            u"Ищет вложенные экземпляры (подкомпоненты), у которых "
            u"ADSK_Система_Сокращение не совпадает с родительским семейством.",
            _run_nested_heat_check,
            option_keys=[
                "nested_heat_param",
                "nested_heat_categories"
            ]
        ),
        CheckDefinition(
            "nested_vent",
            u"15. Вложенные семейства (вентиляция)",
            u"Ищет вложенные экземпляры (подкомпоненты), у которых «Имя системы» "
            u"или ADSK_Группирование не совпадают с родительским семейством.",
            _run_nested_vent_check,
            option_keys=[
                "nested_vent_param1",
                "nested_vent_param2",
                "nested_vent_categories"
            ]
        ),
    ]


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


def _collect_all_elements(doc):
    try:
        return list(
            FilteredElementCollector(doc)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except:
        return []


def _get_type_element(doc, element):
    try:
        return doc.GetElement(element.GetTypeId())
    except:
        return None


def _get_family_name(element):
    try:
        symbol = element.Symbol
        if symbol and symbol.Family:
            return unicode(symbol.Family.Name or u"").strip()
    except:
        pass

    try:
        type_element = element.Document.GetElement(element.GetTypeId())
        family_name = type_element.FamilyName
        if family_name:
            return unicode(family_name).strip()
    except:
        pass

    try:
        param = element.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
        value = _get_parameter_text_from_param(param)
        if value:
            return value
    except:
        pass

    try:
        type_element = element.Document.GetElement(element.GetTypeId())
        param = type_element.get_Parameter(BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM)
        value = _get_parameter_text_from_param(param)
        if value:
            return value
    except:
        pass

    return u""


def _get_type_name(doc, element):
    # Инстансный параметр "Тип" (ELEM_TYPE_PARAM) надежнее всего: его
    # AsValueString возвращает имя типоразмера. Прямой .Name у системных типов
    # (трубы, воздуховоды) может бросать исключение или быть пустым.
    try:
        type_param = element.get_Parameter(BuiltInParameter.ELEM_TYPE_PARAM)
        if type_param is not None:
            text = type_param.AsValueString()
            if text:
                return unicode(text).strip()
    except:
        pass

    type_element = _get_type_element(doc, element)
    if type_element is None:
        return u""

    for bip in (BuiltInParameter.ALL_MODEL_TYPE_NAME, BuiltInParameter.SYMBOL_NAME_PARAM):
        try:
            type_param = type_element.get_Parameter(bip)
            if type_param is not None:
                text = type_param.AsString()
                if text:
                    return unicode(text).strip()
        except:
            pass

    try:
        return unicode(type_element.Name or u"").strip()
    except:
        return u""


def _get_category_name(element):
    try:
        return unicode(element.Category.Name or u"").strip()
    except:
        return u"?"


def _get_element_label(doc, element):
    type_name = _get_type_name(doc, element)
    category_name = _get_category_name(element)

    if type_name:
        return u"ID {0} | {1} | {2}".format(
            element.Id.IntegerValue,
            category_name,
            type_name
        )

    return u"ID {0} | {1}".format(
        element.Id.IntegerValue,
        category_name
    )


def _get_lookup_parameter(element, names):
    if not isinstance(names, list):
        names = [names]

    for name in names:
        if not name:
            continue
        try:
            param = element.LookupParameter(name)
            if param is not None:
                return param
        except:
            pass

    return None


def _get_parameter_text_from_param(param):
    if param is None:
        return u""

    try:
        value = param.AsString()
        if value:
            return unicode(value).strip()
    except:
        pass

    try:
        value = param.AsValueString()
        if value:
            return unicode(value).strip()
    except:
        pass

    try:
        if param.StorageType == StorageType.Integer:
            return unicode(param.AsInteger()).strip()
    except:
        pass

    try:
        if param.StorageType == StorageType.Double:
            return unicode(param.AsDouble()).strip()
    except:
        pass

    return u""


def _get_parameter_text(element, names):
    return _get_parameter_text_from_param(_get_lookup_parameter(element, names))


def _get_parameter_text_from_element_or_type(doc, element, names):
    value = _get_parameter_text(element, names)
    if value:
        return value

    type_element = _get_type_element(doc, element)
    if type_element is None:
        return u""

    return _get_parameter_text(type_element, names)


def _has_nonempty_text(element, names):
    return bool(_get_parameter_text(element, names))


def _normalize_space(text):
    text = unicode(text or u"").strip()
    if not text:
        return u""
    return u" ".join(text.split())


def _parse_number_list(raw_text):
    result = []
    parts = re.split(r"[;,/\n]+", unicode(raw_text or u""))

    for part in parts:
        value = _parse_float(part)
        if value is None:
            continue
        result.append(value)

    return result


def _parse_text_list(raw_text):
    result = []
    parts = re.split(r"[;,/\n]+", unicode(raw_text or u""))

    for part in parts:
        token = _normalize_space(part).lower()
        if token:
            result.append(token)

    return result


def _normalize_search_text(text):
    text = _normalize_space(text).lower()
    if not text:
        return u""
    return re.sub(r"\s+([,.;:])", r"\1", text)


def _parse_search_text_list(raw_text):
    result = []
    seen = set()
    parts = re.split(r"[;\n]+", unicode(raw_text or u""))

    for part in parts:
        display_text = _normalize_space(part)
        search_text = _normalize_search_text(display_text)

        if not search_text or search_text in seen:
            continue

        seen.add(search_text)
        result.append((display_text, search_text))

    return result


def _upgrade_bzero_search_text(raw_text):
    normalized = _normalize_search_text(raw_text)

    if normalized in [u"b=0", u"b=0 мм"]:
        return DEFAULT_SPEC_CONFIG["bzero_text"]

    return raw_text


def _parse_category_keys(raw_text):
    result = []
    seen = set()
    parts = re.split(r"[;,/\n]+", unicode(raw_text or u""))

    for part in parts:
        token = _normalize_space(part)
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)

    return result


def _parse_float(raw_text):
    text = unicode(raw_text or u"").strip()
    if not text:
        return None

    text = text.replace(u",", u".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None

    try:
        return float(match.group(0))
    except:
        return None


def _get_length_param_mm(element, param_names):
    param = _get_lookup_parameter(element, param_names)

    if param is None:
        return None

    try:
        if param.StorageType == StorageType.Double:
            return float(param.AsDouble()) * MM_PER_FOOT
    except:
        pass

    return _parse_float(_get_parameter_text_from_param(param))


def _get_number_param(element, param_names):
    """Числовое значение параметра без пересчета единиц (для счетных полей)."""
    param = _get_lookup_parameter(element, param_names)

    if param is None:
        return None

    try:
        if param.StorageType == StorageType.Double:
            return float(param.AsDouble())
    except:
        pass

    try:
        if param.StorageType == StorageType.Integer:
            return float(param.AsInteger())
    except:
        pass

    return _parse_float(_get_parameter_text_from_param(param))


def _get_length_param_mm_from_element_or_type(doc, element, param_names):
    value = _get_length_param_mm(element, param_names)
    if value is not None:
        return value

    type_element = _get_type_element(doc, element)
    if type_element is None:
        return None

    return _get_length_param_mm(type_element, param_names)


def _get_pipe_diameter_mm(element):
    built_in_params = [
        BuiltInParameter.RBS_PIPE_DIAMETER_PARAM,
        BuiltInParameter.RBS_CURVE_DIAMETER_PARAM,
    ]

    for built_in_param in built_in_params:
        try:
            param = element.get_Parameter(built_in_param)
            if param is not None and param.StorageType == StorageType.Double:
                return float(param.AsDouble()) * MM_PER_FOOT
        except:
            pass

    return _get_length_param_mm(element, [u"Диаметр"])


def _is_auto_source(source_name):
    source_key = _normalize_space(source_name).lower()
    return source_key in [u"", u"авто", u"auto", u"все", u"любой"]


def _get_element_name(element):
    try:
        return unicode(element.Name or u"").strip()
    except:
        return u""


def _get_named_source_text(doc, element, source_name):
    source_key = _normalize_space(source_name).lower()

    if _is_auto_source(source_key):
        return _gather_text_blob(doc, element)

    if source_key in [u"имя семейства", u"семейство", u"family", u"family name"]:
        return _get_family_name(element)

    if source_key in [
        u"имя типа",
        u"имя типоразмера",
        u"тип",
        u"типоразмер",
        u"type",
        u"type name"
    ]:
        return _get_type_name(doc, element)

    if source_key in [u"имя элемента", u"имя", u"name", u"element name"]:
        return _get_element_name(element)

    if source_key in [u"категория", u"category"]:
        return _get_category_name(element)

    return _get_parameter_text_from_element_or_type(doc, element, [source_name])


def _get_configured_pipe_size_mm(doc, element, size_source):
    source_key = _normalize_space(size_source).lower()

    if _is_auto_source(source_key) or source_key in [u"диаметр", u"diameter"]:
        return _get_pipe_diameter_mm(element)

    return _get_length_param_mm_from_element_or_type(doc, element, [size_source])


def _is_zero_number(value):
    if value is None:
        return False
    return abs(float(value)) < 0.0001


def _gather_text_blob(doc, element):
    values = []

    try:
        values.append(unicode(element.Name or u""))
    except:
        pass

    values.append(_get_family_name(element))
    values.append(_get_type_name(doc, element))

    for param_name in [u"Материал", u"Описание", u"Комментарии к типоразмеру", u"Сегмент трубы"]:
        values.append(_get_parameter_text(element, param_name))

    type_element = _get_type_element(doc, element)
    if type_element is not None:
        for param_name in [u"Материал", u"Описание", u"Комментарии к типоразмеру", u"Сегмент трубы"]:
            values.append(_get_parameter_text(type_element, param_name))

    blob = u" ".join([value for value in values if value])
    return blob.lower()


def _is_copper_pipe(doc, element, keywords, keyword_source):
    if not keywords:
        return False
    blob = _get_named_source_text(doc, element, keyword_source)
    if not blob:
        return False

    blob = blob.lower()

    for keyword in keywords:
        if keyword and keyword in blob:
            return True

    return False


def _get_system_names(element):
    result = []

    direct_texts = [
        _get_parameter_text(element, u"Имя системы"),
        _get_parameter_text(element, u"Система"),
    ]

    for text in direct_texts:
        if text:
            result.append(text)

    try:
        param = element.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
        text = _get_parameter_text_from_param(param)
        if text:
            result.append(text)
    except:
        pass

    try:
        mep_system = element.MEPSystem
        if mep_system is not None and mep_system.Name:
            result.append(unicode(mep_system.Name))
    except:
        pass

    try:
        connector_manager = element.ConnectorManager
    except:
        connector_manager = None

    if connector_manager is not None:
        try:
            connectors = connector_manager.Connectors
        except:
            connectors = []

        try:
            for connector in connectors:
                try:
                    mep_system = connector.MEPSystem
                    if mep_system is not None and mep_system.Name:
                        result.append(unicode(mep_system.Name))
                except:
                    pass
        except:
            pass

    normalized = []
    seen = set()

    for raw_text in result:
        if not raw_text:
            continue
        for token in re.split(r"[;,/]+", unicode(raw_text)):
            cleaned = _normalize_space(token)
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(cleaned)

    return normalized


def _make_simple_result(key, title, elements, param_names, message_template):
    issues = []
    checked_count = len(elements)

    for element in elements:
        if _has_nonempty_text(element, param_names):
            continue
        issues.append(CheckIssue(
            message_template.format(_get_element_label(element.Document, element)),
            [element.Id.IntegerValue]
        ))

    return CheckResult(key, title, checked_count, issues)


def _run_name_presence_check(doc, config, cache):
    elements = cache.get_by_categories(PIPE_AND_DUCT_CATEGORIES)
    param_name = config.get("name_presence_param", DEFAULT_SPEC_CONFIG["name_presence_param"])

    return _make_simple_result(
        "name_presence",
        u"1. Наименования у труб и воздуховодов",
        elements,
        [param_name],
        u"{0} | параметр наименования пустой"
    )


def _run_quantity_presence_check(doc, config, cache):
    elements = cache.get_by_categories(PIPE_AND_DUCT_CATEGORIES)
    param_name = config.get("quantity_param", DEFAULT_SPEC_CONFIG["quantity_param"])
    param_names = [param_name, u"ADSK_Количество"]
    issues = []

    for element in elements:
        text_value = _get_parameter_text(element, param_names)

        if not text_value:
            issues.append(CheckIssue(
                u"{0} | параметр количества пустой".format(_get_element_label(doc, element)),
                [element.Id.IntegerValue]
            ))
            continue

        numeric_value = _get_number_param(element, param_names)

        if _is_zero_number(numeric_value):
            issues.append(CheckIssue(
                u"{0} | количество равно 0".format(_get_element_label(doc, element)),
                [element.Id.IntegerValue]
            ))

    return CheckResult(
        "quantity_presence",
        u"2. Пустое или нулевое ADSK_количество",
        len(elements),
        issues
    )


def _run_duct_bzero_check(doc, config, cache):
    category_keys = _parse_category_keys(
        config.get("bzero_categories", DEFAULT_SPEC_CONFIG["bzero_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult(
            "duct_bzero",
            u"3. Запрещенные тексты в наименовании",
            0,
            []
        )

    elements = cache.get_by_categories(categories)
    param_name = config.get("bzero_name_param", DEFAULT_SPEC_CONFIG["bzero_name_param"])
    raw_search_text = _upgrade_bzero_search_text(
        config.get("bzero_text", DEFAULT_SPEC_CONFIG["bzero_text"])
    )
    search_texts = _parse_search_text_list(raw_search_text)
    issues = []

    for element in elements:
        value = _get_parameter_text(element, [param_name])
        if not value:
            continue

        normalized_value = _normalize_search_text(value)

        for display_text, search_text in search_texts:
            if not search_text or search_text not in normalized_value:
                continue

            issues.append(CheckIssue(
                u"{0} | найден текст '{1}'".format(
                    _get_element_label(doc, element),
                    display_text
                ),
                [element.Id.IntegerValue]
            ))
            break

    return CheckResult(
        "duct_bzero",
        u"3. Запрещенные тексты в наименовании",
        len(elements),
        issues
    )


def _run_duct_metal_thickness_check(doc, config, cache):
    elements = cache.get_by_categories(DUCT_ONLY_CATEGORIES)
    param_name = config.get("metal_thickness_param", DEFAULT_SPEC_CONFIG["metal_thickness_param"])
    issues = []

    for element in elements:
        text_value = _get_parameter_text(element, [param_name])
        numeric_value = _get_length_param_mm(element, [param_name])

        if not text_value:
            issues.append(CheckIssue(
                u"{0} | толщина металла не заполнена".format(_get_element_label(doc, element)),
                [element.Id.IntegerValue]
            ))
            continue

        if _is_zero_number(numeric_value):
            issues.append(CheckIssue(
                u"{0} | толщина металла равна 0".format(_get_element_label(doc, element)),
                [element.Id.IntegerValue]
            ))

    return CheckResult(
        "duct_metal_thickness",
        u"4. Пустая или нулевая толщина металла",
        len(elements),
        issues
    )


def _run_copper_pipe_sizes_check(doc, config, cache):
    elements = cache.get_by_categories([BuiltInCategory.OST_PipeCurves])
    keyword_source = config.get(
        "copper_keyword_source",
        DEFAULT_SPEC_CONFIG["copper_keyword_source"]
    )
    size_param = config.get(
        "copper_size_param",
        DEFAULT_SPEC_CONFIG["copper_size_param"]
    )
    standard_sizes = _parse_number_list(
        config.get("copper_standard_sizes_mm", DEFAULT_SPEC_CONFIG["copper_standard_sizes_mm"])
    )
    keywords = _parse_text_list(
        config.get("copper_keywords", DEFAULT_SPEC_CONFIG["copper_keywords"])
    )
    issues = []
    checked_count = 0

    for element in elements:
        if not _is_copper_pipe(doc, element, keywords, keyword_source):
            continue

        checked_count += 1
        size_mm = _get_configured_pipe_size_mm(doc, element, size_param)

        if size_mm is None:
            issues.append(CheckIssue(
                u"{0} | не удалось определить размер по полю '{1}'".format(
                    _get_element_label(doc, element),
                    size_param
                ),
                [element.Id.IntegerValue]
            ))
            continue

        matches_standard = False

        for standard_size in standard_sizes:
            if abs(size_mm - standard_size) <= 0.5:
                matches_standard = True
                break

        if not matches_standard:
            issues.append(CheckIssue(
                u"{0} | нестандартный размер {1:.1f} мм по полю '{2}'".format(
                    _get_element_label(doc, element),
                    size_mm,
                    size_param
                ),
                [element.Id.IntegerValue]
            ))

    return CheckResult(
        "copper_pipe_sizes",
        u"5. Нестандартные размеры медной трубы",
        checked_count,
        issues
    )


def _run_system_grouping_check(doc, config, cache):
    elements = cache.get_by_categories(SYSTEM_GROUP_CATEGORIES)
    grouping_param = config.get("grouping_param", DEFAULT_SPEC_CONFIG["grouping_param"])
    issues = []

    for element in elements:
        grouping_value = _normalize_space(_get_parameter_text(element, [grouping_param]))
        system_names = _get_system_names(element)

        if not system_names and not grouping_value:
            continue

        if not system_names:
            issues.append(CheckIssue(
                u"{0} | имя системы пустое, ADSK_Группирование = '{1}'".format(
                    _get_element_label(doc, element),
                    grouping_value
                ),
                [element.Id.IntegerValue]
            ))
            continue

        if not grouping_value:
            issues.append(CheckIssue(
                u"{0} | ADSK_Группирование пустое, имя системы = '{1}'".format(
                    _get_element_label(doc, element),
                    u", ".join(system_names)
                ),
                [element.Id.IntegerValue]
            ))
            continue

        grouping_key = grouping_value.lower()
        system_keys = [system_name.lower() for system_name in system_names]

        if grouping_key not in system_keys:
            issues.append(CheckIssue(
                u"{0} | имя системы: '{1}' | ADSK_Группирование: '{2}'".format(
                    _get_element_label(doc, element),
                    u", ".join(system_names),
                    grouping_value
                ),
                [element.Id.IntegerValue]
            ))

    return CheckResult(
        "system_grouping",
        u"6. Имя системы и ADSK_Группирование",
        len(elements),
        issues
    )


def _run_sort_order_check(doc, config, cache):
    param_name = config.get("sort_order_param", DEFAULT_SPEC_CONFIG["sort_order_param"])
    category_keys = _parse_category_keys(
        config.get("sort_order_categories", DEFAULT_SPEC_CONFIG["sort_order_categories"])
    )

    categories = []
    for category_key in category_keys:
        built_in = SORT_ORDER_CATEGORY_MAP.get(category_key)
        if built_in is not None:
            categories.append(built_in)

    if not categories:
        return CheckResult(
            "sort_order",
            u"7. Заполнение PP_Порядок сортировки",
            0,
            []
        )

    elements = cache.get_by_categories(categories)

    return _make_simple_result(
        "sort_order",
        u"7. Заполнение PP_Порядок сортировки",
        elements,
        [param_name],
        u"{0} | PP_Порядок сортировки пустой"
    )


def _get_connectors(element):
    connectors = []

    try:
        manager = element.ConnectorManager
        if manager is not None:
            for connector in manager.Connectors:
                connectors.append(connector)
    except:
        # У экземпляров (оборудование/фитинги) нет ConnectorManager напрямую.
        pass

    try:
        mep_model = element.MEPModel
        if mep_model is not None and mep_model.ConnectorManager is not None:
            for connector in mep_model.ConnectorManager.Connectors:
                connectors.append(connector)
    except:
        # У труб/воздуховодов нет MEPModel.
        pass

    return connectors


def _element_category_id(element):
    try:
        category = element.Category
        if category is not None:
            return category.Id.IntegerValue
    except:
        pass

    return None


def _get_connected_elements(element, allowed_category_ids):
    result = []
    seen = set()

    try:
        self_id = element.Id.IntegerValue
    except:
        return result

    for connector in _get_connectors(element):
        try:
            refs = connector.AllRefs
        except:
            refs = []

        for ref_connector in refs:
            try:
                owner = ref_connector.Owner
            except:
                owner = None

            if owner is None:
                continue

            try:
                owner_id = owner.Id.IntegerValue
            except:
                continue

            if owner_id == self_id or owner_id in seen:
                continue

            if _element_category_id(owner) not in allowed_category_ids:
                continue

            seen.add(owner_id)
            result.append(owner)

    return result


def _resolve_categories(category_keys):
    categories = []

    for category_key in category_keys:
        built_in = SORT_ORDER_CATEGORY_MAP.get(category_key)
        if built_in is not None:
            categories.append(built_in)

    return categories


def _run_mep_name_match_check(doc, config, cache):
    title = u"8. Совпадение имени по MEP-соединениям"

    source_keys = _parse_category_keys(
        config.get("mep_source_categories", DEFAULT_SPEC_CONFIG["mep_source_categories"])
    )
    receiver_keys = _parse_category_keys(
        config.get("mep_receiver_categories", DEFAULT_SPEC_CONFIG["mep_receiver_categories"])
    )
    source_param = config.get("mep_source_param", DEFAULT_SPEC_CONFIG["mep_source_param"])
    receiver_param = config.get("mep_receiver_param", DEFAULT_SPEC_CONFIG["mep_receiver_param"])

    source_categories = _resolve_categories(source_keys)
    receiver_categories = _resolve_categories(receiver_keys)

    if not source_categories or not receiver_categories:
        return CheckResult("mep_name_match", title, 0, [])

    filter_config = pp_mep_filter.normalize_filter_config(config.get("mep_filter"))
    apply_source_filter = pp_mep_filter.filter_targets_side(filter_config, "source")
    apply_receiver_filter = pp_mep_filter.filter_targets_side(filter_config, "receiver")

    allowed_source_ids = set()
    for built_in in source_categories:
        try:
            allowed_source_ids.add(int(built_in))
        except:
            pass

    receivers = cache.get_by_categories(receiver_categories)

    if apply_receiver_filter:
        receivers = [
            receiver for receiver in receivers
            if pp_mep_filter.element_passes_filter(doc, receiver, filter_config)
        ]

    issues = []
    checked_count = 0

    for receiver in receivers:
        connected_sources = _get_connected_elements(receiver, allowed_source_ids)

        if apply_source_filter:
            connected_sources = [
                source for source in connected_sources
                if pp_mep_filter.element_passes_filter(doc, source, filter_config)
            ]

        if not connected_sources:
            continue

        source_values = []
        for source in connected_sources:
            value = _normalize_space(
                _get_parameter_text_from_element_or_type(doc, source, [source_param])
            )
            if value and value not in source_values:
                source_values.append(value)

        if not source_values:
            # Подключенные источники есть, но параметр у них пустой -
            # сверять нечего, это задача других проверок.
            continue

        checked_count += 1
        receiver_value = _normalize_space(_get_parameter_text(receiver, [receiver_param]))
        source_text = u", ".join(sorted(source_values))

        if not receiver_value:
            issues.append(CheckIssue(
                u"{0} | значение потеряно, у источника: {1}".format(
                    _get_element_label(doc, receiver),
                    source_text
                ),
                [receiver.Id.IntegerValue]
            ))
            continue

        if receiver_value not in source_values:
            issues.append(CheckIssue(
                u"{0} | приемник '{1}' не совпадает с источником: {2}".format(
                    _get_element_label(doc, receiver),
                    receiver_value,
                    source_text
                ),
                [receiver.Id.IntegerValue]
            ))

    return CheckResult("mep_name_match", title, checked_count, issues)


def _get_size_text(element, names):
    value = _get_parameter_text(element, names)
    if value:
        return value

    # Для воздуховодов/труб «Размер» — это встроенный RBS_CALCULATED_SIZE.
    try:
        param = element.get_Parameter(BuiltInParameter.RBS_CALCULATED_SIZE)
        value = _get_parameter_text_from_param(param)
        if value:
            return value
    except:
        pass

    return u""


def _normalize_size(text):
    text = unicode(text or u"").lower()

    # Разделители размеров к единому виду: 400х200 / 400×200 / 400*200 -> 400x200
    for separator in [u"×", u"х", u"*"]:
        text = text.replace(separator, u"x")

    # Обозначения диаметра убираем, чтобы ⌀400 и 400 сравнивались одинаково.
    for diameter in [u"⌀", u"ø", u"Ø", u"∅"]:
        text = text.replace(diameter, u"")

    # Единицы убираем.
    text = text.replace(u"мм", u"").replace(u"mm", u"")

    # Десятичный разделитель к точке и хвостовые нули у дробей убираем,
    # чтобы 32,00 / 32.0 сравнивались как 32, а 3,50 как 3.5.
    text = text.replace(u",", u".")
    text = re.sub(
        r"\d+\.\d+",
        lambda match: match.group(0).rstrip(u"0").rstrip(u"."),
        text
    )

    # Пробелы убираем.
    text = u"".join(text.split())

    return text


def _size_end_in_name(end, name_norm):
    # Одно сечение: прямоугольное AxB совпадает и как BxA (порядок сторон
    # не важен), круглое — как есть.
    if not end:
        return True

    if end in name_norm:
        return True

    parts = end.split(u"x")
    if len(parts) == 2 and parts[0] and parts[1]:
        swapped = u"{0}x{1}".format(parts[1], parts[0])
        if swapped in name_norm:
            return True

    return False


def _size_matches_name(size_norm, name_norm):
    if not size_norm:
        return True

    # Весь размер целиком найден в имени.
    if size_norm in name_norm:
        return True

    # Размер может состоять из нескольких сечений (переходы у фитингов),
    # разделенных "-". Порядок сечений в имени может отличаться, а у
    # прямоугольного сечения стороны могут быть переставлены. Считаем
    # совпадением, если каждое сечение размера присутствует в имени.
    ends = [end for end in size_norm.split(u"-") if end]
    if not ends:
        return False

    for end in ends:
        if not _size_end_in_name(end, name_norm):
            return False

    return True


def _run_size_in_name_check(doc, config, cache):
    title = u"9. Размер в ADSK_Наименование на ВЕНТИЛЯЦИИ"

    size_param = config.get("size_name_size_param", DEFAULT_SPEC_CONFIG["size_name_size_param"])
    name_param = config.get("size_name_name_param", DEFAULT_SPEC_CONFIG["size_name_name_param"])
    category_keys = _parse_category_keys(
        config.get("size_name_categories", DEFAULT_SPEC_CONFIG["size_name_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult("size_in_name", title, 0, [])

    elements = cache.get_by_categories(categories)
    issues = []
    checked_count = 0

    for element in elements:
        size_text = _get_size_text(element, [size_param])

        # Нет размера — сравнивать нечего (пустые размеры ловят другие проверки).
        if not size_text:
            continue

        checked_count += 1
        name_text = _get_parameter_text(element, [name_param])

        if not name_text:
            issues.append(CheckIssue(
                u"{0} | наименование пустое, размер '{1}'".format(
                    _get_element_label(doc, element),
                    size_text
                ),
                [element.Id.IntegerValue]
            ))
            continue

        if not _size_matches_name(_normalize_size(size_text), _normalize_size(name_text)):
            issues.append(CheckIssue(
                u"{0} | размер '{1}' не найден в наименовании: '{2}'".format(
                    _get_element_label(doc, element),
                    size_text,
                    name_text
                ),
                [element.Id.IntegerValue]
            ))

    return CheckResult("size_in_name", title, checked_count, issues)


def _run_pipe_size_in_name_check(doc, config, cache):
    title = u"10. Размер в ADSK_Наименование на ОТОПЛЕНИЕ"

    name_param = config.get("pipe_size_name_param", DEFAULT_SPEC_CONFIG["pipe_size_name_param"])
    category_keys = _parse_category_keys(
        config.get("pipe_size_categories", DEFAULT_SPEC_CONFIG["pipe_size_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult("pipe_size_in_name", title, 0, [])

    # Правила «текст в имени типа -> параметр размера». Тип трубы определяется
    # по вхождению текста в имя типа. Два ГОСТа — два разных параметра.
    rules = []
    for keyword_key, size_key in [
        ("pipe_gost1_keyword", "pipe_gost1_size_param"),
        ("pipe_gost2_keyword", "pipe_gost2_size_param"),
    ]:
        keyword = _normalize_space(config.get(keyword_key, DEFAULT_SPEC_CONFIG[keyword_key])).lower()
        size_param = config.get(size_key, DEFAULT_SPEC_CONFIG[size_key])
        if keyword:
            rules.append((keyword, size_param))

    elements = cache.get_by_categories(categories)
    issues = []
    checked_count = 0

    for element in elements:
        type_name = _get_type_name(doc, element).lower()

        matched = None
        for keyword, size_param in rules:
            if keyword in type_name:
                matched = (keyword, size_param)
                break

        # Тип трубы не распознан ни одним правилом — пропускаем.
        if matched is None:
            continue

        keyword, size_param = matched
        size_text = _get_size_text(element, [size_param])

        if not size_text:
            continue

        checked_count += 1
        name_text = _get_parameter_text(element, [name_param])

        if not name_text:
            issues.append(CheckIssue(
                u"{0} | тип '{1}' | наименование пустое, размер '{2}'".format(
                    _get_element_label(doc, element),
                    keyword,
                    size_text
                ),
                [element.Id.IntegerValue]
            ))
            continue

        if not _size_matches_name(_normalize_size(size_text), _normalize_size(name_text)):
            issues.append(CheckIssue(
                u"{0} | тип '{1}' | размер '{2}' не найден в наименовании: '{3}'".format(
                    _get_element_label(doc, element),
                    keyword,
                    size_text,
                    name_text
                ),
                [element.Id.IntegerValue]
            ))

    return CheckResult("pipe_size_in_name", title, checked_count, issues)


def _parse_mapping_lines(raw_text):
    # Каждая строка: "Параметр контроллера = КОД".
    result = []
    for line in re.split(r"[\r\n]+", unicode(raw_text or u"")):
        if u"=" not in line:
            continue
        left, right = line.split(u"=", 1)
        left = _normalize_space(left)
        right = _normalize_space(right)
        if left and right:
            result.append((left, right))
    return result


def _values_match(ctrl_num, ctrl_text, proxy_num, proxy_text):
    if ctrl_num is not None and proxy_num is not None:
        if abs(ctrl_num - proxy_num) <= 0.01:
            return True

    if ctrl_text and _normalize_space(ctrl_text).lower() == _normalize_space(proxy_text).lower():
        return True

    return False


def _run_proxy_controller_check(doc, config, cache):
    title = u"11. Прокси-позиции по контроллеру"

    controller_family = _normalize_space(
        config.get("proxy_ctrl_family", DEFAULT_SPEC_CONFIG["proxy_ctrl_family"])
    )
    proxy_family = _normalize_space(
        config.get("proxy_ctrl_proxy_family", DEFAULT_SPEC_CONFIG["proxy_ctrl_proxy_family"])
    )
    code_param = config.get("proxy_ctrl_code_param", DEFAULT_SPEC_CONFIG["proxy_ctrl_code_param"])
    value_param = config.get("proxy_ctrl_value_param", DEFAULT_SPEC_CONFIG["proxy_ctrl_value_param"])
    mapping = _parse_mapping_lines(
        config.get("proxy_ctrl_mapping", DEFAULT_SPEC_CONFIG["proxy_ctrl_mapping"])
    )

    all_elements = cache.get_all()

    controllers = []
    proxies = []

    for element in all_elements:
        family_name = _normalize_space(_get_family_name(element))
        if controller_family and family_name == controller_family:
            controllers.append(element)
        elif proxy_family and family_name == proxy_family:
            proxies.append(element)

    issues = []

    if not controllers:
        issues.append(CheckIssue(
            u"Контроллер '{0}' не найден в проекте.".format(controller_family),
            []
        ))
        return CheckResult("proxy_controller", title, 0, issues)

    controller = controllers[0]

    if len(controllers) > 1:
        issues.append(CheckIssue(
            u"Найдено несколько контроллеров '{0}' ({1}). Проверяю первый.".format(
                controller_family,
                len(controllers)
            ),
            [element.Id.IntegerValue for element in controllers]
        ))

    # Группируем прокси по коду правила.
    proxies_by_code = {}
    for proxy in proxies:
        code = _normalize_space(_get_parameter_text(proxy, [code_param]))
        if not code:
            continue
        proxies_by_code.setdefault(code.lower(), []).append(proxy)

    checked_count = 0

    for controller_param, code in mapping:
        ctrl_num = _get_number_param(controller, [controller_param])
        ctrl_text = _get_parameter_text(controller, [controller_param])

        # Позиция контроллера заполнена? (для чисел — не ноль)
        if ctrl_num is not None:
            if _is_zero_number(ctrl_num):
                continue
        elif not ctrl_text:
            continue

        checked_count += 1

        display_value = ctrl_text
        if not display_value and ctrl_num is not None:
            display_value = unicode(ctrl_num)

        code_proxies = proxies_by_code.get(code.lower(), [])

        if not code_proxies:
            issues.append(CheckIssue(
                u"Нет прокси-позиции с кодом '{0}' | контроллер '{1}' = {2}".format(
                    code,
                    controller_param,
                    display_value
                ),
                [controller.Id.IntegerValue]
            ))
            continue

        matched = False
        proxy_values = []

        for proxy in code_proxies:
            proxy_num = _get_number_param(proxy, [value_param])
            proxy_text = _get_parameter_text(proxy, [value_param])
            proxy_values.append(proxy_text if proxy_text else u"")
            if _values_match(ctrl_num, ctrl_text, proxy_num, proxy_text):
                matched = True
                break

        if not matched:
            issues.append(CheckIssue(
                u"Код '{0}': значение не совпадает | контроллер '{1}' = {2} | прокси {3} = {4}".format(
                    code,
                    controller_param,
                    display_value,
                    value_param,
                    u", ".join(proxy_values)
                ),
                [proxy.Id.IntegerValue for proxy in code_proxies]
            ))

    return CheckResult("proxy_controller", title, checked_count, issues)


def _parse_lines(raw_text):
    result = []
    for line in re.split(r"[\r\n]+", unicode(raw_text or u"")):
        line = _normalize_space(line)
        if line:
            result.append(line)
    return result


def _run_forbidden_text_check(doc, config, cache):
    title = u"12. Запрещенные фразы в наименовании и марке"

    param_names = _parse_category_keys(
        config.get("badtext_params", DEFAULT_SPEC_CONFIG["badtext_params"])
    )
    phrases = _parse_lines(
        config.get("badtext_phrases", DEFAULT_SPEC_CONFIG["badtext_phrases"])
    )
    category_keys = _parse_category_keys(
        config.get("badtext_categories", DEFAULT_SPEC_CONFIG["badtext_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories or not param_names or not phrases:
        return CheckResult("forbidden_text", title, 0, [])

    phrase_pairs = [(phrase, phrase.lower()) for phrase in phrases]
    elements = cache.get_by_categories(categories)
    issues = []

    for element in elements:
        for param_name in param_names:
            value = _get_parameter_text(element, [param_name])
            if not value:
                continue

            value_lower = value.lower()
            found = None
            for original, lowered in phrase_pairs:
                if lowered and lowered in value_lower:
                    found = original
                    break

            if found is not None:
                issues.append(CheckIssue(
                    u"{0} | {1}: найдена фраза '{2}' | значение '{3}'".format(
                        _get_element_label(doc, element),
                        param_name,
                        found,
                        value
                    ),
                    [element.Id.IntegerValue]
                ))

    return CheckResult("forbidden_text", title, len(elements), issues)


# Операторы условий в правилах (порядок важен: многосимвольные сначала).
# Значения ключей — как в pp_mep_filter (FILTER_OP_KEYS).
_PARAM_RULE_OPERATORS = [
    (u"!~", "not_contains"),
    (u"^=", "starts_with"),
    (u"!=", "ne"),
    (u"~", "contains"),
    (u"=", "eq"),
]


def _parse_one_condition(text):
    text = _normalize_space(text)
    if not text:
        return None

    for token, op in _PARAM_RULE_OPERATORS:
        index = text.find(token)
        if index >= 0:
            param = _normalize_space(text[:index])
            value = _normalize_space(text[index + len(token):])
            if param:
                return {"param": param, "op": op, "value": value, "group": 1}

    return None


def _resolve_category_tokens(text):
    # Категории можно писать ключами OST_... или русскими названиями.
    lookup = {}
    for key, label, _built_in in SORT_ORDER_CATEGORY_OPTIONS:
        lookup[key.lower()] = key
        lookup[_normalize_space(label).lower()] = key

    result = []
    for token in re.split(r"[;,/\n]+", unicode(text or u"")):
        cleaned = _normalize_space(token).lower()
        if not cleaned:
            continue
        key = lookup.get(cleaned)
        if key and key not in result:
            result.append(key)

    return result


def _parse_param_rules(raw_text):
    rules = []

    for line in re.split(r"[\r\n]+", unicode(raw_text or u"")):
        stripped = line.strip()
        if not stripped or stripped.startswith(u"#"):
            continue

        parts = stripped.split(u"|")
        if len(parts) < 4:
            continue

        value = _normalize_space(parts[0])
        category_keys = _resolve_category_tokens(parts[1])
        match = _normalize_space(parts[2]).lower()
        match_key = "any" if match in (u"любое", u"any", u"или", u"or") else "all"

        conditions = []
        for chunk in re.split(r"[;]+", parts[3]):
            condition = _parse_one_condition(chunk)
            if condition is not None:
                conditions.append(condition)

        if not value or not category_keys or not conditions:
            continue

        rules.append({
            "value": value,
            "category_keys": category_keys,
            "match": match_key,
            "conditions": conditions,
        })

    return rules


def _run_param_rules_check(doc, config, cache):
    title = u"13. ADSK_Система_Сокращение"

    target_param = config.get("param_rules_target", DEFAULT_SPEC_CONFIG["param_rules_target"])
    rules = _parse_param_rules(
        config.get("param_rules_text", DEFAULT_SPEC_CONFIG["param_rules_text"])
    )

    if not rules:
        return CheckResult("param_rules", title, 0, [])

    issues = []
    checked_count = 0

    for rule in rules:
        categories = _resolve_categories(rule["category_keys"])
        if not categories:
            continue

        filter_config = {
            "enabled": True,
            "match": rule["match"],
            "group_combine": "and",
            "conditions": rule["conditions"],
        }

        expected = _normalize_space(rule["value"])
        elements = cache.get_by_categories(categories)

        for element in elements:
            if not pp_mep_filter.element_passes_filter(doc, element, filter_config):
                continue

            checked_count += 1
            actual = _normalize_space(_get_parameter_text(element, [target_param]))

            if actual != expected:
                shown = actual if actual else u"(пусто)"
                issues.append(CheckIssue(
                    u"{0} | {1}: '{2}' (ожидалось '{3}')".format(
                        _get_element_label(doc, element),
                        target_param,
                        shown,
                        expected
                    ),
                    [element.Id.IntegerValue]
                ))

    return CheckResult("param_rules", title, checked_count, issues)


def _get_super_component(element):
    # Вложенный экземпляр (подкомпонент) имеет родителя SuperComponent.
    try:
        return element.SuperComponent
    except:
        return None


def _show_value(text):
    return text if text else u"(пусто)"


def _run_nested_heat_check(doc, config, cache):
    title = u"14. Вложенные семейства (отопление)"

    param = config.get("nested_heat_param", DEFAULT_SPEC_CONFIG["nested_heat_param"])
    category_keys = _parse_category_keys(
        config.get("nested_heat_categories", DEFAULT_SPEC_CONFIG["nested_heat_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult("nested_heat", title, 0, [])

    elements = cache.get_by_categories(categories)
    issues = []
    checked_count = 0

    for element in elements:
        parent = _get_super_component(element)
        if parent is None:
            continue

        checked_count += 1
        child_value = _normalize_space(_get_parameter_text(element, [param]))
        parent_value = _normalize_space(_get_parameter_text(parent, [param]))

        if child_value != parent_value:
            issues.append(CheckIssue(
                u"{0} | {1}: вложенное '{2}' ≠ родитель '{3}'".format(
                    _get_element_label(doc, element),
                    param,
                    _show_value(child_value),
                    _show_value(parent_value)
                ),
                [element.Id.IntegerValue, parent.Id.IntegerValue]
            ))

    return CheckResult("nested_heat", title, checked_count, issues)


def _run_nested_vent_check(doc, config, cache):
    title = u"15. Вложенные семейства (вентиляция)"

    param1 = config.get("nested_vent_param1", DEFAULT_SPEC_CONFIG["nested_vent_param1"])
    param2 = config.get("nested_vent_param2", DEFAULT_SPEC_CONFIG["nested_vent_param2"])
    category_keys = _parse_category_keys(
        config.get("nested_vent_categories", DEFAULT_SPEC_CONFIG["nested_vent_categories"])
    )
    categories = _resolve_categories(category_keys)

    if not categories:
        return CheckResult("nested_vent", title, 0, [])

    elements = cache.get_by_categories(categories)
    issues = []
    checked_count = 0

    for element in elements:
        parent = _get_super_component(element)
        if parent is None:
            continue

        checked_count += 1
        mismatches = []

        for param in (param1, param2):
            child_value = _normalize_space(_get_parameter_text(element, [param]))
            parent_value = _normalize_space(_get_parameter_text(parent, [param]))
            if child_value != parent_value:
                mismatches.append(u"{0}: вложенное '{1}' ≠ родитель '{2}'".format(
                    param,
                    _show_value(child_value),
                    _show_value(parent_value)
                ))

        if mismatches:
            issues.append(CheckIssue(
                u"{0} | {1}".format(
                    _get_element_label(doc, element),
                    u" | ".join(mismatches)
                ),
                [element.Id.IntegerValue, parent.Id.IntegerValue]
            ))

    return CheckResult("nested_vent", title, checked_count, issues)
