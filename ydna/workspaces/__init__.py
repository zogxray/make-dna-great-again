"""Реестр режимов приложения. Порядок и подписи — в base.MODE_LABELS."""

from .chat import ChatWorkspace
from .data import DataWorkspace
from .genome import GenomeWorkspace
from .help import HelpWorkspace
from .pgs import PgsWorkspace
from .reference import ReferenceWorkspace
from .tree import TreeWorkspace

__all__ = ["TreeWorkspace", "DataWorkspace", "GenomeWorkspace", "PgsWorkspace",
           "ReferenceWorkspace", "ChatWorkspace", "HelpWorkspace"]
