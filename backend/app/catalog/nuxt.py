"""Разбор данных Nuxt (формат devalue): плоский массив узлов, которые ссылаются друг на друга по индексам."""

import json
import re
from typing import Any

_NUXT_DATA = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef"}


class NuxtDataError(ValueError):
    """На странице нет данных Nuxt или их структура не распознана."""


def extract_nodes(html: str) -> list:
    match = _NUXT_DATA.search(html)
    if not match:
        raise NuxtDataError("на странице нет блока __NUXT_DATA__")
    try:
        nodes = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise NuxtDataError(f"данные Nuxt не разобрались как JSON: {exc}") from None
    if not isinstance(nodes, list) or not nodes:
        raise NuxtDataError("данные Nuxt пусты")
    return nodes


def unflatten(nodes: list) -> Any:
    """Разворачивает плоский массив devalue в обычные словари и списки."""
    cache: dict[int, Any] = {}

    def resolve(index: Any) -> Any:
        # внутри объектов и массивов числа — ссылки; отрицательные означают undefined, NaN и т. п.
        return get(index) if isinstance(index, int) and index >= 0 else None

    def get(i: int) -> Any:
        if i in cache:
            return cache[i]
        node = nodes[i]
        if isinstance(node, list):
            if node and isinstance(node[0], str) and node[0] in _WRAPPERS:
                value = resolve(node[1]) if len(node) > 1 else None
                cache[i] = value
                return value
            if node and isinstance(node[0], str):
                # Set, Map, Date и другие специальные типы нам не нужны: оставляем как есть
                cache[i] = node
                return node
            out: list = []
            cache[i] = out
            out.extend(resolve(x) for x in node)
            return out
        if isinstance(node, dict):
            obj: dict = {}
            cache[i] = obj
            for key, ref in node.items():
                obj[key] = resolve(ref)
            return obj
        cache[i] = node
        return node

    return get(0)
