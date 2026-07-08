"""Регрессионные тесты применения пресета (apply_preset_config).

Загрузка обязана полностью определять состояние UI из сохранённого
конфига: любой ключ, отсутствующий в пресете, должен откатываться к
дефолту из реестра, а НЕ сохранять то, что сейчас держит UI. Пресет,
сохранённый старой сборкой (без новых ключей fx_viz_*/_drive/_gate/_react),
раньше молча оставлял старые эффекты включёнными и старую аудио-проводку
активной - в рендере всплывали эффекты, которые пользователь не включал.
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
    """Загрузка конфига без ключей новых фич обязана сбросить их к
    дефолтам, а не сохранить "грязное" состояние UI."""
    # Пачкаем UI, будто пользователь до этого поэкспериментировал.
    app.vars['fx_viz_plasma'].set(True)
    app.vars['fx_viz_plasma_mode'].set('warp')
    app.vars['fx_rgb_drive'].set('bass')
    app.vars['fx_feedback_react'].set('on')
    app.vars['fx_negative_gate'].set('onset')

    # Минимальный пресет "старой версии" - включает только RGB shift и
    # ничего не знает о более новых ключах.
    old_cfg = {'fx_rgb': True, 'fx_rgb_chance': 0.7}
    app.apply_preset_config(old_cfg, 'old')

    # Отсутствующие ключи откатились к дефолтам реестра.
    assert app.vars['fx_viz_plasma'].get() is False
    assert app.vars['fx_rgb_drive'].get() == 'segment'
    assert app.vars['fx_feedback_react'].get() == 'off'
    assert app.vars['fx_negative_gate'].get() == 'off'

    # И цепочка рендера больше не содержит фантомный визуализатор.
    chain = [type(c).__name__ for c in build_chain(app.get_current_config())]
    assert 'PlasmaFieldEffect' not in chain


def test_full_roundtrip_is_identity(app):
    """Полный конфиг, сохранённый этой версией, должен загрузиться
    побитово идентичным - сброс-и-наложение не должен портить пресеты
    той же версии."""
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
    """Переключатель Dust - строковый выбор 'off'/'on', а не bool: иначе
    в комбобоксе показывались бы True/False и проверка '== on' в фабрике
    никогда бы не срабатывала."""
    v = app.vars['fx_vhstape_dust']
    assert v.get() == 'off'
    from vpc.registry import find_spec
    spec = find_spec('vhstape')
    v.set('on')
    assert spec.build_kwargs(app.get_current_config()).get('dust') is True
    v.set('off')
    assert spec.build_kwargs(app.get_current_config()).get('dust') is False
