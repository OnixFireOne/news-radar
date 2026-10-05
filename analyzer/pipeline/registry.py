"""Named pipeline functions and their registries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from analyzer.pipeline.context import AnalysisResult, AnalyzeContext, DigestContext, Row

T = TypeVar("T")
AnalyzerFn = Callable[[list[Row], AnalyzeContext], Awaitable[list[AnalysisResult]]]
HookFn = Callable[[Row, Row, AnalyzeContext], None]
SelectorFn = Callable[[list[Row], DigestContext], list[Row]]
ExtraFn = Callable[[list[Row], DigestContext], Awaitable[None]]
ComposeFn = Callable[[list[Row], DigestContext], Awaitable[Any]]
RenderFn = Callable[[Any, list[Row], DigestContext], tuple[str, str]]


@dataclass(frozen=True)
class Writer:
    """Two phases so extras can run between the LLM draft and the final render."""

    compose: ComposeFn  # LLM call → draft (text or JSON)
    render: RenderFn    # draft → (content, parse_mode)


class Registry(Generic[T]):
    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    def register(self, name: str) -> Callable[[T], T]:
        def decorator(item: T) -> T:
            if name in self._items:
                raise ValueError(f"Pipeline brick already registered: {name}")
            self._items[name] = item
            return item
        return decorator

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"Unknown pipeline brick {name!r}; known names: {', '.join(self.names())}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._items)


ANALYZERS: Registry[AnalyzerFn] = Registry()
HOOKS: Registry[HookFn] = Registry()
SELECTORS: Registry[SelectorFn] = Registry()
EXTRAS: Registry[ExtraFn] = Registry()
WRITERS: Registry[Writer] = Registry()
