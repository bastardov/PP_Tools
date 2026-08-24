# -*- coding: utf-8 -*-
u"""Валидатор WPF-окон PP_Tools (запускать python3 на копиях, не в расширении).

Проверки — по разделу «Автоматические проверки» UI.md.
"""

from __future__ import print_function

import ast
import io
import os
import re
import sys
import xml.etree.ElementTree as ET


NS = {"x": "http://schemas.microsoft.com/winfx/2006/xaml"}
XNAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
XKEY = "{http://schemas.microsoft.com/winfx/2006/xaml}Key"

PROBLEMS = []
NOTES = []


def bad(msg):
    PROBLEMS.append(msg)


def note(msg):
    NOTES.append(msg)


def reset():
    del PROBLEMS[:]
    del NOTES[:]


def read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if text and text[0] == u"﻿":
        text = text[1:]
    return text


# ----------------------------------------------------------------------
# 1. Синтаксис .py
# ----------------------------------------------------------------------

def check_py_syntax(paths):
    trees = {}
    for p in paths:
        src = read(p)
        try:
            trees[p] = ast.parse(src)
        except SyntaxError as ex:
            # Python 3 не понимает валидный для IronPython 2.7 префикс ur/ru.
            # Для статического AST меняем только префикс, raw-семантика остаётся.
            compat = re.sub(r"(?i)(?<![A-Za-z0-9_])(?:ur|ru)(?=[\"'])",
                            "r", src)
            if compat != src:
                try:
                    trees[p] = ast.parse(compat)
                    continue
                except SyntaxError:
                    pass
            bad(u"[синтаксис] {}: строка {}: {}".format(p, ex.lineno, ex.msg))
    return trees


# ----------------------------------------------------------------------
# 2. Валидность XAML
# ----------------------------------------------------------------------

def check_xaml_wellformed(paths):
    roots = {}
    for p in paths:
        try:
            roots[p] = ET.fromstring(read(p).encode("utf-8"))
        except Exception as ex:
            bad(u"[xaml] {}: {}".format(os.path.basename(p), ex))
    return roots


# ----------------------------------------------------------------------
# 3. Setter TargetName внутри ControlTemplate
# ----------------------------------------------------------------------

BAD_TARGET_SUFFIX = ("Transform", "Brush", "Geometry")


def check_setter_targetname(path, root):
    for tpl in root.iter():
        if not tpl.tag.endswith("}ControlTemplate"):
            continue

        names = {}
        for el in tpl.iter():
            n = el.get(XNAME)
            if n:
                names[n] = el.tag.split("}")[-1]

        for st in tpl.iter():
            if not st.tag.endswith("}Setter"):
                continue
            tn = st.get("TargetName")
            if not tn:
                continue
            if tn not in names:
                bad(u"[TargetName] {}: Setter целится в '{}', такого x:Name нет"
                    .format(os.path.basename(path), tn))
            elif names[tn].endswith(BAD_TARGET_SUFFIX):
                bad(u"[TargetName] {}: Setter целится во Freezable <{}> '{}'"
                    .format(os.path.basename(path), names[tn], tn))


# ----------------------------------------------------------------------
# 4-5. StaticResource и ключи из кода
# ----------------------------------------------------------------------

RE_STATIC = re.compile(r"\{StaticResource\s+([A-Za-z0-9_.]+)\s*\}")
RE_STATIC_INLINE = re.compile(r"StaticResource\s+([A-Za-z0-9_.]+)")

CONVERTERS = {u"BoolVis", u"NullVis"}


def theme_keys(theme_path):
    keys = set()
    for m in re.finditer(r'x:Key="([^"]+)"', read(theme_path)):
        keys.add(m.group(1))
    return keys


def check_static_resources(paths, keys, local_keys_by_file):
    known = keys | CONVERTERS
    for p in paths:
        text = read(p)
        local = local_keys_by_file.get(p, set())
        for m in RE_STATIC_INLINE.finditer(text):
            key = m.group(1)
            if key not in known and key not in local:
                bad(u"[StaticResource] {}: ключ '{}' не найден в теме"
                    .format(os.path.basename(p), key))


def check_code_resource_keys(py_paths, keys):
    pattern = re.compile(r'Resources\[\s*u?["\']([^"\']+)["\']\s*\]')
    for p in py_paths:
        for m in pattern.finditer(read(p)):
            if m.group(1) not in keys:
                bad(u"[ресурс из кода] {}: ключ '{}' не найден в теме"
                    .format(os.path.basename(p), m.group(1)))


# ----------------------------------------------------------------------
# 6. FindName -> x:Name
# ----------------------------------------------------------------------

def xaml_names(root):
    names = set()
    for el in root.iter():
        n = el.get(XNAME)
        if n:
            names.add(n)
    return names


