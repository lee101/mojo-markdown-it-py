from __future__ import annotations

from collections.abc import Sequence

from ._lib import escape_html
from .token import Token


class RendererHTML:
    __output__ = "html"

    def __init__(self, parser=None):
        self.rules = {
            "text": self.text,
            "code_inline": self.code_inline,
            "code_block": self.code_block,
            "fence": self.fence,
            "image": self.image,
            "hardbreak": self.hardbreak,
            "softbreak": self.softbreak,
            "html_block": self.html_block,
            "html_inline": self.html_inline,
        }

    def render(self, tokens: Sequence[Token], options, env) -> str:
        result: list[str] = []
        for i, token in enumerate(tokens):
            if token.type == "inline":
                result.append(self.renderInline(token.children or [], options, env))
            elif token.type in self.rules:
                result.append(self.rules[token.type](tokens, i, options, env))
            else:
                result.append(self.renderToken(tokens, i, options, env))
        return "".join(result)

    def renderInline(self, tokens: Sequence[Token], options, env) -> str:
        result: list[str] = []
        for i, token in enumerate(tokens):
            rule = self.rules.get(token.type)
            result.append(
                rule(tokens, i, options, env)
                if rule
                else self.renderToken(tokens, i, options, env)
            )
        return "".join(result)

    def renderToken(self, tokens, idx, options, env) -> str:
        token = tokens[idx]
        if token.hidden:
            return ""
        result = ""
        if token.block and token.nesting != -1 and idx and tokens[idx - 1].hidden:
            result = "\n"
        result += ("</" if token.nesting == -1 else "<") + token.tag
        result += self.renderAttrs(token)
        if token.nesting == 0 and options["xhtmlOut"]:
            result += " /"
        need_lf = False
        if token.block:
            need_lf = True
            if token.nesting == 1 and idx + 1 < len(tokens):
                nxt = tokens[idx + 1]
                if nxt.type == "inline" or nxt.hidden:
                    need_lf = False
                elif nxt.nesting == -1 and nxt.tag == token.tag:
                    need_lf = False
        return result + (">\n" if need_lf else ">")

    @staticmethod
    def renderAttrs(token: Token) -> str:
        return "".join(
            f' {escape_html(str(key))}="{escape_html(str(value))}"'
            for key, value in token.attrItems()
        )

    def renderInlineAsText(self, tokens, options, env) -> str:
        result: list[str] = []
        for token in tokens or []:
            if token.type == "text":
                result.append(token.content)
            elif token.type == "image":
                result.append(self.renderInlineAsText(token.children, options, env))
            elif token.type == "softbreak":
                result.append("\n")
        return "".join(result)

    def code_inline(self, tokens, idx, options, env):
        token = tokens[idx]
        return f"<code{self.renderAttrs(token)}>{escape_html(token.content)}</code>"

    def code_block(self, tokens, idx, options, env):
        token = tokens[idx]
        return f"<pre{self.renderAttrs(token)}><code>{escape_html(token.content)}</code></pre>\n"

    def fence(self, tokens, idx, options, env):
        token = tokens[idx]
        info = token.info.strip()
        lang_name = info.split(maxsplit=1)[0] if info else ""
        lang_attrs = info.split(maxsplit=1)[1] if info and len(info.split(maxsplit=1)) == 2 else ""
        highlight = options.get("highlight")
        rendered = highlight(token.content, lang_name, lang_attrs) if highlight else ""
        rendered = rendered or escape_html(token.content)
        if rendered.startswith("<pre"):
            return rendered + "\n"
        attrs = token.attrs.copy()
        if lang_name:
            prefix = options["langPrefix"]
            attrs["class"] = (
                f"{attrs['class']} {prefix}{lang_name}"
                if "class" in attrs
                else f"{prefix}{lang_name}"
            )
        temp = Token("", "", 0, attrs=attrs)
        return f"<pre><code{self.renderAttrs(temp)}>{rendered}</code></pre>\n"

    def image(self, tokens, idx, options, env):
        token = tokens[idx]
        token.attrSet("alt", self.renderInlineAsText(token.children, options, env))
        return self.renderToken(tokens, idx, options, env)

    @staticmethod
    def hardbreak(tokens, idx, options, env):
        return "<br />\n" if options["xhtmlOut"] else "<br>\n"

    @staticmethod
    def softbreak(tokens, idx, options, env):
        if options["breaks"]:
            return "<br />\n" if options["xhtmlOut"] else "<br>\n"
        return "\n"

    @staticmethod
    def text(tokens, idx, options, env):
        return escape_html(tokens[idx].content)

    @staticmethod
    def html_block(tokens, idx, options, env):
        return tokens[idx].content

    @staticmethod
    def html_inline(tokens, idx, options, env):
        return tokens[idx].content
