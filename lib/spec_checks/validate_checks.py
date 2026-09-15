# -*- coding: utf-8 -*-
"""Самопроверка пакета spec_checks («Проверка спецификации») — запускать ОБЫЧНЫМ Python 3, Revit не нужен.

    python lib/spec_checks/validate_checks.py

Что проверяет:
  1. синтаксис всех .py пакета и фасада lib/pp_spec_checks.py;
  2. реестр: каждый модуль из CHECK_MODULES существует, лишних check_*.py нет;
  3. у каждого check_*.py есть DEFAULTS, get_options(), get_definition(),
     а runner определён в этом же файле;
  4. ключи: проверок и опций без дублей; у каждой опции ровно одно описание и
     ровно одно значение по умолчанию, и оба лежат в одном файле; каждый
     option_keys ссылается на описанную опцию; check_keys опции указывают на
     существующие проверки, и эти проверки перечисляют опцию у себя;
     файл-владелец опции сам есть среди её check_keys;
  5. check_*.py не импортируют друг друга (общее — только через common*.py);
     обращения DEFAULT_SPEC_CONFIG["ключ"] / DEFAULTS["ключ"] в файле проверки
     указывают на ключ из DEFAULTS этого же файла;
  6. имена, которые используются, но нигде не определены и не импортированы
     (грубый поиск забытых импортов после переноса);
  7. импорт пакета с заглушками вместо Revit API: всё грузится, дефолты
     совпадают с опциями.

Код выхода 0 — всё хорошо, 1 — есть ошибки.
"""

from __future__ import print_function

import ast
import builtins
import glob
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.dirname(HERE)
PACKAGE = os.path.basename(HERE)

FACADE = "pp_spec_checks.py"
FACADE_MODULE = "pp_spec_checks"
RUN_FUNCTION = "run_checks"

ERRORS = []
WARNINGS = []


def error(message):
    ERRORS.append(message)


def warn(message):
    WARNINGS.append(message)


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def const(node):
    if isinstance(node, ast.Constant):
        return node.value
    return None


# --------------------------------------------------------------------------- #
# 1. синтаксис
# --------------------------------------------------------------------------- #

def parse_all():
    trees = {}
    paths = sorted(glob.glob(os.path.join(HERE, "*.py")))
    facade = os.path.join(LIB, FACADE)
    if os.path.exists(facade):
        paths.append(facade)
    else:
        error(u"нет фасада lib/" + FACADE)
    for path in paths:
        try:
            trees[path] = ast.parse(read(path), filename=path)
        except SyntaxError as exc:
            error(u"синтаксис: {0}:{1}: {2}".format(os.path.basename(path), exc.lineno, exc.msg))
    return trees


# --------------------------------------------------------------------------- #
# 2–4. реестр и ключи
# --------------------------------------------------------------------------- #

def registry_modules(tree):
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CHECK_MODULES":
            return [const(e) for e in node.value.elts]
    error(u"registry.py: не найден список CHECK_MODULES")
    return []


