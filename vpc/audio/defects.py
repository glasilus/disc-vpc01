"""Pure functions that mangle a stereo float32 audio buffer in ways
that match specific visual effects.

Every function here takes (samples, sr) where:
  samples : np.ndarray of shape (n_samples, n_channels), dtype float32,
            values in roughly [-1.0, 1.0]
  sr      : int sample rate (Hz)

and returns a new array of the SAME shape and dtype. They never mutate
the input. This makes them trivially testable and stackable.

scipy.signal is required (already a project dependency via the SIGNAL
DOMAIN effects). On any unexpected scipy import error the affected
defect degrades to a no-op that just returns the input unchanged — the
audio pipeline never raises into the render thread.
"""
from __future__ import annotations

import numpy as np

try:
    from scipy.signal import butter, sosfilt, iircomb, lfilter
    _SCIPY_OK = True
except ImportError:                              # pragma: no cover
    _SCIPY_OK = False


# ──────────────────────────────────────────────────────────────────────
#   VHS tape — bandlimit to ~6 kHz + comb resonance + slow pitch wobble
# ──────────────────────────────────────────────────────────────────────


def defect_vhs_tape(samples: np.ndarray, sr: int) -> np.ndarray:
    """Bandlimit + comb resonance + slow LFO pitch warble.

    Three-stage pipeline:
      1. 4th-order Butterworth lowpass at 6 kHz — VHS audio HiFi tracks
         topped out there; non-HiFi linear tracks were even narrower.
      2. IIR notching comb at ~120 Hz — adds the characteristic harmonic
         resonance / phase-smear that makes consumer-grade tape audio
         sound "thin and metallic".
      3. Wow & flutter via varying-rate resampling using linear interp:
         a slow sine drives the read-position so pitch drifts ~ +/- 0.5%.
    """
    if samples.size == 0 or not _SCIPY_OK:
        return samples
    out = samples.astype(np.float32, copy=True)

    # 1. Lowpass at 6 kHz (Nyquist guard if sr is unusually low).
    nyq = sr * 0.5
    cutoff = min(6000.0, nyq * 0.9) / nyq
    sos = butter(4, cutoff, btype='lowpass', output='sos')
    for c in range(out.shape[1]):
        out[:, c] = sosfilt(sos, out[:, c])

    # 2. Comb notching at ~120 Hz with moderate Q. iircomb requires
    # sr % w0_freq == 0 for stable design, so we round to nearest divisor.
    base = 120.0
    period = max(1, int(round(sr / base)))
    w0 = sr / period / nyq
    if 0.0 < w0 < 1.0:
        try:
            b, a = iircomb(w0, 6.0, ftype='notch', fs=2.0)
            for c in range(out.shape[1]):
                out[:, c] = lfilter(b, a, out[:, c])
        except (ValueError, ZeroDivisionError):
            pass

    # 3. Wow & flutter — vary read position by a slow sine so pitch
    # wanders ~0.4%. Implemented as np.interp on a float-index timeline.
    n = out.shape[0]
    t = np.arange(n, dtype=np.float32)
    wow = np.sin(t * (2.0 * np.pi * 0.6 / sr)) * 0.004 * sr
    src_idx = np.clip(t + wow, 0, n - 1)
    src_floor = src_idx.astype(np.int32)
    src_frac = (src_idx - src_floor).astype(np.float32)
    src_next = np.minimum(src_floor + 1, n - 1)
    for c in range(out.shape[1]):
        col = out[:, c]
        out[:, c] = col[src_floor] * (1.0 - src_frac) + col[src_next] * src_frac

    return out


# ──────────────────────────────────────────────────────────────────────
#   SelfCannibalize — feedback echo (audio "eats itself")
# ──────────────────────────────────────────────────────────────────────


