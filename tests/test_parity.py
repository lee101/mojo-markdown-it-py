from __future__ import annotations

import pytest

from markdown_it import MarkdownIt as UpstreamMarkdownIt

from mojo_markdown_it import MarkdownIt, RendererHTML, Token


CASES = [
    "",
    "hello world\n",
    "one\ntwo\n",
    "# h1\n###### h6 ######\n",
    "Heading\n=======\nSubheading\n---\n",
    "***\n___\n- - -\n",
    "~~~ js\nif (a < b) x++;\n~~~~\n",
    "```\nabc\n",
    "    one\n    two\n\n    three\n",
    "> alpha\n> beta\n",
    "> > nested\n",
    "- one\n- two\n",
    "3. three\n4. four\n",
    "- outer\n  - inner\n",
    "- one\n\n- two\n",
    "<div>\nhello\n</div>\n\ntext\n",
    r"\*not em\* and \# hash" + "\n",
    "&copy; &#169; &#x3C; &bogus;\n",
    "`` foo ` bar `` and `  x  `\n",
    "*em* **strong** _em_ __strong__\n",
    "foo_bar_baz foo*bar*baz\n",
    "**bold and *em***\n",
    "***both***\n",
    '[label](/url "title")\n',
    "[label](<a b>)\n",
    '![alt *text*](image.png "t")\n',
    '[foo]: /url "title"\n\n[foo] [bar][foo]\n',
    "<https://example.com/a?q=1&x=2> <me@example.com>\n",
    "line  \nbreak\\\nagain\n",
    'a <span class="x">b</span> c\n',
    "Привет *мир* &amp; 你好\n",
    "A **bold** and *em* with [link](https://x.test \"t\") and "
    "![alt *x*](img.png).  \nnext `a < b` &amp; <u>x</u>",
]


@pytest.mark.parametrize("source", CASES)
def test_render_commonmark_parity(source):
    assert MarkdownIt("commonmark").render(source) == UpstreamMarkdownIt(
        "commonmark"
    ).render(source)


@pytest.mark.parametrize(
    "source",
    [
        "# hi *there*\n",
        "```python\nx < y\n```\n",
        "- alpha\n- beta\n",
        "[ref]: /target\n\n[ref]\n",
    ],
)
def test_token_stream_parity(source):
    ours = [token.as_dict() for token in MarkdownIt().parse(source)]
    theirs = [token.as_dict() for token in UpstreamMarkdownIt("commonmark").parse(source)]
    assert ours == theirs


@pytest.mark.parametrize(
    "source",
    [
        "plain",
        "*em* and **strong**",
        "[link](/url) and <https://example.com>",
        "`x < y` &amp; ![alt](img.png)",
    ],
)
def test_render_inline_parity(source):
    assert MarkdownIt().renderInline(source) == UpstreamMarkdownIt(
        "commonmark"
    ).renderInline(source)


def test_options_breaks_xhtml_and_html():
    source = "one\ntwo\n\n<span>x</span>\n"
    for options in (
        {"breaks": True},
        {"xhtmlOut": False},
        {"html": False},
        {"breaks": True, "xhtmlOut": False},
    ):
        assert MarkdownIt(options_update=options).render(source) == UpstreamMarkdownIt(
            "commonmark", options
        ).render(source)


def test_environment_references_match_upstream():
    source = '[name]: /url "title"\n\n[name]\n'
    ours_env, theirs_env = {}, {}
    MarkdownIt().render(source, ours_env)
    UpstreamMarkdownIt("commonmark").render(source, theirs_env)
    assert ours_env == theirs_env


def test_custom_renderer_rule():
    class Renderer(RendererHTML):
        def __init__(self, parser=None):
            super().__init__(parser)
            self.rules["text"] = self.text

        @staticmethod
        def text(tokens, idx, options, env):
            return tokens[idx].content.upper()

    assert MarkdownIt(renderer_cls=Renderer).render("Hello *world*") == (
        "<p>HELLO <em>WORLD</em></p>\n"
    )


def test_plugin_use_and_option_update():
    def plugin(md, value):
        md.options["breaks"] = value

    md = MarkdownIt().use(plugin, True)
    assert md.render("a\nb") == "<p>a<br />\nb</p>\n"
    assert md.set({"xhtmlOut": False}) is md


def test_configure_parse_inline_enable_and_link_helpers():
    md = MarkdownIt().configure({"options": {"breaks": True}}, {"xhtmlOut": False})
    assert md.renderInline("a\nb") == "a<br>\nb"
    inline = md.parseInline("*word*")
    assert [token.type for token in inline[0].children] == [
        "em_open",
        "text",
        "em_close",
    ]
    md.disable("emphasis")
    assert md.renderInline("*word*") == "*word*"
    assert md.enable("emphasis") is md
    assert md.renderInline("*word*") == "<em>word</em>"
    assert md.normalizeLink("a b?x=1&y=2") == "a%20b?x=1&y=2"
    assert md.normalizeLinkText("a b") == "a%20b"
    assert not md.validateLink(" JAVASCRIPT:alert(1)")
    assert md.validateLink("data:image/png;base64,abc")


@pytest.mark.parametrize(
    "destination",
    ["javascript:alert(1)", "vbscript:msgbox(1)", "file:///etc/passwd", "data:text/html,x"],
)
def test_unsafe_link_destinations_match_upstream(destination):
    source = f"[link](<{destination}>)"
    assert MarkdownIt().render(source) == UpstreamMarkdownIt("commonmark").render(source)


def test_enable_disable_inline_rules_match_upstream():
    source = "a *word* and <b>HTML</b>"
    for rule in ("emphasis", "html_inline"):
        assert MarkdownIt().disable(rule).render(source) == UpstreamMarkdownIt(
            "commonmark"
        ).disable(rule).render(source)


def test_token_attribute_api_and_roundtrip():
    token = Token("link_open", "a", 1, attrs=[["href", "/a"]])
    token.attrPush(("title", "A"))
    token.attrJoin("class", "first")
    token.attrJoin("class", "second")
    assert token.attrGet("href") == "/a"
    assert token.attrItems() == [
        ("href", "/a"),
        ("title", "A"),
        ("class", "first second"),
    ]
    assert Token.from_dict(token.as_dict(as_upstream=False)) == token


def test_invalid_input_and_preset():
    with pytest.raises(TypeError):
        MarkdownIt().render(b"bytes")
    with pytest.raises(KeyError):
        MarkdownIt("not-a-preset")
