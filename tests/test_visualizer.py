"""Тесты группы аудио-реактивных визуализаторов WINDOWS MEDIA PLAYER."""
import numpy as np
import pytest


# хелперы
def _fake_sample(val=0.8):
    from vpc.render.reactor import AudioSample
    from vpc.analyzer import N_BINS
    return AudioSample(val, val, val, val, True, np.full(N_BINS, val, np.float32), 0.0)


# фичи анализатора
def test_audiofeatures_shape_contract():
    from vpc.analyzer import AudioFeatures, N_BINS
    n = 50
    af = AudioFeatures(
        times=np.linspace(0, 1, n),
        bass=np.zeros(n), mid=np.zeros(n), high=np.zeros(n), onset=np.zeros(n),
        bins=np.zeros((n, N_BINS)), sr=22050, hop=512,
    )
    assert af.bins.shape == (n, N_BINS)
    assert len(af.times) == len(af.bass) == len(af.onset)


def test_analyze_returns_features(tmp_path):
    pytest.importorskip("soundfile")
    import soundfile as sf
    from vpc.analyzer import AudioAnalyzer, AudioFeatures, N_BINS
    sr = 22050
    t = np.linspace(0, 2.0, sr * 2, endpoint=False)
    y = 0.5 * np.sin(2 * np.pi * 80 * t) + 0.3 * np.sin(2 * np.pi * 4000 * t)
    wav = tmp_path / "tone.wav"
    sf.write(wav, y, sr)
    segs, dur, feats = AudioAnalyzer(str(wav)).analyze()
    assert isinstance(feats, AudioFeatures)
    assert feats.bins.shape[1] == N_BINS
    assert feats.bass.max() > 0 and feats.high.max() > 0


# reactor
def test_reactor_interpolates_and_smooths():
    from vpc.analyzer import AudioFeatures, N_BINS
    from vpc.render.reactor import AudioReactor
    n = 10
    af = AudioFeatures(
        times=np.linspace(0, 1, n), bass=np.linspace(0, 1, n),
        mid=np.zeros(n), high=np.zeros(n), onset=np.zeros(n),
        bins=np.zeros((n, N_BINS)), sr=22050, hop=512)
    r = AudioReactor(af, fps=30.0)
    s0 = r.sample(0.0)
    s1 = r.sample(1.0)
    assert s0.bass < s1.bass
    assert s1.bins.shape == (N_BINS,)
    assert 0.0 <= s1.bass <= 1.0


def test_reactor_synth_fallback_animates():
    from vpc.render.reactor import AudioReactor
    r = AudioReactor(None, fps=30.0)
    a = r.synth(0.8, 0)
    b = r.synth(0.8, 7)
    assert a.bins.shape[0] > 0
    assert (a.bass, a.mid) != (b.bass, b.mid)


# подключение к движку
def test_segment_has_live_field():
    from vpc.analyzer import Segment, SegmentType
    s = Segment(0, 1, 1, SegmentType.SUSTAIN, 0.5, 0.1, 0.1, 0.0)
    assert hasattr(s, "live") and s.live is None


# композитор
def test_composite_replace_and_over():
    from vpc.effects.visualizer.compose import composite
    src = np.full((16, 16, 3), 40, np.uint8)
    vis = np.full((16, 16, 3), 200, np.uint8)
    field = np.full((16, 16), 255, np.uint8)
    out_r = composite(src, vis, field, 'replace')
    assert np.array_equal(out_r, vis)
    out_o = composite(src, vis, field, 'over', opacity=1.0, blend='screen')
    assert out_o.mean() > src.mean()
    assert out_o.shape == src.shape and out_o.dtype == np.uint8


def test_composite_warp_and_mask_preserve_shape():
    from vpc.effects.visualizer.compose import composite
    src = np.random.randint(0, 255, (24, 32, 3), np.uint8)
    vis = np.random.randint(0, 255, (24, 32, 3), np.uint8)
    field = np.random.randint(0, 255, (24, 32), np.uint8)
    for mode in ('warp', 'mask'):
        out = composite(src, vis, field, mode)
        assert out.shape == src.shape and out.dtype == np.uint8


def test_read_sample_fallback_when_no_live():
    from vpc.analyzer import Segment, SegmentType
    from vpc.effects.visualizer.reactive import read_sample
    s = Segment(0, 1, 1, SegmentType.SUSTAIN, 0.5, 0.1, 0.1, 0.0)
    sample = read_sample(s)
    assert sample.bins.max() > 0   # промодулировано интенсивностью, не пустое


# рендереры
def test_spectrum_renderers_nonempty():
    from vpc.effects.visualizer.spectrum import SpectrumBarsEffect, RadialSpectrumEffect
    for cls in (SpectrumBarsEffect, RadialSpectrumEffect):
        vis, field = cls()._render(120, 160, _fake_sample(0.9))
        assert vis.shape == (120, 160, 3) and vis.dtype == np.uint8
        assert field.shape == (120, 160)
        assert vis.max() > 0


def test_scope_renderers_nonempty():
    from vpc.effects.visualizer.scope import OscilloscopeEffect, LissajousEffect
    for cls in (OscilloscopeEffect, LissajousEffect):
        vis, field = cls()._render(120, 160, _fake_sample(0.9))
        assert vis.shape == (120, 160, 3) and vis.dtype == np.uint8
        assert vis.max() > 0