def top_function(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def returned(func):
    for node in ast.walk(func):
        if isinstance(node, ast.Return):
            return node.value
    return None


def call_name(node):
    return getattr(getattr(node, "func", None), "id", "")


def keyword(call, name):
    for item in call.keywords:
        if item.arg == name:
            return item.value
    return None


def str_list(node):
    if isinstance(node, (ast.List, ast.Tuple)):
        return [const(e) for e in node.elts]
    return None


def describe_check_module(name, tree):
    info = {"name": name, "defaults": [], "options": [], "definition": None}
    defined = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
                    if target.id == "DEFAULTS":
                        if isinstance(node.value, ast.Dict):
                            info["defaults"] = [const(k) for k in node.value.keys]
                        else:
                            error(u"{0}: DEFAULTS должен быть словарём-литералом".format(name))
    if "DEFAULTS" not in defined:
        error(u"{0}: нет DEFAULTS".format(name))

    # Имена, под которыми в файле доступен DEFAULTS (сам он и псевдонимы вида
    # DEFAULT_SPEC_CONFIG = DEFAULTS), и строковые ключи обращений к ним.
    aliases = set(["DEFAULTS"])
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.value, "id", None) == "DEFAULTS":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
    info["default_reads"] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and getattr(node.value, "id", None) in aliases:
            key = const(node.slice)
            if isinstance(key, str):
                info["default_reads"].append((key, node.lineno))

    func = top_function(tree, "get_options")
    if func is None:
        error(u"{0}: нет get_options()".format(name))
    else:
        value = returned(func)
        if not isinstance(value, ast.List):
            error(u"{0}: get_options() должна возвращать список-литерал".format(name))
        else:
            for item in value.elts:
                if call_name(item) != "CheckOptionDefinition":
                    error(u"{0}: в get_options() не CheckOptionDefinition (строка {1})".format(name, item.lineno))
                    continue
                check_keys = str_list(item.args[2]) if len(item.args) > 2 else str_list(keyword(item, "check_keys"))
                info["options"].append((const(item.args[0]), check_keys or [], item.lineno))

    func = top_function(tree, "get_definition")
    if func is None:
        error(u"{0}: нет get_definition()".format(name))
    else:
        value = returned(func)
        if call_name(value) != "CheckDefinition":
            error(u"{0}: get_definition() должна возвращать CheckDefinition(...)".format(name))
        else:
            # runner и option_keys бывают и именованными, и позиционными (4-й и 5-й аргументы)
            runner = keyword(value, "runner") or (value.args[3] if len(value.args) > 3 else None)
            runner_name = getattr(runner, "id", None)
            if runner_name is None:
                error(u"{0}: у CheckDefinition не задан runner".format(name))
            elif runner_name not in defined:
                error(u"{0}: runner {1} не определён в этом файле".format(name, runner_name))
            info["definition"] = {
                "key": const(value.args[0]),
                "option_keys": str_list(keyword(value, "option_keys") or (value.args[4] if len(value.args) > 4 else None)) or [],
            }
    return info


def check_keys(infos):
    checks = {}
    options = {}
    defaults = {}
    for info in infos:
        definition = info["definition"]
        if definition:
            if definition["key"] in checks:
                error(u"ключ проверки {0} повторяется: {1} и {2}".format(
                    definition["key"], checks[definition["key"]]["module"], info["name"]))
            checks[definition["key"]] = dict(definition, module=info["name"])
        for key, owners, _line in info["options"]:
            if key in options:
                error(u"опция {0} описана дважды: {1} и {2}".format(key, options[key]["module"], info["name"]))
            options[key] = {"module": info["name"], "check_keys": owners}
        for key in info["defaults"]:
            if key in defaults:
                error(u"значение по умолчанию {0} дважды: {1} и {2}".format(key, defaults[key], info["name"]))
            defaults[key] = info["name"]
        own_defaults = set(info["defaults"])
        for key, line in info.get("default_reads", []):
            if key not in own_defaults:
                error(u"{0}:{1}: читает значение по умолчанию {2}, которого нет в DEFAULTS этого файла".format(
                    info["name"], line, key))

    for key, option in options.items():
        if key not in defaults:
            error(u"опция {0} ({1}) без значения в DEFAULTS".format(key, option["module"]))
        elif defaults[key] != option["module"]:
            error(u"опция {0}: описание в {1}, а DEFAULTS в {2} — должны быть в одном файле".format(
                key, option["module"], defaults[key]))
        if not option["check_keys"]:
            error(u"опция {0}: пустой check_keys".format(key))
        owner_is_listed = False
        for check_key in option["check_keys"]:
            check = checks.get(check_key)
            if check is None:
                error(u"опция {0}: check_keys ссылается на несуществующую проверку {1}".format(key, check_key))
                continue
            if key not in check["option_keys"]:
                error(u"опция {0}: проверка {1} есть в check_keys, но не перечисляет опцию в option_keys".format(
                    key, check_key))
            if check["module"] == option["module"]:
                owner_is_listed = True
        if not owner_is_listed:
            error(u"опция {0} описана в {1}, но проверка этого файла не входит в её check_keys".format(
                key, option["module"]))

    for key in defaults:
        if key not in options:
            error(u"DEFAULTS[{0}] в {1}: такой опции нет".format(key, defaults[key]))

    used = set()
    for check_key, check in checks.items():
        for option_key in check["option_keys"]:
            used.add(option_key)
            option = options.get(option_key)
            if option is None:
                error(u"проверка {0} ({1}): опция {2} нигде не описана".format(check_key, check["module"], option_key))
            elif check_key not in option["check_keys"]:
                error(u"проверка {0}: опция {1} не перечисляет её в check_keys".format(check_key, option_key))
    for key in options:
        if key not in used:
            warn(u"опция {0} не используется ни одной проверкой".format(key))
    return checks, options, defaults


# --------------------------------------------------------------------------- #
# 5–6. импорты и неопределённые имена
# --------------------------------------------------------------------------- #

