from __future__ import annotations

import dataclasses as dc
import warnings
from collections.abc import Callable, MutableMapping
from typing import Any, Literal

@dc.dataclass(slots=True)
class Token:
    type: str
    tag: str
    nesting: Literal[-1, 0, 1]
    attrs: dict[str, str | int | float] = dc.field(default_factory=dict)
    map: list[int] | None = None
    level: int = 0
    children: list["Token"] | None = None
    content: str = ""
    markup: str = ""
    info: str = ""
    meta: dict[Any, Any] = dc.field(default_factory=dict)
    block: bool = False
    hidden: bool = False

    def __post_init__(self) -> None:
        if self.attrs is None:
            self.attrs = {}
        elif not isinstance(self.attrs, dict):
            self.attrs = dict(self.attrs)

    def attrIndex(self, name: str) -> int:
        warnings.warn(
            "Token.attrIndex should not be used, since Token.attrs is a dictionary",
            UserWarning,
            stacklevel=2,
        )
        return list(self.attrs).index(name) if name in self.attrs else -1

    def attrItems(self):
        return list(self.attrs.items())

    def attrPush(self, attrData) -> None:
        self.attrSet(*attrData)

    def attrSet(self, name, value) -> None:
        self.attrs[name] = value

    def attrGet(self, name):
        return self.attrs.get(name)

    def attrJoin(self, name: str, value: str) -> None:
        if name in self.attrs:
            current = self.attrs[name]
            if not isinstance(current, str):
                raise TypeError(f"existing attr 'name' is not a str: {current}")
            self.attrs[name] = f"{current} {value}"
        else:
            self.attrs[name] = value

    def copy(self, **changes):
        return dc.replace(self, **changes)

    def as_dict(
        self,
        *,
        children: bool = True,
        as_upstream: bool = True,
        meta_serializer: Callable | None = None,
        filter: Callable | None = None,
        dict_factory: Callable[..., MutableMapping] = dict,
    ):
        mapping = dict_factory((f.name, getattr(self, f.name)) for f in dc.fields(self))
        if filter:
            mapping = dict_factory((k, v) for k, v in mapping.items() if filter(k, v))
        if as_upstream and "attrs" in mapping:
            mapping["attrs"] = (
                None if not mapping["attrs"] else [[k, v] for k, v in mapping["attrs"].items()]
            )
        if meta_serializer and "meta" in mapping:
            mapping["meta"] = meta_serializer(mapping["meta"])
        if children and mapping.get("children"):
            mapping["children"] = [
                child.as_dict(
                    children=children,
                    as_upstream=as_upstream,
                    meta_serializer=meta_serializer,
                    filter=filter,
                    dict_factory=dict_factory,
                )
                for child in mapping["children"]
            ]
        return mapping

    @classmethod
    def from_dict(cls, dct):
        token = cls(**dct)
        if token.children:
            token.children = [cls.from_dict(c) for c in token.children]
        return token