def test_abstraction_renderers_nonempty():
    from vpc.effects.visualizer.abstraction import (
        PlasmaFieldEffect, BeatParticlesEffect, FlowFieldEffect)
    for cls in (PlasmaFieldEffect, BeatParticlesEffect, FlowFieldEffect):
        vis, field = cls()._render(120, 160, _fake_sample(0.9))
        assert vis.shape == (120, 160, 3) and vis.dtype == np.uint8
        assert field.shape == (120, 160)


def test_alchemy_renderer_nonempty():
    from vpc.effects.visualizer.alchemy import AlchemyEffect
    fx = AlchemyEffect()
    vis, field = fx._render(120, 160, _fake_sample(0.9))
    assert vis.shape == (120, 160, 3) and vis.dtype == np.uint8
    assert field.shape == (120, 160)
    assert vis.max() > 0


def test_visualizer_package_exports_eight():
    import vpc.effects.visualizer as v
    assert len(v.__all__) == 8
    assert 'AlchemyEffect' in v.__all__


def test_visualizer_full_apply_pipeline():
    """End-to-end: рендерер применён к реальному кадру через BaseEffect.apply()."""
    from vpc.analyzer import Segment, SegmentType
    from vpc.effects.visualizer.spectrum import SpectrumBarsEffect
    frame = np.full((90, 120, 3), 30, np.uint8)
    seg = Segment(0, 1, 1, SegmentType.SUSTAIN, 0.8, 0.1, 0.1, 0.0)
    seg.live = _fake_sample(0.9)
    fx = SpectrumBarsEffect(enabled=True, chance=1.0, mode='replace')
    out = fx.apply(frame, seg, draft=False)
    assert out.shape == frame.shape and out.dtype == np.uint8
    assert out.max() > 0


# реестр
def test_registry_has_wmp_group():
    from vpc.registry import GROUP_ORDER, GROUP_DISPLAY_NAMES, EFFECTS
    assert 'VISUALIZER' in GROUP_ORDER
    assert GROUP_DISPLAY_NAMES['VISUALIZER'] == 'WINDOWS MEDIA PLAYER'
    viz = [e for e in EFFECTS if e.group == 'VISUALIZER']
    assert len(viz) == 8
    assert 'viz_alchemy' in {e.id for e in viz}
    assert all(e.enable_key.startswith('fx_viz_') for e in viz)
    assert all(e.enabled_default is False for e in viz)


def test_registry_builds_chain_with_visualizers():
    from vpc.registry import default_cfg, build_chain
    cfg = default_cfg()
    cfg['fx_viz_bars'] = True
    chain = build_chain(cfg)
    assert any(type(fx).__name__ == 'SpectrumBarsEffect' for fx in chain)


def test_default_cfg_covers_all_viz_keys():
    from vpc.registry import default_cfg, EFFECTS
    cfg = default_cfg()
    for e in EFFECTS:
        if e.group == 'VISUALIZER':
            for k in e.all_keys():
                assert k in cfg, f"missing default for {k}"


def test_old_preset_loads_without_viz():
    from vpc.registry import default_cfg, build_chain
    cfg = default_cfg()
    for k in list(cfg):
        if k.startswith('fx_viz_'):
            del cfg[k]
    chain = build_chain(cfg)
    assert all('Visualizer' not in type(fx).__name__ for fx in chain)


# консистентность интенсивности
def test_visualizer_intensity_scales():
    from vpc.analyzer import Segment, SegmentType
    from vpc.effects.visualizer.spectrum import SpectrumBarsEffect
    frame = np.full((90, 120, 3), 30, np.uint8)
    seg = Segment(0, 1, 1, SegmentType.SUSTAIN, 0.8, 0.1, 0.1, 0.0)
    seg.live = _fake_sample(0.9)

    fx_zero = SpectrumBarsEffect(enabled=True, chance=1.0, mode='replace',
                                  intensity_min=0.0, intensity_max=0.0)
    result_zero = fx_zero.apply(frame, seg, draft=False)
    assert np.array_equal(result_zero, frame)

    fx_full = SpectrumBarsEffect(enabled=True, chance=1.0, mode='replace',
                                  intensity_min=0.7, intensity_max=0.7)
    result_full = fx_full.apply(frame, seg, draft=False)
    diff = int(np.abs(result_full.astype(int) - frame.astype(int)).sum())
    assert diff > 0


def test_viz_effects_have_chance_key():
    from vpc.registry import EFFECTS
    viz = {e.id: e for e in EFFECTS if e.group == 'VISUALIZER'}
    for eid, spec in viz.items():
        assert spec.chance_key == f'{spec.enable_key}_chance', eid
        assert spec.default_chance == 1.0, eid
        assert spec.chance_scaled_by_chaos is True, eid


def test_viz_trigger_types_curated():
    from vpc.analyzer import SegmentType
    from vpc.registry import find_spec
    accumulators = ('viz_lissajous', 'viz_flow', 'viz_alchemy')
    for eid in accumulators:
        spec = find_spec(eid)
        assert spec.trigger_types == [SegmentType.SUSTAIN, SegmentType.BUILD], eid

    particles = find_spec('viz_particles')
    assert particles.trigger_types == [SegmentType.IMPACT, SegmentType.DROP]

    unrestricted = ('viz_bars', 'viz_radial', 'viz_scope', 'viz_plasma')
    for eid in unrestricted:
        spec = find_spec(eid)
        assert spec.trigger_types is None, eid