def check_findname(py_paths, names):
    pattern = re.compile(r'(?:FindName|find)\(\s*u?["\']([^"\']+)["\']\s*\)')
    for p in py_paths:
        for m in pattern.finditer(read(p)):
            if m.group(1) not in names:
                bad(u"[FindName] {}: имя '{}' не найдено в разметке"
                    .format(os.path.basename(p), m.group(1)))


# ----------------------------------------------------------------------
# 7. Binding -> свойства ViewModel
# ----------------------------------------------------------------------

RE_BINDING = re.compile(r"\{Binding\s+([^}]*)\}")


def bindings(root_text):
    result = []
    for m in RE_BINDING.finditer(root_text):
        body = m.group(1).strip()
        parts = [x.strip() for x in body.split(",")]
        path = None
        element_name = None
        for i, part in enumerate(parts):
            if part.startswith("Path="):
                path = part[5:].strip()
            elif part.startswith("ElementName="):
                element_name = part[12:].strip()
            elif i == 0 and "=" not in part:
                path = part
        result.append((path, element_name))
    return result


def vm_properties(tree):
    props = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    for dec in item.decorator_list:
                        if isinstance(dec, ast.Name) and dec.id == "property":
                            props.add(item.name)
                    props.add(item.name)
                elif isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name):
                            props.add(t.id)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                        and t.value.id == "self":
                    props.add(t.attr)
    return props


DP_WHITELIST = {"IsChecked", "IsEnabled", "Text", "SelectedItem", "Content",
                "IsSelected", "Visibility", "Tag"}


def check_bindings(xaml_path, xaml_text, names, props):
    for path, element_name in bindings(xaml_text):
        if element_name is not None:
            if element_name not in names:
                bad(u"[ElementName] {}: '{}' не найден среди x:Name"
                    .format(os.path.basename(xaml_path), element_name))
            if path and path not in DP_WHITELIST:
                note(u"[Binding] {}: ElementName-биндинг на свойство '{}' — проверить вручную"
                     .format(os.path.basename(xaml_path), path))
            continue

        if not path:
            continue

        if path not in props:
            bad(u"[Binding] {}: свойство '{}' не найдено во ViewModel"
                .format(os.path.basename(xaml_path), path))


# ----------------------------------------------------------------------
# 9. GridLength с числовым вторым аргументом
# ----------------------------------------------------------------------

def check_gridlength(py_paths):
    pattern = re.compile(r"GridLength\(\s*[^,)]+\s*,\s*(\d+)\s*\)")
    for p in py_paths:
        for m in pattern.finditer(read(p)):
            bad(u"[GridLength] {}: второй аргумент '{}' — нужен GridUnitType"
                .format(os.path.basename(p), m.group(1)))


# ----------------------------------------------------------------------
# 10-11. Связывания имён
# ----------------------------------------------------------------------

BUILTINS = set(dir(__builtins__) if not isinstance(__builtins__, dict)
               else __builtins__.keys())
BUILTINS |= {"unicode", "basestring", "long", "xrange", "reduce", "__file__",
             "__name__", "__revit__", "__builtin__", "raw_input", "unichr",
             "execfile", "reload", "cmp", "apply", "buffer", "intern"}


