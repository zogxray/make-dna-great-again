"""Реестр режимов приложения. Порядок и подписи — в base.MODE_LABELS.

Импорты режимов ленивые: cold start тянет только F1 (genome), остальные — при первом F-ключе.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__all__ = [
    "TreeWorkspace", "DataWorkspace", "GenomeWorkspace", "PgsWorkspace",
    "ReferenceWorkspace", "ChatWorkspace", "JobsWorkspace",
    "SamplesWorkspace", "HelpWorkspace",
]

if TYPE_CHECKING:
    from .chat import ChatWorkspace
    from .data import DataWorkspace
    from .genome import GenomeWorkspace
    from .help import HelpWorkspace
    from .jobs import JobsWorkspace
    from .pgs import PgsWorkspace
    from .reference import ReferenceWorkspace
    from .samples import SamplesWorkspace
    from .tree import TreeWorkspace

_LAZY = {
    "ChatWorkspace": ".chat",
    "DataWorkspace": ".data",
    "GenomeWorkspace": ".genome",
    "HelpWorkspace": ".help",
    "JobsWorkspace": ".jobs",
    "PgsWorkspace": ".pgs",
    "ReferenceWorkspace": ".reference",
    "SamplesWorkspace": ".samples",
    "TreeWorkspace": ".tree",
}


def __getattr__(name: str):
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    value = getattr(import_module(mod, __name__), name)
    globals()[name] = value
    return value
