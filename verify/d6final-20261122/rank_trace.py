"""Observe the copied original and migration rendering functions."""

import ast
from functools import wraps
import importlib.util
import json
import sys
from threading import Lock, local
from time import perf_counter


class RenderTrace:
    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        self.current = local()
        self.previous_card_ids = set()

    def wrap(self, kind):
        def decorate(function):
            @wraps(function)
            def traced(*args, **kwargs):
                if kind == "render":
                    return self.render(function, args, kwargs)
                row = getattr(self.current, "row", None)
                if row is None:
                    return function(*args, **kwargs)
                started = perf_counter()
                result = function(*args, **kwargs)
                row["card_call_ms"] += (perf_counter() - started) * 1000
                row["card_calls"] += 1
                if kind == 'payload':
                    return result  # Vue keys own DOM reuse.
                if result is not None:
                    card = id(result)
                    if card not in self.previous_card_ids:
                        row["cards_created"] += 1
                    self.current.ids.add(card)
                else:
                    row["cards_created"] += 1
                return result
            return traced
        return decorate

    def render(self, function, args, kwargs):
        row = {"start": perf_counter(), "card_call_ms": 0.0, "card_calls": 0, "cards_created": 0}
        self.current.row = row
        self.current.ids = set()
        try:
            return function(*args, **kwargs)
        finally:
            row["end"] = perf_counter()
            with self.lock:
                if self.current.ids:
                    self.previous_card_ids = self.current.ids
                with self.path.open("a", encoding="ascii") as output:
                    output.write(json.dumps(row) + "\n")
            self.current.row = None


def instrument_original(original, trace):
    path = original / "ranking_tab.py"
    module_spec = importlib.util.spec_from_file_location("ranking_tab", path)
    module = importlib.util.module_from_spec(module_spec)
    module.__dict__["_d6_trace"] = trace.wrap
    tree = ast.parse(path.read_bytes(), filename=str(path))
    functions = {"_render": "render", "_build_card": "card"}
    builders = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "build_ranking_tab"]
    if len(builders) != 1:
        raise ValueError("original ranking builder changed")
    wrapped = set()
    for node in builders[0].body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            decoration = ast.parse(f"_d6_trace({functions[node.name]!r})", mode="eval").body
            node.decorator_list.append(ast.copy_location(decoration, node))
            wrapped.add(node.name)
    if wrapped != functions.keys():
        raise ValueError("original ranking timing points changed")
    sys.modules[module_spec.name] = module
    exec(compile(ast.fix_missing_locations(tree), str(path), "exec"), module.__dict__)


def instrument_nice(trace):
    from pixivvault_nicegui.ui.ranking_view import RankingView, _RankingGallery

    _RankingGallery._card_data = trace.wrap("payload")(_RankingGallery._card_data)
    RankingView.render = trace.wrap("render")(RankingView.render)
