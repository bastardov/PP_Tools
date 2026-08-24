# -*- coding: utf-8 -*-
# --- учёт использования (см. lib/pp_usage.py) ---
try: import pp_usage; pp_usage.log(__file__)
except Exception: pass

import os
import sys

# Модуль окна лежит рядом со скриптом
_HERE = os.path.dirname(os.path.abspath(__file__))

if _HERE not in sys.path:
    sys.path.append(_HERE)

import pp_wpf
import pp_settings_window


TITLE = u"PP Tools — настройки"


try:
    result = pp_settings_window.show(_HERE)

    if result == u"imported":
        pp_wpf.show_report(
            u"Текущие значения заменены содержимым файла.\n\n"
            u"Окно настроек закрыто без сохранения, чтобы форма не перезаписала "
            u"импортированные данные. Откройте настройки заново, чтобы увидеть изменения.",
            title=u"Настройки импортированы",
            subtitle=TITLE
        )

except Exception as ex:
    pp_wpf.show_report(
        unicode(ex),
        title=u"Ошибка",
        subtitle=TITLE,
        is_error=True
    )
