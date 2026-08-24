from __future__ import annotations

import html
import re
import string
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from ._lib import Line
from .token import Token


_ESCAPABLE = set(string.punctuation)
_ENTITY = re.compile(r"&(?:#[xX][0-9a-fA-F]{1,8}|#[0-9]{1,8}|[A-Za-z][A-Za-z0-9]{1,31});")
_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^ <>]*)>")
_EMAIL = re.compile(r"<([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,61}[A-Za-z0-9])?)>")
_HTML_INLINE = re.compile(
    r"(?:<!--(?:[\s\S]*?)-->|<\?.*?\?>|<![A-Z].*?>|<!\[CDATA\[[\s\S]*?\]\]>|"
    r"</?[A-Za-z][A-Za-z0-9-]*(?:\s+[^<>]*?)?\s*/?>)"
)
_INLINE_SPECIAL = re.compile(r"[\n\r!&*<\[\\_`]")
_REFERENCE = re.compile(
    r'^ {0,3}\[([^\]]+)\]:\s*(?:<([^>]+)>|(\S+))(?:\s+(?:"([^"]*)"|\'([^\']*)\'|\(([^)]*)\)))?\s*$'
)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$")
_ATX_CLOSING = re.compile(r"[ \t]+#+[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_BULLET = re.compile(r"^( {0,3})([-+*])(?:[ \t]+(.*)|[ \t]*)$")
_ORDERED = re.compile(r"^( {0,3})(\d{1,9})([.)])(?:[ \t]+(.*)|[ \t]*)$")
_BLOCKQUOTE = re.compile(r"^ {0,3}>")
_BLOCKQUOTE_LINE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_HTML_BLOCK_START = re.compile(
    r"^ {0,3}(?:<!--|<script(?:\s|>|$)|<style(?:\s|>|$)|<pre(?:\s|>|$))",
    re.I,
)
_BLOCK_TAG = re.compile(
    r"^ {0,3}</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|"
    r"col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|"
    r"form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|"
    r"menuitem|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|"
    r"tbody|td|tfoot|th|thead|title|tr|track|ul)(?:\s|/?>|$)",
    re.I,
)


def _token(type_, tag="", nesting=0, **kwargs):
    return Token(type_, tag, nesting, **kwargs)


def _normalize_reference(label: str) -> str:
    return " ".join(label.strip().split()).casefold().upper()


def _normalize_url(url: str) -> str:
    return quote(html.unescape(url), safe="/:#?&=@[]!$'()*+,;%-._~")


def _validate_url(url: str) -> bool:
    normalized = url.strip().lower()
    return not normalized.startswith(
        ("javascript:", "vbscript:", "file:", "data:")
    ) or normalized.startswith(
        ("data:image/gif;", "data:image/png;", "data:image/jpeg;", "data:image/webp;")
    )


def _punct(ch: str) -> bool:
    return bool(ch) and unicodedata.category(ch)[0] in "PS"


def _flanking(source: str, pos: int, width: int, marker: str):
    prev = source[pos - 1] if pos else "\n"
    nxt = source[pos + width] if pos + width < len(source) else "\n"
    prev_ws, next_ws = prev.isspace(), nxt.isspace()
    prev_p, next_p = _punct(prev), _punct(nxt)
    left = not next_ws and (not next_p or prev_ws or prev_p)
    right = not prev_ws and (not prev_p or next_ws or next_p)
    if marker == "_":
        return left and (not right or prev_p), right and (not left or next_p)
    return left, right


def _levels(tokens: list[Token]) -> None:
    level = 0
    for token in tokens:
        if token.nesting == -1:
            level -= 1
        token.level = level
        if token.nesting == 1:
            level += 1


def _emit_text(tokens: list[Token], content: str) -> None:
    if not content:
        return
    if tokens and tokens[-1].type == "text":
        tokens[-1].content += content
    else:
        tokens.append(_token("text", content=content))


def _find_closer(source: str, start: int, marker: str, width: int) -> int:
    needle = marker * width
    pos = start
    while True:
        pos = source.find(needle, pos)
        if pos < 0:
            return -1
        run_start = pos
        while run_start and source[run_start - 1] == marker:
            run_start -= 1
        run_end = pos + width
        while run_end < len(source) and source[run_end] == marker:
            run_end += 1
        _, can_close = _flanking(source, pos, width, marker)
        if can_close and run_end - run_start >= width:
            return run_end - width
        pos += width


def _matching_bracket(source: str, start: int) -> int:
    depth = 0
    i = start
    while i < len(source):
        if source[i] == "\\":
            i += 2
            continue
        if source[i] == "[":
            depth += 1
        elif source[i] == "]":
            if depth == 0:
                return i
            depth -= 1
        i += 1
    return -1


def _inline_target(source: str, pos: int):
    if pos >= len(source) or source[pos] != "(":
        return None
    i = pos + 1
    while i < len(source) and source[i].isspace():
        i += 1
    if i < len(source) and source[i] == "<":
        end = source.find(">", i + 1)
        if end < 0 or "\n" in source[i:end]:
            return None
        destination = source[i + 1 : end]
        i = end + 1
    else:
        begin = i
        depth = 0
        while i < len(source):
            ch = source[i]
            if ch == "\\" and i + 1 < len(source):
                i += 2
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif ch.isspace() and depth == 0:
                break
            i += 1
        destination = source[begin:i]
    while i < len(source) and source[i].isspace():
        i += 1
    title = ""
    if i < len(source) and source[i] in "\"'(":
        opener = source[i]
        closer = ")" if opener == "(" else opener
        end = source.find(closer, i + 1)
        if end < 0:
            return None
        title = source[i + 1 : end]
        i = end + 1
        while i < len(source) and source[i].isspace():
            i += 1
    if i >= len(source) or source[i] != ")":
        return None
    return destination, title, i + 1


def parse_inline(source: str, env: dict, options, rules: set[str] | None = None) -> list[Token]:
    default_rules = {
        "text",
        "newline",
        "escape",
        "backticks",
        "strikethrough",
        "emphasis",
        "link",
        "image",
        "autolink",
        "html_inline",
        "entity",
    }
    enabled = (
        rules
        if rules is not None
        else default_rules - set(options.get("_disabled", ()))
    )
    if not source:
        return []
    if not _INLINE_SPECIAL.search(source):
        return [_token("text", content=source)]
    tokens: list[Token] = []
    buf: list[str] = []

    def flush():
        if buf:
            _emit_text(tokens, "".join(buf))
            buf.clear()

    i = 0
    while i < len(source):
        special = _INLINE_SPECIAL.search(source, i)
        if special and special.start() > i:
            buf.append(source[i : special.start()])
            i = special.start()
            continue
        ch = source[i]
        if ch == "\\" and "escape" in enabled:
            if i + 1 < len(source) and source[i + 1] == "\n":
                flush()
                tokens.append(_token("hardbreak", "br"))
                i += 2
                continue
            if i + 1 < len(source) and source[i + 1] in _ESCAPABLE:
                buf.append(source[i + 1])
                i += 2
                continue
        if ch == "\n" and "newline" in enabled:
            trailing = 0
            while buf:
                tail = buf[-1]
                stripped = tail.rstrip(" ")
                trailing += len(tail) - len(stripped)
                if stripped:
                    buf[-1] = stripped
                    break
                buf.pop()
            flush()
            tokens.append(_token("hardbreak" if trailing >= 2 else "softbreak", "br"))
            i += 1
            continue
        if ch == "`" and "backticks" in enabled:
            width = 1
            while i + width < len(source) and source[i + width] == "`":
                width += 1
            end = source.find("`" * width, i + width)
            if end >= 0:
                flush()
                content = source[i + width : end].replace("\n", " ")
                if (
                    len(content) >= 2
                    and content.startswith(" ")
                    and content.endswith(" ")
                    and content.strip(" ")
                ):
                    content = content[1:-1]
                tokens.append(
                    _token("code_inline", "code", content=content, markup="`" * width)
                )
                i = end + width
                continue
        if ch in "*_" and "emphasis" in enabled:
            run = 1
            while i + run < len(source) and source[i + run] == ch:
                run += 1
            can_open, _ = _flanking(source, i, min(run, 2), ch)
            width = 1 if run % 2 else 2
            if can_open:
                end = _find_closer(source, i + run, ch, width)
                if end >= 0:
                    flush()
                    kind, tag = ("strong", "strong") if width == 2 else ("em", "em")
                    markup = ch * width
                    tokens.append(_token(kind + "_open", tag, 1, markup=markup))
                    tokens.extend(parse_inline(source[i + width : end], env, options, enabled))
                    tokens.append(_token(kind + "_close", tag, -1, markup=markup))
                    i = end + width
                    continue
        image = ch == "!" and i + 1 < len(source) and source[i + 1] == "["
        if (ch == "[" or image) and ("image" if image else "link") in enabled:
            label_start = i + 2 if image else i + 1
            close = _matching_bracket(source, label_start)
            if close >= 0:
                label = source[label_start:close]
                target = _inline_target(source, close + 1)
                end = close + 1
                if target:
                    destination, title, end = target
                else:
                    ref_label = label
                    if end < len(source) and source[end] == "[":
                        ref_end = source.find("]", end + 1)
                        if ref_end >= 0:
                            ref_label = source[end + 1 : ref_end] or label
                            end = ref_end + 1
                    ref = env.get("references", {}).get(_normalize_reference(ref_label))
                    if not ref:
                        target = None
                    else:
                        destination, title = ref["href"], ref.get("title", "")
                        target = True
                if target and not _validate_url(destination):
                    target = None
                if target:
                    flush()
                    attrs = {"src" if image else "href": _normalize_url(destination)}
                    if image:
                        attrs["alt"] = ""
                    if title:
                        attrs["title"] = html.unescape(title)
                    children = parse_inline(label, env, options, enabled)
                    if image:
                        tokens.append(
                            _token(
                                "image",
                                "img",
                                attrs=attrs,
                                children=children,
                                content=label,
                            )
                        )
                    else:
                        tokens.append(_token("link_open", "a", 1, attrs=attrs))
                        tokens.extend(children)
                        tokens.append(_token("link_close", "a", -1))
                    i = end
                    continue
        if ch == "<":
            match = _AUTOLINK.match(source, i) if "autolink" in enabled else None
            if match and not _validate_url(match.group(1)):
                match = None
            email = _EMAIL.match(source, i) if "autolink" in enabled and not match else None
            if match or email:
                flush()
                shown = (match or email).group(1)
                href = shown if match else "mailto:" + shown
                attrs = {"href": _normalize_url(href)}
                tokens.append(
                    _token("link_open", "a", 1, attrs=attrs, markup="autolink", info="auto")
                )
                _emit_text(tokens, shown)
                tokens.append(
                    _token("link_close", "a", -1, markup="autolink", info="auto")
                )
                i = (match or email).end()
                continue
            match = _HTML_INLINE.match(source, i) if options["html"] and "html_inline" in enabled else None
            if match:
                flush()
                tokens.append(_token("html_inline", content=match.group(0)))
                i = match.end()
                continue
        if ch == "&" and "entity" in enabled:
            match = _ENTITY.match(source, i)
            if match:
                decoded = html.unescape(match.group(0))
                if decoded != match.group(0):
                    buf.append(decoded)
                    i = match.end()
                    continue
        buf.append(ch)
        i += 1
    flush()
    _levels(tokens)
    return tokens


@dataclass(slots=True)
class Marker:
    kind: str
    markup: str
    content: str
    width: int
    number: int = 1
    indent: int = 0


def _marker(text: str) -> Marker | None:
    match = _BULLET.match(text)
    if match:
        prefix, markup, content = match.groups()
        width = len(prefix) + len(markup) + 1
        return Marker("bullet", markup, content or "", width, indent=len(prefix))
    match = _ORDERED.match(text)
    if match:
        prefix, number, markup, content = match.groups()
        width = len(prefix) + len(number) + len(markup) + 1
        return Marker("ordered", markup, content or "", width, int(number), len(prefix))
    return None


def _is_thematic(text: str) -> bool:
    stripped = text.strip()
    return (
        len(stripped) >= 3
        and stripped[0] in "*-_"
        and all(ch == stripped[0] or ch in " \t" for ch in stripped)
        and stripped.count(stripped[0]) >= 3
    )


def _is_fence_close(text: str, marker: str) -> bool:
    candidate = text.lstrip(" ")
    if len(text) - len(candidate) > 3:
        return False
    candidate = candidate.rstrip(" \t")
    return len(candidate) >= len(marker) and not candidate.strip(marker[0])


def _starts_block(lines: list[Line], i: int, options) -> bool:
    text = lines[i].text
    stripped = text.lstrip()
    return bool(
        not stripped
        or _FENCE.match(text)
        or _ATX.match(text)
        or _marker(text)
        or _is_thematic(text)
        or _BLOCKQUOTE.match(text)
        or (_BLOCK_TAG.match(text) and options["html"])
        or text.startswith("    ")
    )


def _add_container(tokens, type_, tag, content, start, end, env, options):
    tokens.append(_token(type_ + "_open", tag, 1, map=[start, end], block=True))
    tokens.append(
        _token(
            "inline",
            map=[start, end],
            content=content,
            children=parse_inline(content, env, options),
            block=True,
        )
    )
    tokens.append(_token(type_ + "_close", tag, -1, block=True))


def parse_blocks(lines: list[Line], env: dict, options) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        text = line.text
        if not text.strip():
            i += 1
            continue
        ref = _REFERENCE.match(text)
        if ref:
            label, angle, bare, t1, t2, t3 = ref.groups()
            env.setdefault("references", {})[_normalize_reference(label)] = {
                "href": _normalize_url(angle or bare),
                "title": html.unescape(t1 or t2 or t3 or ""),
                "map": [line.number, line.number + 1],
            }
            i += 1
            continue
        fence = _FENCE.match(text)
        if fence:
            markup, info = fence.groups()
            end = i + 1
            content: list[str] = []
            while end < len(lines) and not _is_fence_close(lines[end].text, markup):
                content.append(lines[end].text)
                end += 1
            finish = end + 1 if end < len(lines) else end
            tokens.append(
                _token(
                    "fence",
                    "code",
                    map=[line.number, lines[finish - 1].number + 1 if finish else line.number + 1],
                    content="\n".join(content) + ("\n" if content else ""),
                    markup=markup,
                    info=info.strip(),
                    block=True,
                )
            )
            i = finish
            continue
        atx = _ATX.match(text)
        if atx:
            markup, content = atx.groups()
            content = _ATX_CLOSING.sub("", content or "").strip()
            tag = "h" + str(len(markup))
            _add_container(
                tokens, "heading", tag, content, line.number, line.number + 1, env, options
            )
            tokens[-3].markup = tokens[-1].markup = markup
            i += 1
            continue
        if i + 1 < len(lines) and _SETEXT.match(lines[i + 1].text) and text.strip():
            marker = _SETEXT.match(lines[i + 1].text).group(1)
            tag = "h1" if marker[0] == "=" else "h2"
            _add_container(
                tokens,
                "heading",
                tag,
                text.strip(),
                line.number,
                lines[i + 1].number + 1,
                env,
                options,
            )
            tokens[-3].markup = tokens[-1].markup = marker
            i += 2
            continue
        if _is_thematic(text):
            tokens.append(
                _token(
                    "hr",
                    "hr",
                    map=[line.number, line.number + 1],
                    markup=text.strip()[0],
                    block=True,
                )
            )
            i += 1
            continue
        if _BLOCKQUOTE.match(text):
            start = i
            inner: list[Line] = []
            while i < len(lines):
                match = _BLOCKQUOTE_LINE.match(lines[i].text)
                if match:
                    inner.append(Line(match.group(1), lines[i].number, 0))
                    i += 1
                elif not lines[i].text.strip():
                    inner.append(Line("", lines[i].number, 0))
                    i += 1
                else:
                    break
            tokens.append(
                _token(
                    "blockquote_open",
                    "blockquote",
                    1,
                    map=[lines[start].number, lines[i - 1].number + 1],
                    markup=">",
                    block=True,
                )
            )
            tokens.extend(parse_blocks(inner, env, options))
            tokens.append(
                _token("blockquote_close", "blockquote", -1, markup=">", block=True)
            )
            continue
        marker = _marker(text)
        if marker:
            start = i
            kind = marker.kind
            list_tag = "ul" if kind == "bullet" else "ol"
            list_type = "bullet_list" if kind == "bullet" else "ordered_list"
            attrs = {"start": marker.number} if kind == "ordered" and marker.number != 1 else {}
            open_token = _token(
                list_type + "_open",
                list_tag,
                1,
                attrs=attrs,
                map=[line.number, line.number + 1],
                markup=marker.markup,
                block=True,
            )
            tokens.append(open_token)
            paragraph_pairs: list[tuple[int, int]] = []
            loose = False
            while i < len(lines):
                item_marker = _marker(lines[i].text)
                if (
                    not item_marker
                    or item_marker.kind != kind
                    or item_marker.indent != marker.indent
                ):
                    break
                item_start = i
                item_lines = [Line(item_marker.content, lines[i].number, 0)]
                i += 1
                item_blank = False
                while i < len(lines):
                    next_marker = _marker(lines[i].text)
                    if (
                        next_marker
                        and next_marker.kind == kind
                        and next_marker.indent == item_marker.indent
                    ):
                        break
                    current = lines[i]
                    if not current.text.strip():
                        next_i = i + 1
                        while next_i < len(lines) and not lines[next_i].text.strip():
                            next_i += 1
                        if next_i >= len(lines):
                            break
                        following = _marker(lines[next_i].text)
                        following_indent = len(lines[next_i].text) - len(
                            lines[next_i].text.lstrip(" ")
                        )
                        if not (
                            (
                                following
                                and following.kind == kind
                                and following.indent == item_marker.indent
                            )
                            or following_indent >= item_marker.width
                        ):
                            break
                        item_lines.append(Line("", current.number, 0))
                        item_blank = True
                        i += 1
                        continue
                    indent = len(current.text) - len(current.text.lstrip(" "))
                    if indent >= item_marker.width:
                        item_lines.append(
                            Line(current.text[item_marker.width :], current.number, 0)
                        )
                        i += 1
                        continue
                    break
                item_end_number = item_lines[-1].number + 1
                tokens.append(
                    _token(
                        "list_item_open",
                        "li",
                        1,
                        map=[lines[item_start].number, item_end_number],
                        markup=item_marker.markup,
                        info=str(item_marker.number) if kind == "ordered" else "",
                        block=True,
                    )
                )
                before = len(tokens)
                tokens.extend(parse_blocks(item_lines, env, options))
                after = len(tokens)
                paragraph_pairs.append((before, after))
                tokens.append(
                    _token(
                        "list_item_close",
                        "li",
                        -1,
                        markup=item_marker.markup,
                        block=True,
                    )
                )
                loose = loose or item_blank
            if not loose:
                for begin, finish in paragraph_pairs:
                    for token in tokens[begin:finish]:
                        if token.type in {"paragraph_open", "paragraph_close"}:
                            token.hidden = True
            open_token.map = [lines[start].number, lines[i - 1].number + 1]
            tokens.append(
                _token(
                    list_type + "_close",
                    list_tag,
                    -1,
                    markup=marker.markup,
                    block=True,
                )
            )
            continue
        if text.startswith("    "):
            start = i
            content: list[str] = []
            while i < len(lines) and (
                lines[i].text.startswith("    ") or not lines[i].text.strip()
            ):
                content.append(lines[i].text[4:] if lines[i].text.strip() else "")
                i += 1
            while content and content[-1] == "":
                content.pop()
            tokens.append(
                _token(
                    "code_block",
                    "code",
                    map=[lines[start].number, lines[i - 1].number + 1],
                    content="\n".join(content) + "\n",
                    block=True,
                )
            )
            continue
        html_start = (
            options["html"]
            and (
                _BLOCK_TAG.match(text)
                or _HTML_BLOCK_START.match(text)
            )
        )
        if html_start:
            start = i
            content = []
            while i < len(lines) and lines[i].text.strip():
                content.append(lines[i].text)
                i += 1
            tokens.append(
                _token(
                    "html_block",
                    map=[lines[start].number, lines[i - 1].number + 1],
                    content="\n".join(content) + "\n",
                    block=True,
                )
            )
            continue
        start = i
        content = [text.strip() if text.startswith("   ") else text]
        i += 1
        while i < len(lines) and lines[i].text.strip():
            if _SETEXT.match(lines[i].text):
                break
            if _starts_block(lines, i, options):
                break
            if _REFERENCE.match(lines[i].text):
                break
            content.append(lines[i].text)
            i += 1
        _add_container(
            tokens,
            "paragraph",
            "p",
            "\n".join(content),
            lines[start].number,
            lines[i - 1].number + 1,
            env,
            options,
        )
    _levels(tokens)
    return tokens