class BindingCollector(ast.NodeVisitor):
    def __init__(self):
        self.bound = set()
        self.loaded = set()

    def visit_Import(self, node):
        for a in node.names:
            self.bound.add((a.asname or a.name).split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for a in node.names:
            if a.name == "*":
                self.bound.add("*")
            else:
                self.bound.add(a.asname or a.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self.bound.add(node.name)
        args = node.args
        for a in list(args.args) + list(getattr(args, "kwonlyargs", [])):
            self.bound.add(a.arg if hasattr(a, "arg") else a.id)
        if args.vararg:
            self.bound.add(getattr(args.vararg, "arg", args.vararg))
        if args.kwarg:
            self.bound.add(getattr(args.kwarg, "arg", args.kwarg))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        args = node.args
        for a in list(args.args):
            self.bound.add(a.arg if hasattr(a, "arg") else a.id)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.bound.add(node.name)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.bound.add(node.name if isinstance(node.name, str)
                           else node.name.id)
        self.generic_visit(node)

    def visit_Global(self, node):
        for n in node.names:
            self.bound.add(n)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.loaded.add(node.id)
        else:
            self.bound.add(node.id)
        self.generic_visit(node)


def unbound_names(tree):
    c = BindingCollector()
    c.visit(tree)
    return c.loaded - c.bound - BUILTINS, ("*" in c.bound)


def check_unbound(path, tree, baseline=None):
    names, has_star = unbound_names(tree)
    if baseline is not None:
        names = names - baseline
    if names:
        label = u"(сверх бэкапа)" if baseline is not None else u""
        bad(u"[висячие имена] {} {}: {}".format(
            os.path.basename(path), label, u", ".join(sorted(names))))


def unused_functions(path, tree, siblings=()):
    u"""siblings — деревья остальных файлов кнопки: функция может вызываться там."""
    defined = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defined[node.name] = node.lineno
    used = set()
    for t in (tree,) + tuple(siblings):
        for node in ast.walk(t):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used.add(node.id)
            if isinstance(node, ast.Attribute):
                used.add(node.attr)
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    used.add(a.name)
    text = read(path)
    for name, line in sorted(defined.items(), key=lambda kv: kv[1]):
        if name not in used and (u'"' + name) not in text:
            bad(u"[мёртвый код] {}: функция '{}' (строка {}) нигде не вызывается"
                .format(os.path.basename(path), name, line))


# ----------------------------------------------------------------------
# 15. Отступы шапки таблицы
# ----------------------------------------------------------------------

HEADER_MARGIN = "13,0,25,6"


def check_table_header(path, text, root):
    has_listbox = "<ListBox" in text
    if not has_listbox:
        return
    if HEADER_MARGIN in text:
        if 'VerticalScrollBarVisibility="Visible"' not in text:
            bad(u"[шапка таблицы] {}: есть шапка {}, но нет "
                u'VerticalScrollBarVisibility="Visible"'.format(
                    os.path.basename(path), HEADER_MARGIN))
    else:
        note(u"[шапка таблицы] {}: есть ListBox — проверить, нет ли шапки "
             u"колонок отдельным Grid (нужен Margin {})".format(
                 os.path.basename(path), HEADER_MARGIN))


# ----------------------------------------------------------------------
# Запреты
# ----------------------------------------------------------------------

FORBIDDEN = [
    ("System.Windows.Forms", u"WinForms"),
    ("System.Drawing", u"System.Drawing"),
    ("MessageBox", u"MessageBox"),
    ("forms.alert", u"forms.alert"),
    ("script.exit", u"script.exit"),
]

RE_HEX = re.compile(r'"#[0-9A-Fa-f]{6,8}"')


def check_forbidden(paths):
    for p in paths:
        text = read(p)
        for needle, label in FORBIDDEN:
            if needle in text:
                bad(u"[запрет] {}: найдено {}".format(os.path.basename(p), label))
        # pp_theme.xaml — единственное место, где хекс-цвета разрешены
        if p.endswith(".xaml") and os.path.basename(p) != "pp_theme.xaml":
            for m in RE_HEX.finditer(text):
                bad(u"[хекс-цвет] {}: {}".format(os.path.basename(p), m.group(0)))


# ----------------------------------------------------------------------
# Побайтовая сверка сохранённых функций
# ----------------------------------------------------------------------

def top_level_blocks(path):
    u"""Исходники функций и классов верхнего уровня, по имени."""
    text = read(path)
    lines = text.split(u"\n")
    tree = ast.parse(text)
    blocks = {}

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            continue
        start = node.lineno - 1
        end = max(getattr(n, "lineno", node.lineno) for n in ast.walk(node))
        while end < len(lines) and (end >= len(lines) or True):
            if end >= len(lines):
                break
            if lines[end].strip() == u"" or lines[end].startswith((u" ", u"\t")):
                end += 1
            else:
                break
        src = u"\n".join(lines[start:end]).rstrip()
        blocks[node.name] = src

    return blocks


def check_preserved(old_path, new_path, expected_same, expected_gone):
    old = top_level_blocks(old_path)
    new = top_level_blocks(new_path)

    for name in expected_same:
        if name not in old:
            bad(u"[сверка] в бэкапе нет '{}'".format(name))
            continue
        if name not in new:
            bad(u"[сверка] в новом файле пропала функция '{}'".format(name))
            continue
        if old[name] != new[name]:
            bad(u"[сверка] тело '{}' изменилось".format(name))

    for name in expected_gone:
        if name in new:
            bad(u"[сверка] '{}' должна была уйти, но осталась".format(name))

    extra_old = set(old) - set(new) - set(expected_gone)
    if extra_old:
        bad(u"[сверка] потеряны без объяснения: {}".format(
            u", ".join(sorted(extra_old))))

    return old, new


# ----------------------------------------------------------------------

def report():
    print()
    if NOTES:
        print(u"--- к сведению ---")
        for n in NOTES:
            print(u"  ~ " + n)
        print()
    if PROBLEMS:
        print(u"--- ПРОБЛЕМЫ ({}) ---".format(len(PROBLEMS)))
        for p in PROBLEMS:
            print(u"  ! " + p)
        return 1
    print(u"Все проверки пройдены.")
    return 0
