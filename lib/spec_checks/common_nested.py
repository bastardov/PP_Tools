# -*- coding: utf-8 -*-
"""Хелперы «вложенные семейства» — общие для проверок
«Вложенные семейства (отопление)» и «(вентиляция)»
(check_nested_heat.py, check_nested_vent.py).
"""




def _get_super_component(element):
    # Вложенный экземпляр (подкомпонент) имеет родителя SuperComponent.
    try:
        return element.SuperComponent
    except:
        return None


def _show_value(text):
    return text if text else u"(пусто)"
