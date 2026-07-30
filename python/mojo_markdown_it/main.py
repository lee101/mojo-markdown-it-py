from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from typing import Any

from ._lib import scan_lines
from .parser import parse_blocks, parse_inline
from .renderer import RendererHTML
from .token import Token


class Options(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


DEFAULT_OPTIONS = {
    "maxNesting": 20,
    "html": True,
    "linkify": False,
    "typographer": False,
    "quotes": "“”‘’",
    "xhtmlOut": True,
    "breaks": False,
    "langPrefix": "language-",
    "highlight": None,
}


class MarkdownIt:
    def __init__(
        self,
        config: str | Mapping[str, Any] = "commonmark",
        options_update: Mapping[str, Any] | None = None,
        *,
        renderer_cls=RendererHTML,
    ):
        if isinstance(config, str) and config not in {"commonmark", "default", "zero"}:
            raise KeyError(f"Wrong `markdown-it` preset '{config}'")
        self.options = Options(DEFAULT_OPTIONS)
        if isinstance(config, Mapping):
            self.options.update(config.get("options", {}))
        if options_update:
            self.options.update(options_update)
        self.renderer = renderer_cls(self)
        self._disabled: set[str] = set()

    def parse(self, src: str, env: dict | None = None) -> list[Token]:
        if not isinstance(src, str):
            raise TypeError("Input data should be a string")
        state = env if env is not None else {}
        self.options["_disabled"] = self._disabled
        return parse_blocks(scan_lines(src), state, self.options)

    def render(self, src: str, env: dict | None = None):
        state = env if env is not None else {}
        return self.renderer.render(self.parse(src, state), self.options, state)

    def parseInline(self, src: str, env: dict | None = None) -> list[Token]:
        if not isinstance(src, str):
            raise TypeError("Input data should be a string")
        state = env if env is not None else {}
        self.options["_disabled"] = self._disabled
        children = parse_inline(src, state, self.options)
        return [Token("inline", "", 0, children=children, content=src)]

    def renderInline(self, src: str, env: dict | None = None):
        state = env if env is not None else {}
        tokens = self.parseInline(src, state)
        return self.renderer.render(tokens, self.options, state)

    def set(self, options: Mapping[str, Any]):
        self.options.update(options)
        return self

    def configure(self, presets, options_update=None):
        if isinstance(presets, Mapping):
            self.options.update(presets.get("options", {}))
        if options_update:
            self.options.update(options_update)
        return self

    @staticmethod
    def _names(names: str | Iterable[str]) -> set[str]:
        return {names} if isinstance(names, str) else set(names)

    def enable(self, names, ignoreInvalid: bool = False):
        self._disabled -= self._names(names)
        return self

    def disable(self, names, ignoreInvalid: bool = False):
        self._disabled |= self._names(names)
        return self

    def use(self, plugin, *params, **options):
        plugin(self, *params, **options)
        return self

    @contextmanager
    def reset_rules(self):
        old_rules = self.renderer.rules.copy()
        old_options = self.options.copy()
        try:
            yield
        finally:
            self.renderer.rules = old_rules
            self.options.clear()
            self.options.update(old_options)

    def get_all_rules(self):
        return sorted(
            {
                "blockquote",
                "code",
                "fence",
                "heading",
                "hr",
                "html_block",
                "lheading",
                "list",
                "paragraph",
                "reference",
                "autolink",
                "backticks",
                "emphasis",
                "entity",
                "escape",
                "html_inline",
                "image",
                "link",
                "newline",
                "text",
            }
        )

    @staticmethod
    def normalizeLink(url: str) -> str:
        from .parser import _normalize_url

        return _normalize_url(url)

    normalizeLinkText = normalizeLink

    @staticmethod
    def validateLink(url: str) -> bool:
        from .parser import _validate_url

        return _validate_url(url)