def defect_self_echo(samples: np.ndarray, sr: int,
                     delay_ms: float = 220.0,
                     feedback: float = 0.45) -> np.ndarray:
    """Single-tap delay with feedback. The same audio appears repeatedly
    at fading amplitude — auditory analogue of the visual recursion.

    Vectorised in chunks of `delay_n` samples. Each chunk depends ONLY
    on the chunk that finished `delay_n` samples earlier, so within a
    chunk we can do a single vector-add of the prior chunk scaled by
    feedback — no per-sample Python loop, no scipy IIR (lfilter on a
    sparse 10k-coefficient denominator is paradoxically slower than the
    naive Python loop because it traverses the whole `a` vector per
    sample). On 60 s stereo @ 44.1 kHz this is ~5 ms.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    delay_n = max(1, int(round(delay_ms * 0.001 * sr)))
    if delay_n >= n:
        return out
    fb = float(np.clip(feedback, 0.0, 0.85))
    # Walk the buffer in non-overlapping windows of `delay_n` samples.
    # Each window mixes in (fb * the immediately preceding window) which
    # has already been fully written and is therefore stable. Because
    # the source window ends exactly where the destination begins, no
    # within-window self-dependency exists, so the operation vectorises.
    pos = delay_n
    while pos < n:
        end = min(n, pos + delay_n)
        src_end = pos                      # = end - delay_n + (end-pos<delay_n offset) ⇒ trimmed below
        src_start = pos - delay_n
        actual = end - pos
        out[pos:end] += out[src_start:src_start + actual] * fb
        pos = end
    # Soft clip so heavy feedback can't blow past full-scale.
    return np.tanh(out)


# ──────────────────────────────────────────────────────────────────────
#   CursorStorm — sparse impulse "click crackle"
# ──────────────────────────────────────────────────────────────────────


def defect_cursor_clicks(samples: np.ndarray, sr: int,
                         density_per_sec: float = 12.0) -> np.ndarray:
    """Sprinkle short bipolar impulses across the track. Sounds like
    contact-crackle / pointer-click rain. Density scales with how much
    swarm the user wants visually; default is moderate.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    n_clicks = int((n / sr) * density_per_sec)
    if n_clicks <= 0:
        return out
    rng = np.random.default_rng(0x5C5C5C)
    positions = rng.integers(0, n, size=n_clicks)
    # Each click is a bipolar 4-sample blip. Amplitudes randomised to
    # avoid a metronomic feel.
    amps = (rng.random(n_clicks).astype(np.float32) * 0.45 + 0.15)
    for pos, amp in zip(positions, amps):
        end = min(n, pos + 4)
        for c in range(ch):
            out[pos:end, c] += amp * np.array([1, -1, 0.5, -0.25],
                                              dtype=np.float32)[:end - pos]
    return np.clip(out, -1.0, 1.0)


# ──────────────────────────────────────────────────────────────────────
#   BSODShred — brief white-noise bursts
# ──────────────────────────────────────────────────────────────────────


def defect_bsod_static(samples: np.ndarray, sr: int,
                       bursts_per_sec: float = 1.5,
                       burst_ms: float = 90.0) -> np.ndarray:
    """Random short bursts where the signal is REPLACED by harsh
    white noise. Mirrors the visual band-cutting: the audio is also
    "shredded" wherever a bluescreen band would land.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    n_bursts = int((n / sr) * bursts_per_sec)
    if n_bursts <= 0:
        return out
    burst_len = max(1, int(burst_ms * 0.001 * sr))
    rng = np.random.default_rng(0xB50DB50D)
    starts = rng.integers(0, max(1, n - burst_len), size=n_bursts)
    for start in starts:
        end = min(n, start + burst_len)
        # Slight per-burst gain randomisation so the bursts vary in
        # severity; otherwise they all sound identical and the effect
        # reads as a deliberate ostinato.
        gain = 0.55 + rng.random() * 0.35
        noise = rng.standard_normal(size=(end - start, ch)).astype(np.float32) * gain
        out[start:end] = noise
    return np.clip(out, -1.0, 1.0)


# ──────────────────────────────────────────────────────────────────────
#   VSyncRoll — slow pitch wobble (audio analogue of vertical roll)
# ──────────────────────────────────────────────────────────────────────


def defect_pitch_wobble(samples: np.ndarray, sr: int,
                        rate_hz: float = 0.4,
                        depth: float = 0.012) -> np.ndarray:
    """Slow LFO-driven pitch warble — a stronger version of VHS wow,
    audible as a deliberate detuning. Same underlying technique
    (vary read-position via linear interp), but ~3x deeper and ~3x
    slower so it reads as "the player is unstable" rather than tape
    grain. Pairs with the visual seam crawling vertically.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    t = np.arange(n, dtype=np.float32)
    # Position offset (in samples) follows a sine of period 1/rate_hz
    # with peak displacement ~depth*sr.
    lfo = np.sin(t * (2.0 * np.pi * rate_hz / sr)) * depth * sr
    src_idx = np.clip(t + lfo, 0, n - 1)
    floor = src_idx.astype(np.int32)
    frac = (src_idx - floor).astype(np.float32)
    nxt = np.minimum(floor + 1, n - 1)
    for c in range(ch):
        col = out[:, c]
        out[:, c] = col[floor] * (1.0 - frac) + col[nxt] * frac
    return out


