"""Bridge ``textual`` imports to ``textual_wgpu`` for dual-backend testing."""

from __future__ import annotations

import sys


def install_textual_wgpu_shim() -> dict[str, object]:
    """Alias ``textual.*`` modules onto ``textual_wgpu.*`` in ``sys.modules``.

    Must be called before importing ``ydna.tui`` or ``ydna.workspaces``.
    """
    import textual_wgpu
    from textual_wgpu import (
        app,
        binding,
        containers,
        content,
        dom,
        driver,
        events,
        geometry,
        keys,
        layout,
        message,
        on,
        pilot,
        reactive,
        screen,
        scroll_view,
        scrollbar,
        selection,
        strip,
        theme,
        widget,
        widgets,
        worker,
    )
    from textual_wgpu.widgets import option_list

    mapping: dict[str, object] = {
        "textual": textual_wgpu,
        "textual.app": app,
        "textual.binding": binding,
        "textual.containers": containers,
        "textual.content": content,
        "textual.dom": dom,
        "textual.driver": driver,
        "textual.events": events,
        "textual.geometry": geometry,
        "textual.keys": keys,
        "textual.layout": layout,
        "textual.message": message,
        "textual.on": on,
        "textual.pilot": pilot,
        "textual.reactive": reactive,
        "textual.screen": screen,
        "textual.scroll_view": scroll_view,
        "textual.scrollbar": scrollbar,
        "textual.selection": selection,
        "textual.strip": strip,
        "textual.theme": theme,
        "textual.widget": widget,
        "textual.widgets": widgets,
        "textual.widgets.option_list": option_list,
        "textual.worker": worker,
    }
    previous = {k: sys.modules.get(k) for k in mapping}
    sys.modules.update(mapping)
    return previous
