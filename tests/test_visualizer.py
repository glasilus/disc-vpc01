"""Tests for the WINDOWS MEDIA PLAYER audio-reactive visualizer group."""
import numpy as np
import pytest


# ── helpers ───────────────────────────────────────────────────────────────
def _fake_sample(val=0.8):
    from vpc.render.reactor import AudioSample
    from vpc.analyzer import N_BINS
    return AudioSample(val, val, val, val, True, np.full(N_BINS, val, np.float32), 0.0)


# ── Task 1: analyzer feature track ─────────────────────────────────────────
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


# ── Task 2: reactor ────────────────────────────────────────────────────────
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


# ── Task 3: engine wiring ──────────────────────────────────────────────────
def test_segment_has_live_field():
    from vpc.analyzer import Segment, SegmentType
    s = Segment(0, 1, 1, SegmentType.SUSTAIN, 0.5, 0.1, 0.1, 0.0)
    assert hasattr(s, "live") and s.live is None


# ── Task 4: compositor ─────────────────────────────────────────────────────
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
    assert sample.bins.max() > 0   # modulated by intensity, non-blank


# ── Tasks 5-7: renderers ───────────────────────────────────────────────────
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


def test_visualizer_package_exports_seven():
    import vpc.effects.visualizer as v
    assert len(v.__all__) == 7


def test_visualizer_full_apply_pipeline():
    """End-to-end: a renderer applied to a real frame via the BaseEffect path."""
    from vpc.analyzer import Segment, SegmentType
    from vpc.effects.visualizer.spectrum import SpectrumBarsEffect
    frame = np.full((90, 120, 3), 30, np.uint8)
    seg = Segment(0, 1, 1, SegmentType.SUSTAIN, 0.8, 0.1, 0.1, 0.0)
    seg.live = _fake_sample(0.9)
    fx = SpectrumBarsEffect(enabled=True, chance=1.0, mode='replace')
    out = fx.apply(frame, seg, draft=False)
    assert out.shape == frame.shape and out.dtype == np.uint8
    assert out.max() > 0


# ── Task 8: registry ───────────────────────────────────────────────────────
def test_registry_has_wmp_group():
    from vpc.registry import GROUP_ORDER, GROUP_DISPLAY_NAMES, EFFECTS
    assert 'VISUALIZER' in GROUP_ORDER
    assert GROUP_DISPLAY_NAMES['VISUALIZER'] == 'WINDOWS MEDIA PLAYER'
    viz = [e for e in EFFECTS if e.group == 'VISUALIZER']
    assert len(viz) == 7
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