# ──────────────────────────────────────────────────────────────────────
#   PFrameLag — short multi-tap "ghost reverb"
# ──────────────────────────────────────────────────────────────────────


def defect_ghost_reverb(samples: np.ndarray, sr: int) -> np.ndarray:
    """Three short delayed copies summed at decreasing amplitude — a
    poor man's early-reflection reverb. Audio "lags behind itself" the
    same way the visual decoder lags behind the source frame: the
    present is the dominant signal but the past is constantly bleeding
    in at low level. Distinct from `defect_self_echo` which uses a
    single tap with feedback (recursive); this one is a fixed-tap FIR.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    taps_ms = (35.0, 90.0, 170.0)
    gains = (0.40, 0.25, 0.15)
    accum = out.copy()
    for ms, g in zip(taps_ms, gains):
        delay_n = int(round(ms * 0.001 * sr))
        if delay_n <= 0 or delay_n >= n:
            continue
        accum[delay_n:] += out[:n - delay_n] * g
    return np.tanh(accum)


# ──────────────────────────────────────────────────────────────────────
#   BitFlip — sample-level bitcrush bursts
# ──────────────────────────────────────────────────────────────────────


def defect_bitcrush_bursts(samples: np.ndarray, sr: int,
                           bursts_per_sec: float = 4.0,
                           burst_ms: float = 60.0,
                           bits: int = 4) -> np.ndarray:
    """Random short windows are aggressively bit-crushed (quantised to
    `bits` levels). Audio "drops to 4-bit" in patches the same way the
    visual frame picks up XOR shifts in patches. Outside the bursts the
    audio is untouched, so the effect is percussive rather than a
    wash — matches the spotty visual look of bit-rot.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    n_bursts = int((n / sr) * bursts_per_sec)
    if n_bursts <= 0:
        return out
    burst_len = max(1, int(burst_ms * 0.001 * sr))
    levels = float(2 ** max(1, min(8, bits)) - 1)
    rng = np.random.default_rng(0xB17F11)
    starts = rng.integers(0, max(1, n - burst_len), size=n_bursts)
    for start in starts:
        end = min(n, start + burst_len)
        chunk = out[start:end]
        # Symmetric quantisation around 0.
        out[start:end] = np.round(chunk * levels) / levels
    return out


# ──────────────────────────────────────────────────────────────────────
#   WrongMotionVector — sample-window swaps from displaced positions
# ──────────────────────────────────────────────────────────────────────


def defect_sample_swap(samples: np.ndarray, sr: int,
                       swaps_per_sec: float = 6.0,
                       window_ms: float = 35.0) -> np.ndarray:
    """Short windows of audio are REPLACED with audio copied from a
    random other position in the track. Direct audio analogue of the
    visual macroblock displacement: the right kind of audio appears
    in the wrong place, just like the right kind of pixels appear in
    the wrong block. Distinct from sample tile/loop because the source
    position is uncorrelated with the destination.
    """
    if samples.size == 0:
        return samples
    out = samples.astype(np.float32, copy=True)
    n, ch = out.shape
    n_swaps = int((n / sr) * swaps_per_sec)
    if n_swaps <= 0:
        return out
    win_n = max(1, int(window_ms * 0.001 * sr))
    if win_n >= n:
        return out
    rng = np.random.default_rng(0x14F1DAB)
    dst = rng.integers(0, n - win_n, size=n_swaps)
    src = rng.integers(0, n - win_n, size=n_swaps)
    for d, s in zip(dst, src):
        out[d:d + win_n] = samples[s:s + win_n]
    return out
