"""Tests for the EFFECTS-tab navigation bar (search / filters / jump-to).

The navigation layer is *view-only*: it pack_forgets/repacks effect blocks
and toggles group open-state, but never writes cfg vars. The most important
guarantee these tests lock in is preset inheritance — no amount of filtering
may change what `get_current_config()` serialises.

All tests need a real Tk display, which headless CI runners lack, so the
module skips cleanly when Tk cannot initialise.
"""
import json
import pytest

tk = pytest.importorskip('tkinter')


@pytest.fixture
def app():
    try:
        from vpc.gui import MainGUI
        gui = MainGUI()
    except tk.TclError as exc:                       # no display (headless CI)
        pytest.skip(f'Tk unavailable: {exc}')
    gui.withdraw()
    gui.update_idletasks()
    yield gui
    gui.destroy()


def _visible(app):
    """Blocks the filter currently considers visible (group state aside)."""
    return {k for k in app._effect_block_frames if app._block_visible.get(k, True)}


def test_contract_methods_exist(app):
    for m in ('expand_group', 'collapse_group', 'expand_all_groups',
              'collapse_all_groups', 'scroll_to_group',
              '_recompute_block_visibility'):
        assert callable(getattr(app, m, None)), m


def test_search_index_covers_every_effect(app):
    from vpc.registry import EFFECTS
    assert set(app._search_index) >= {s.enable_key for s in EFFECTS}


def test_search_by_name(app):
    app.var_fx_search.set('rgb shift')
    app.update_idletasks()
    vis = _visible(app)
    assert 'fx_rgb' in vis
    assert len(vis) < len(app._effect_block_frames)


def test_search_matches_tooltip_text(app):
    # 'left' appears in the fx_rgb tooltip but not in its display name.
    app.var_fx_search.set('left')
    app.update_idletasks()
    assert 'fx_rgb' in _visible(app)


def test_multiword_search_is_and(app):
    app.var_fx_search.set('rgb zzznotaword')
    app.update_idletasks()
    assert _visible(app) == set()


def test_clear_restores_all(app):
    app.var_fx_search.set('rgb')
    app.update_idletasks()
    app.var_fx_search.set('')
    app.update_idletasks()
    assert _visible(app) == set(app._effect_block_frames)


def test_active_only_isolates_enabled(app):
    for k in app._effect_block_frames:
        if k in app.vars:
            app.vars[k].set(False)
    app.vars['fx_rgb'].set(True)
    app.var_fx_active_only.set(True)
    app.update_idletasks()
    assert _visible(app) == {'fx_rgb'}


def test_active_only_is_live(app):
    for k in app._effect_block_frames:
        if k in app.vars:
            app.vars[k].set(False)
    app.vars['fx_rgb'].set(True)
    app.var_fx_active_only.set(True)
    app.update_idletasks()
    app.vars['fx_psort'].set(True)          # enable while filter is on
    app.update_idletasks()
    assert _visible(app) == {'fx_rgb', 'fx_psort'}


def test_collapse_expand_all(app):
    app.collapse_all_groups()
    app.update_idletasks()
    assert not any(h.is_open() for h in app._acc_groups.values())
    app.expand_all_groups()
    app.update_idletasks()
    assert all(h.is_open() for h in app._acc_groups.values())


def test_filter_never_mutates_config(app):
    """The inheritance guarantee: filtering must not touch the cfg."""
    before = json.dumps(app.get_current_config(), sort_keys=True, default=str)
    app.var_fx_search.set('glitch')
    app.var_fx_active_only.set(True)
    app.collapse_all_groups()
    app.update_idletasks()
    after = json.dumps(app.get_current_config(), sort_keys=True, default=str)
    assert before == after
