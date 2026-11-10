"""Реестр режимов приложения. Порядок и подписи — в base.MODE_LABELS."""

from .chat import ChatWorkspace
from .data import DataWorkspace
from .genome import GenomeWorkspace
from .help import HelpWorkspace
from .jobs import JobsWorkspace
from .pgs import PgsWorkspace
from .reference import ReferenceWorkspace
from .samples import SamplesWorkspace
from .tree import TreeWorkspace

__all__ = ["TreeWorkspace", "DataWorkspace", "GenomeWorkspace", "PgsWorkspace",
           "ReferenceWorkspace", "ChatWorkspace", "JobsWorkspace",
           "SamplesWorkspace", "HelpWorkspace"]
