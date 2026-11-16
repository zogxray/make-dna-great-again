"""F1 Genome Esc → clear_chrom_filter; empty filter does not swallow Esc."""

from __future__ import annotations

from unittest.mock import MagicMock

from textual_wgpu.binding import iter_bindings

from ydna.tui.wgpu_genome import GenomeScreen


def test_genome_bindings_include_escape_clear_filter():
    pairs = list(iter_bindings(GenomeScreen.BINDINGS))
    assert ("escape", "clear_chrom_filter") in pairs


def test_clear_chrom_filter_empty_returns_false():
    screen = GenomeScreen()
    view = MagicMock(filter="")
    inp = MagicMock(text="")

    def _q(sel: str, cls=None):  # noqa: ANN001
        return view if "chroms" in sel else inp

    screen.query_one = _q  # type: ignore[method-assign]
    assert screen.clear_chrom_filter() is False
    view.set_filter.assert_not_called()


def test_clear_chrom_filter_clears_and_returns_true():
    screen = GenomeScreen()
    view = MagicMock(filter="rs12")
    inp = MagicMock(text="rs12")
    screen.query_one = (  # type: ignore[method-assign]
        lambda sel, cls=None: view if "chroms" in sel else inp
    )
    screen._sync_filter_input = MagicMock()  # type: ignore[method-assign]
    assert screen.clear_chrom_filter() is True
    screen._sync_filter_input.assert_called_once_with("")
    view.set_filter.assert_called_once_with("")
    view.focus.assert_called_once()


def test_on_key_escape_empty_not_consumed():
    """Binding must not eat Esc when there is nothing to clear (quit path)."""
    screen = GenomeScreen()
    view = MagicMock(filter="")
    inp = MagicMock(text="")
    screen.query_one = (  # type: ignore[method-assign]
        lambda sel, cls=None: view if "chroms" in sel else inp
    )
    assert screen.on_key("escape") is False


def test_genome_open_requires_app():
    screen = GenomeScreen()
    screen.app = None
    notes: list[str] = []
    screen._notify = notes.append  # type: ignore[method-assign]
    screen.open()
    assert notes == ["No app · add genotype"]


def test_genome_open_calls_ask_open_file():
    screen = GenomeScreen()
    app = MagicMock()
    screen.app = app
    screen.open()
    app.ask_open_file.assert_called_once()
    assert app.ask_open_file.call_args.kwargs.get("title") == "Add genotype file"
    app.ask_prompt.assert_not_called()


def test_genome_remove_requires_confirm():
    screen = GenomeScreen()
    path = MagicMock()
    path.name = "sample.txt"
    path.__str__ = lambda self: "/tmp/sample.txt"  # noqa: ARG005
    screen.files = [path]
    screen.info = {}
    ol = MagicMock()
    ol.highlighted = 0
    screen.query_one = MagicMock(return_value=ol)  # type: ignore[method-assign]
    app = MagicMock()
    app._focus = ol
    screen.app = app
    screen.remove()
    app.ask_confirm.assert_called_once()
    assert "Remove" in app.ask_confirm.call_args.args[0]


def test_genome_remove_no_silent_delete_without_app():
    screen = GenomeScreen()
    screen.app = None
    notes: list[str] = []
    screen._notify = notes.append  # type: ignore[method-assign]
    screen._remove_confirmed = MagicMock()  # type: ignore[method-assign]
    screen.remove()
    assert notes == ["No app · remove genotype"]
    screen._remove_confirmed.assert_not_called()
