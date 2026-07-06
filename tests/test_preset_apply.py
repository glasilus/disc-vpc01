"""Regression tests for preset application (apply_preset_config).

The load path must fully determine the UI state from the saved config: any
key absent from the saved preset has to fall back to its registry default,
NOT keep whatever the UI currently holds. A preset saved by an older build
(missing the newer fx_viz_*/_drive/_gate/_react keys) used to silently leave
stale effects enabled and stale audio wiring active — phantom effects in the
render that the user never enabled.
"""
import pytest

from vpc.registry import build_chain, EFFECTS


@pytest.fixture
def app():
    tk = pytest.importorskip('tkinter')
    try:
        from vpc.gui import MainGUI
        gui = MainGUI()
    except tk.TclError:
        pytest.skip('no Tk display available')
    gui.withdraw()
    yield gui
    gui.destroy()


def test_partial_preset_resets_absent_keys(app):
    """Loading a config missing the new-feature keys must reset them to
    defaults, not preserve dirty UI state."""
    # Dirty the UI as if the user had been experimenting.
    app.vars['fx_viz_plasma'].set(True)
    app.vars['fx_viz_plasma_mode'].set('warp')
    app.vars['fx_rgb_drive'].set('bass')
    app.vars['fx_feedback_react'].set('on')
    app.vars['fx_negative_gate'].set('onset')

    # A minimal "old-version" preset that enables only RGB shift and knows
    # nothing about the newer keys.
    old_cfg = {'fx_rgb': True, 'fx_rgb_chance': 0.7}
    app.apply_preset_config(old_cfg, 'old')

    # Absent keys fell back to registry defaults.
    assert app.vars['fx_viz_plasma'].get() is False
    assert app.vars['fx_rgb_drive'].get() == 'segment'
    assert app.vars['fx_feedback_react'].get() == 'off'
    assert app.vars['fx_negative_gate'].get() == 'off'

    # And the render chain no longer contains the phantom visualizer.
    chain = [type(c).__name__ for c in build_chain(app.get_current_config())]
    assert 'PlasmaFieldEffect' not in chain


def test_full_roundtrip_is_identity(app):
    """A full config saved this version must load back byte-identical —
    reset-then-overlay must not perturb same-version presets."""
    app.vars['fx_rgb'].set(True)
    app.vars['fx_psort'].set(True)
    app.vars['fx_psort_int'].set(0.77)
    app.vars['fx_viz_bars'].set(True)
    app.vars['fx_viz_bars_mode'].set('warp')

    cfg1 = app.get_current_config()
    import copy
    app.apply_preset_config(copy.deepcopy(cfg1), 'rt')
    cfg2 = app.get_current_config()

    diffs = {k for k in set(cfg1) | set(cfg2) if cfg1.get(k) != cfg2.get(k)}
    assert diffs == set()


def test_vhstape_dust_is_string_choice(app):
    """The Dust toggle is a string 'off'/'on' choice, not a bool — otherwise
    its combobox shows True/False and the '== on' factory check never fires."""
    v = app.vars['fx_vhstape_dust']
    assert v.get() == 'off'
    from vpc.registry import find_spec
    spec = find_spec('vhstape')
    v.set('on')
    assert spec.build_kwargs(app.get_current_config()).get('dust') is True
    v.set('off')
    assert spec.build_kwargs(app.get_current_config()).get('dust') is False