PY2_NAMES = set(["unicode", "basestring", "long", "xrange", "unichr", "__revit__",
                 "__file__", "__name__", "__doc__"])


def bound_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def check_imports_and_names(path, tree):
    base = os.path.basename(path)
    if base.startswith("check_"):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                last = node.module.split(".")[-1]
                if last.startswith("check_"):
                    error(u"{0}: импортирует другую проверку {1} — общее выносить в common*.py".format(
                        base, node.module))
    known = bound_names(tree) | set(dir(builtins)) | PY2_NAMES
    missing = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in known:
            missing.add(node.id)
    for name in sorted(missing):
        error(u"{0}: имя {1} используется, но не определено и не импортировано".format(base, name))


# --------------------------------------------------------------------------- #
# 7. импорт с заглушками Revit API
# --------------------------------------------------------------------------- #

class _Stub(object):
    def __init__(self, path):
        self._path = path

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Stub(self._path + "." + name)

    def __call__(self, *args, **kwargs):
        return _Stub(self._path + "()")

    def __repr__(self):
        return self._path

    def __eq__(self, other):
        return isinstance(other, _Stub) and other._path == self._path

    def __hash__(self):
        return hash(self._path)


def stub_import(checks, options):
    clr = types.ModuleType("clr")
    clr.AddReference = lambda *args: None
    sys.modules["clr"] = clr
    for name in ("Autodesk", "Autodesk.Revit", "Autodesk.Revit.DB"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["Autodesk.Revit.DB"].__getattr__ = lambda name: _Stub("DB." + name)
    builtins.unicode = str
    sys.path.insert(0, LIB)
    try:
        import importlib
        facade = importlib.import_module(FACADE_MODULE)
    except Exception as exc:
        error(u"импорт фасада с заглушками упал: {0!r}".format(exc))
        return
    finally:
        sys.path.pop(0)

    for module_name, details in facade.LOAD_ERRORS:
        last = details.strip().splitlines()[-1] if details.strip() else u""
        error(u"реестр не загрузил {0}: {1}".format(module_name, last))

    config_keys = set(facade.get_default_config())
    option_keys = [o.key for o in facade.get_check_option_definitions()]
    definitions = facade.get_check_definitions()
    if config_keys != set(option_keys):
        error(u"рантайм: дефолты и опции расходятся: {0}".format(sorted(config_keys ^ set(option_keys))))
    if len(definitions) != len(facade.CHECK_MODULES):
        error(u"рантайм: проверок {0}, а модулей в реестре {1}".format(len(definitions), len(facade.CHECK_MODULES)))
    for name in ("create_element_cache", "get_check_definitions", "get_check_option_definitions",
                 "get_default_config", RUN_FUNCTION):
        if not hasattr(facade, name):
            error(u"фасад не отдаёт {0}".format(name))
    print(u"  импорт с заглушками: проверок {0}, опций {1}".format(len(definitions), len(option_keys)))


# --------------------------------------------------------------------------- #

def main():
    trees = parse_all()
    registry_path = os.path.join(HERE, "registry.py")
    modules = registry_modules(trees[registry_path]) if registry_path in trees else []

    files = set(os.path.splitext(os.path.basename(p))[0]
                for p in glob.glob(os.path.join(HERE, "check_*.py")))
    for name in modules:
        if name not in files:
            error(u"реестр: модуля {0}.py нет в папке".format(name))
    if len(set(modules)) != len(modules):
        error(u"реестр: модуль указан дважды")
    for name in sorted(files - set(modules)):
        warn(u"{0}.py лежит в папке, но не подключён в CHECK_MODULES".format(name))

    infos = []
    for name in modules:
        path = os.path.join(HERE, name + ".py")
        if path in trees:
            infos.append(describe_check_module(name, trees[path]))
    checks, options, _defaults = check_keys(infos)

    for path, tree in trees.items():
        check_imports_and_names(path, tree)

    print(u"Файлов разобрано: {0}; проверок: {1}; опций: {2}".format(len(trees), len(checks), len(options)))
    if not ERRORS:
        stub_import(checks, options)

    for message in WARNINGS:
        print(u"  предупреждение: " + message)
    for message in ERRORS:
        print(u"  ОШИБКА: " + message)
    print(u"ИТОГ: " + (u"ошибок нет" if not ERRORS else u"ошибок: {0}".format(len(ERRORS))))
    return 1 if ERRORS else 0


if __name__ == "__main__":
    sys.exit(main())
