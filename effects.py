"""Complete audio-reactive effects library."""
from abc import ABC, abstractmethod
import random
import numpy as np
import cv2
from analyzer import Segment, SegmentType
from typing import List
from opensimplex import noise2
try:
    from scipy.signal import butter, sosfilt, fftconvolve
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False


# ── BaseEffect (Task 5) ──────────────────────────────────────────────

class BaseEffect(ABC):
    trigger_types: List[SegmentType] = list(SegmentType)

    def __init__(self, enabled=True, chance=1.0, intensity_min=0.0, intensity_max=1.0):
        self.enabled = enabled
        self.chance = chance
        self.intensity_min = intensity_min
        self.intensity_max = intensity_max

    def apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray:
        if not self.enabled:
            return frame
        if seg.type not in self.trigger_types:
            return frame
        if random.random() > self.chance:
            return frame
        return self._apply(frame, seg, draft)

    def scaled_intensity(self, seg: Segment) -> float:
        return self.intensity_min + seg.intensity * (self.intensity_max - self.intensity_min)

    @abstractmethod
    def _apply(self, frame: np.ndarray, seg: Segment, draft: bool) -> np.ndarray: ...


def _ensure_uint8(frame: np.ndarray) -> np.ndarray:
    return np.clip(frame, 0, 255).astype(np.uint8)


def _reseg(seg: Segment, intensity: float) -> Segment:
    return Segment(seg.t_start, seg.t_end, seg.duration, seg.type, intensity,
                   seg.rms, seg.flatness, seg.rms_change)


# ── Task 6 ────────────────────────────────────────────────────────────

class FlashEffect(BaseEffect):
    trigger_types = [SegmentType.DROP, SegmentType.IMPACT]

    def _apply(self, frame, seg, draft):
        alpha = 0.6 + self.scaled_intensity(seg) * 0.4
        flash = np.full_like(frame, 255 if random.random() > 0.5 else 0)
        result = cv2.addWeighted(frame, 1.0 - alpha, flash, alpha, 0)
        return _ensure_uint8(result)


class GhostTrailsEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.last_frame = None

    def _apply(self, frame, seg, draft):
        alpha = self.scaled_intensity(seg)
        if self.last_frame is not None and self.last_frame.shape == frame.shape:
            result = cv2.addWeighted(frame, 1.0 - alpha, self.last_frame, alpha, 0)
        else:
            result = frame.copy()
        self.last_frame = frame.copy()
        return _ensure_uint8(result)


# ── Task 7: PixelSortEffect ──────────────────────────────────────────

class PixelSortEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.IMPACT, SegmentType.DROP]

    def __init__(self, sort_axis='luminance', **kw):
        super().__init__(**kw)
        self.sort_axis = sort_axis

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)
        strip_h = max(1, int(h * (0.05 + intensity * 0.4)))
        n_strips = 1 if draft else max(1, int(intensity * 8))

        if self.sort_axis == 'hue':
            hsv = cv2.cvtColor(result, cv2.COLOR_RGB2HSV)
            key_idx = 0
        elif self.sort_axis == 'saturation':
            hsv = cv2.cvtColor(result, cv2.COLOR_RGB2HSV)
            key_idx = 1
        else:
            hsv = None
            key_idx = None  # luminance: use true grayscale

        for _ in range(n_strips):
            y = random.randint(0, max(0, h - strip_h))
            strip = result[y:y + strip_h]
            if key_idx is not None:
                key_strip = hsv[y:y + strip_h, :, key_idx]
                col_means = key_strip.mean(axis=0)
            else:
                gray = cv2.cvtColor(strip, cv2.COLOR_RGB2GRAY)
                col_means = gray.mean(axis=0)
            order = np.argsort(col_means)
            result[y:y + strip_h] = strip[:, order]

        return _ensure_uint8(result)


# ── Task 8: DatamoshEffect ───────────────────────────────────────────

class DatamoshEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.SUSTAIN, SegmentType.IMPACT, SegmentType.DROP]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.prev_frame = None

    def apply(self, frame: np.ndarray, seg: 'Segment', draft: bool) -> np.ndarray:
        """Override to always update prev_frame, even when effect doesn't fire."""
        should_fire = (
            self.enabled and
            seg.type in self.trigger_types and
            random.random() <= self.chance
        )
        if self.prev_frame is None or self.prev_frame.shape != frame.shape:
            self.prev_frame = frame.copy()
            return frame
        if not should_fire:
            self.prev_frame = frame.copy()
            return frame
        result = self._apply(frame, seg, draft)
        return result

    def _apply(self, frame, seg, draft):
        gray_cur = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        gray_prev = cv2.cvtColor(self.prev_frame, cv2.COLOR_RGB2GRAY)
        intensity = self.scaled_intensity(seg)
        # Amplify so the effect is clearly visible (1.0 = 5x flow, minimum 2x)
        flow_mul = 2.0 + intensity * 5.0

        try:
            preset = cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST if draft else cv2.DISOPTICAL_FLOW_PRESET_FAST
            dis = cv2.DISOpticalFlow_create(preset)
            flow = dis.calc(gray_prev, gray_cur, None)
        except AttributeError:
            flow = cv2.calcOpticalFlowFarneback(
                gray_prev, gray_cur, None, 0.5, 2 if draft else 3, 15, 3, 5, 1.2, 0)

        h, w = frame.shape[:2]
        flow_scaled = flow * flow_mul
        map_x = np.float32(np.tile(np.arange(w), (h, 1)) + flow_scaled[..., 0])
        map_y = np.float32(np.tile(np.arange(h).reshape(-1, 1), (1, w)) + flow_scaled[..., 1])
        result = cv2.remap(self.prev_frame, map_x, map_y, cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REFLECT)
        self.prev_frame = frame.copy()
        return _ensure_uint8(result)


# ── Task 9: ASCIIEffect ──────────────────────────────────────────────

class ASCIIEffect(BaseEffect):
    """
    Full-frame ASCII art conversion via PIL.

    color_mode:
      'fixed'    — all chars drawn in fg_color on bg_color background
      'original' — each char colored from the average RGB of its source block
      'inverted' — same as original but colors inverted
    blend:
      0.0 = pure ASCII output
      1.0 = original frame only (ASCII invisible)
      values in between composite ASCII over original
    """
    trigger_types = [SegmentType.SUSTAIN, SegmentType.SILENCE, SegmentType.BUILD]

    # dark → light (dense characters = bright regions)
    DEFAULT_CHARSET = '@#%S?*+;:,. '

    def __init__(self, char_size=10, charset=None,
                 fg_color=(0, 255, 0), bg_color=(0, 0, 0),
                 blend=0.0, color_mode='fixed', **kw):
        super().__init__(**kw)
        self.char_size  = char_size
        self.charset    = charset or self.DEFAULT_CHARSET
        self.fg_color   = tuple(fg_color)
        self.bg_color   = tuple(bg_color)
        self.blend      = blend
        self.color_mode = color_mode   # 'fixed' | 'original' | 'inverted'
        self._pil_font  = None
        self._font_size = None

    def _get_pil_font(self, size):
        """Return a PIL ImageFont. Tries Courier/monospace, falls back to default."""
        if self._pil_font is not None and self._font_size == size:
            return self._pil_font
        from PIL import ImageFont
        candidates = [
            'cour.ttf', 'courbd.ttf',         # Windows Courier New
            'DejaVuSansMono.ttf',              # Linux
            'Menlo.ttc', 'Monaco.ttf',         # macOS
            'LiberationMono-Regular.ttf',
        ]
        font = None
        for name in candidates:
            try:
                font = ImageFont.truetype(name, size)
                break
            except (OSError, IOError):
                continue
        if font is None:
            font = ImageFont.load_default()
        self._pil_font  = font
        self._font_size = size
        return font

    def _apply(self, frame, seg, draft):
        from PIL import Image as PILImage, ImageDraw

        h, w = frame.shape[:2]
        # In draft mode, use larger blocks (faster + stylistically distinct)
        cell_h = (self.char_size * 2) if draft else self.char_size
        cell_h = max(4, cell_h)
        # Monospace chars are roughly half as wide as tall
        cell_w = max(2, cell_h // 2)

        cols = max(1, w // cell_w)
        rows = max(1, h // cell_h)

        charset = self.charset
        n = len(charset)

        # Compute grayscale for brightness mapping
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

        # PIL canvas filled with background
        canvas = PILImage.new('RGB', (w, h), self.bg_color)
        draw   = ImageDraw.Draw(canvas)
        font   = self._get_pil_font(cell_h)

        for r in range(rows):
            for c in range(cols):
                y0 = r * cell_h
                x0 = c * cell_w
                y1 = min(y0 + cell_h, h)
                x1 = min(x0 + cell_w, w)

                brightness = int(gray[y0:y1, x0:x1].mean())
                char_idx   = min(int(brightness / 256 * n), n - 1)
                ch         = charset[char_idx]

                if ch == ' ':
                    continue  # background already filled

                if self.color_mode == 'original':
                    cell_rgb = frame[y0:y1, x0:x1]
                    color = (
                        int(cell_rgb[:, :, 0].mean()),
                        int(cell_rgb[:, :, 1].mean()),
                        int(cell_rgb[:, :, 2].mean()),
                    )
                elif self.color_mode == 'inverted':
                    cell_rgb = frame[y0:y1, x0:x1]
                    color = (
                        255 - int(cell_rgb[:, :, 0].mean()),
                        255 - int(cell_rgb[:, :, 1].mean()),
                        255 - int(cell_rgb[:, :, 2].mean()),
                    )
                else:
                    color = self.fg_color

                draw.text((x0, y0), ch, fill=color, font=font)

        out = np.array(canvas)

        if self.blend > 0:
            out = cv2.addWeighted(out, 1.0 - self.blend,
                                  frame, self.blend, 0)

        return _ensure_uint8(out)


# ── Task 10: Codec Breakers ──────────────────────────────────────────

class RGBShiftEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.BUILD,
                     SegmentType.NOISE, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        shift = int(self.scaled_intensity(seg) * 20)
        result = frame.copy()
        result[:, :, 0] = np.roll(frame[:, :, 0], shift, axis=1)   # R right
        result[:, :, 2] = np.roll(frame[:, :, 2], -shift, axis=1)  # B left
        return result


class BlockGlitchEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP, SegmentType.NOISE]

    def __init__(self, block_size=16, **kw):
        super().__init__(**kw)
        self.block_size = block_size

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        n_blocks = int(self.scaled_intensity(seg) * 20)
        bs = self.block_size
        for _ in range(n_blocks):
            y = random.randint(0, max(0, h - bs))
            x = random.randint(0, max(0, w - bs))
            if random.random() < 0.5:
                sy = random.randint(0, max(0, h - bs))
                sx = random.randint(0, max(0, w - bs))
                result[y:y + bs, x:x + bs] = frame[sy:sy + bs, sx:sx + bs]
            else:
                result[y:y + bs, x:x + bs] = random.randint(0, 255)
        return result


class PixelDriftEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.IMPACT]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        max_shift = int(self.scaled_intensity(seg) * 30)
        step = 4 if draft else 1
        for row in range(0, h, step):
            n = noise2(float(row) * 0.1, seg.intensity * 100.0)
            shift = int(n * max_shift)
            result[row] = np.roll(frame[row], shift, axis=0)
        return result


class ScanLinesEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        result = frame.astype(np.float32)
        intensity = self.scaled_intensity(seg)
        n = max(2, int(8 - intensity * 6))
        darkness = 0.3 + intensity * 0.5
        result[::n] = result[::n] * (1.0 - darkness)
        return _ensure_uint8(result)


class BitcrushEffect(BaseEffect):
    trigger_types = list(SegmentType)

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        bits = max(1, int(7 - intensity * 5))
        shift = 8 - bits
        return ((frame >> shift) << shift).astype(np.uint8)


class ColorBleedEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.SUSTAIN]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        intensity = self.scaled_intensity(seg)
        channel = random.randint(0, 2)
        kernel_w = max(3, int(intensity * 40))
        if kernel_w % 2 == 0:
            kernel_w += 1
        result[:, :, channel] = cv2.blur(result[:, :, channel], (kernel_w, 1))
        return result


class FreezeCorruptEffect(BaseEffect):
    trigger_types = [SegmentType.DROP]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._held = None
        self._hold_count = 0
        self._glitch = BlockGlitchEffect(enabled=True, chance=1.0)

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        hold_frames = max(1, int(intensity * 6))
        if self._held is None or self._hold_count >= hold_frames:
            self._held = frame.copy()
            self._hold_count = 0
        self._hold_count += 1
        return self._glitch._apply(self._held, seg, draft)


class NegativeEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        return (255 - frame).astype(np.uint8)


# ── Task 11: Degradation Effects ─────────────────────────────────────

class JPEGCrushEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        quality = max(1, int(40 - intensity * 38))
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        _, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        result = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
        return _ensure_uint8(result)


class FisheyeEffect(BaseEffect):
    trigger_types = [SegmentType.BUILD, SegmentType.SUSTAIN]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        strength = intensity * 0.8
        K = np.array([[w, 0, w / 2.0],
                       [0, w, h / 2.0],
                       [0, 0, 1.0]], dtype=np.float64)
        D = np.array([[strength], [strength * 0.3], [0.0], [0.0]], dtype=np.float64)
        try:
            map1, map2 = cv2.fisheye.initUndistortRectifyMap(
                K, D, np.eye(3), K, (w, h), cv2.CV_32FC1)
            result = cv2.remap(frame, map1, map2, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REFLECT)
        except cv2.error:
            # Fallback: simple barrel distortion via remap
            cx, cy = w / 2.0, h / 2.0
            xs = np.linspace(-1, 1, w)
            ys = np.linspace(-1, 1, h)
            xg, yg = np.meshgrid(xs, ys)
            r2 = xg ** 2 + yg ** 2
            factor = 1.0 + strength * r2
            map_x = ((xg * factor + 1) / 2 * w).astype(np.float32)
            map_y = ((yg * factor + 1) / 2 * h).astype(np.float32)
            result = cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REFLECT)
        return _ensure_uint8(result)


class VHSTrackingEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)
        n_strips = max(1, int(intensity * 8))
        noise_amp = int(intensity * 20)
        strip_h = h // max(1, n_strips)
        for i in range(n_strips):
            y = random.randint(0, max(0, h - strip_h))
            shift = int(noise2(float(i) * 0.5, intensity * 50.0) * noise_amp)
            result[y:y + strip_h] = np.roll(result[y:y + strip_h], shift, axis=1)
            # luminance noise
            noise_val = np.clip(
                np.array([noise2(float(x) * 0.1, float(y) * 0.1) * noise_amp
                          for x in range(w)]),
                -30, 30).astype(np.int16)
            for row in range(y, min(y + strip_h, h)):
                result[row] = np.clip(
                    result[row].astype(np.int16) + noise_val.reshape(-1, 1), 0, 255
                ).astype(np.uint8)
        return result


class InterlaceEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.prev_frame = None

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        if self.prev_frame is not None and self.prev_frame.shape == frame.shape:
            result[1::2] = self.prev_frame[1::2]
        self.prev_frame = frame.copy()
        return result


class BadSignalEffect(BaseEffect):
    trigger_types = [SegmentType.DROP, SegmentType.NOISE]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)
        # Vertical noise bars
        n_bars = int(intensity * 5)
        for _ in range(n_bars):
            x = random.randint(0, w - 1)
            bw = random.randint(1, 4)
            val = random.randint(0, 255)
            result[:, x:min(x + bw, w)] = val
        # Row shifts
        n_shift = int(intensity * h * 0.1)
        for _ in range(n_shift):
            row = random.randint(0, h - 1)
            shift = random.randint(-20, 20)
            result[row] = np.roll(result[row], shift, axis=0)
        return result


class DitheringEffect(BaseEffect):
    trigger_types = [SegmentType.SILENCE, SegmentType.SUSTAIN]

    BAYER_4X4 = np.array([
        [0, 8, 2, 10],
        [12, 4, 14, 6],
        [3, 11, 1, 9],
        [15, 7, 13, 5]
    ], dtype=np.float32) / 16.0

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        levels = max(2, int(16 - intensity * 12))
        h, w = frame.shape[:2]
        # Tile bayer matrix
        tile_r = (h + 3) // 4
        tile_c = (w + 3) // 4
        bayer = np.tile(self.BAYER_4X4, (tile_r, tile_c))[:h, :w]
        bayer3 = np.stack([bayer] * 3, axis=-1)
        # Quantize
        normalized = frame.astype(np.float32) / 255.0
        step = 1.0 / levels
        dithered = normalized + (bayer3 - 0.5) * step
        quantized = np.floor(dithered * levels) / levels
        return _ensure_uint8(quantized * 255.0)


class ZoomGlitchEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        zoom = 1.0 + intensity * 0.4
        cw, ch = int(w / zoom), int(h / zoom)
        x1 = (w - cw) // 2
        y1 = (h - ch) // 2
        cropped = frame[y1:y1 + ch, x1:x1 + cw]
        result = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_NEAREST)
        return _ensure_uint8(result)


# ── Task 12: Complex Effects ─────────────────────────────────────────

class FeedbackLoopEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.accumulated = None

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        weight = intensity * 0.7
        if seg.type == SegmentType.IMPACT:
            self.accumulated = None
        if self.accumulated is None or self.accumulated.shape != frame.shape:
            self.accumulated = frame.astype(np.float32)
            return frame.copy()
        self.accumulated = frame.astype(np.float32) * (1 - weight) + self.accumulated * weight
        return _ensure_uint8(self.accumulated)


class PhaseShiftEffect(BaseEffect):
    trigger_types = [SegmentType.NOISE, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        result = frame.copy()
        h, w = result.shape[:2]
        intensity = self.scaled_intensity(seg)
        band_h = max(4, int(h * 0.05))
        shift = int(intensity * w * 0.2)
        for y in range(0, h, band_h):
            band_idx = y // band_h
            s = shift if band_idx % 2 == 0 else -shift
            end = min(y + band_h, h)
            result[y:end] = np.roll(result[y:end], s, axis=1)
        return result


class MosaicPulseEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.BUILD]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        block = max(2, int(4 + intensity * 40))
        small = cv2.resize(frame, (max(1, w // block), max(1, h // block)),
                           interpolation=cv2.INTER_NEAREST)
        result = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        return _ensure_uint8(result)


class EchoCompoundEffect(BaseEffect):
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD]

    def __init__(self, echo_n=8, **kw):
        super().__init__(**kw)
        self.echo_n = echo_n
        self.history = []

    def _apply(self, frame, seg, draft):
        self.history.append(frame.copy())
        max_len = self.echo_n * 2 + 1
        if len(self.history) > max_len:
            self.history = self.history[-max_len:]

        result = frame.astype(np.float32) * 0.5
        n = self.echo_n
        # blend frame N-ago
        if len(self.history) > n:
            past1 = self.history[-(n + 1)]
            result += past1.astype(np.float32) * 0.3
        else:
            result += frame.astype(np.float32) * 0.3
        # blend frame 2N-ago with hue shift
        if len(self.history) > 2 * n:
            past2 = self.history[-(2 * n + 1)]
            # hue shift by 60
            hsv = cv2.cvtColor(past2, cv2.COLOR_RGB2HSV)
            hsv[:, :, 0] = (hsv[:, :, 0].astype(np.int16) + 30) % 180  # 60° = 30 in OpenCV
            past2_shifted = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
            result += past2_shifted.astype(np.float32) * 0.2
        else:
            result += frame.astype(np.float32) * 0.2
        return _ensure_uint8(result)


class KaliMirrorEffect(BaseEffect):
    trigger_types = [SegmentType.BUILD, SegmentType.SUSTAIN]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        # Mirror horizontally
        mirrored = np.hstack([frame, frame[:, ::-1]])
        # Vstack with inverted
        full = np.vstack([mirrored, 255 - mirrored])
        # Rotate
        angle = intensity * 180.0
        fh, fw = full.shape[:2]
        M = cv2.getRotationMatrix2D((fw / 2, fh / 2), angle, 1.0)
        rotated = cv2.warpAffine(full, M, (fw, fh), borderMode=cv2.BORDER_REFLECT)
        # Crop center back to original size
        cy, cx = fh // 2, fw // 2
        result = rotated[cy - h // 2:cy - h // 2 + h, cx - w // 2:cx - w // 2 + w]
        return _ensure_uint8(result)


class GlitchCascadeEffect(BaseEffect):
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP, SegmentType.NOISE]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.pool = [
            RGBShiftEffect(enabled=True, chance=1.0),
            BlockGlitchEffect(enabled=True, chance=1.0),
            PixelDriftEffect(enabled=True, chance=1.0),
            BitcrushEffect(enabled=True, chance=1.0),
        ]

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        n = max(1, int(intensity * len(self.pool)))
        chosen = random.sample(self.pool, min(n, len(self.pool)))
        result = frame.copy()
        for fx in chosen:
            fx.trigger_types = list(SegmentType)
            result = fx._apply(result, seg, draft)
        return _ensure_uint8(result)


# ── Signal Domain Effects ─────────────────────────────────────────────


def _match_histograms(src: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Match src histogram to ref per channel using CDF interpolation. Pure numpy."""
    result = np.empty_like(src)
    for c in range(3):
        s = src[:, :, c].flatten()
        r = ref[:, :, c].flatten()
        s_vals, s_idx, s_cnt = np.unique(s, return_inverse=True, return_counts=True)
        r_vals, r_cnt = np.unique(r, return_counts=True)
        s_cdf = np.cumsum(s_cnt).astype(np.float64)
        s_cdf /= s_cdf[-1]
        r_cdf = np.cumsum(r_cnt).astype(np.float64)
        r_cdf /= r_cdf[-1]
        mapped = np.interp(s_cdf, r_cdf, r_vals.astype(np.float64))
        result[:, :, c] = mapped[s_idx].reshape(src.shape[:2]).astype(np.uint8)
    return result


class ResonantRowsEffect(BaseEffect):
    """IIR bandpass filter along pixel rows — creates spatial resonance / ringing at edges."""
    trigger_types = [SegmentType.NOISE, SegmentType.DROP, SegmentType.IMPACT]

    def __init__(self, cutoff=0.08, q=12.0, **kw):
        super().__init__(**kw)
        self.cutoff = cutoff   # normalised freq 0.01–0.3
        self.q = q             # quality factor 2–30

    def _apply(self, frame, seg, draft):
        if not _SCIPY_OK:
            return frame
        intensity = self.scaled_intensity(seg)
        freq = float(np.clip(self.cutoff, 0.01, 0.45))
        low  = max(0.001, freq * 0.7)
        high = min(0.499, freq * 1.3)
        try:
            sos = butter(2, [low, high], btype='bandpass', fs=1.0, output='sos')
        except Exception:
            return frame
        result = frame.astype(np.float32)
        step = 2 if draft else 1
        scale = float(self.q) * 0.15 * intensity
        for c in range(3):
            for y in range(0, frame.shape[0], step):
                ringing = sosfilt(sos, result[y, :, c])
                result[y, :, c] += ringing * scale
                if draft and y + 1 < frame.shape[0]:
                    result[y + 1, :, c] = result[y, :, c]
        return _ensure_uint8(result)


class TemporalRGBEffect(BaseEffect):
    """R/G/B channels read from different time-offset frames — chromatic temporal aberration."""
    trigger_types = list(SegmentType)

    def __init__(self, lag=8, **kw):
        super().__init__(**kw)
        self.lag = lag   # max frame lag 2–20
        self._history: list = []

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        lag_g = max(1, int(self.lag * intensity * 0.5))
        lag_b = max(1, int(self.lag * intensity))

        self._history.append(frame.copy())
        max_len = self.lag * 2 + 4
        if len(self._history) > max_len:
            self._history.pop(0)

        n = len(self._history)
        r = frame[:, :, 0]
        g_src = self._history[-min(lag_g + 1, n)]
        b_src = self._history[-min(lag_b + 1, n)]
        if g_src.shape != frame.shape:
            g_src = frame
        if b_src.shape != frame.shape:
            b_src = frame
        g = g_src[:, :, 1]
        b = b_src[:, :, 2]
        return np.stack([r, g, b], axis=2).astype(np.uint8)


class FFTPhaseCorruptEffect(BaseEffect):
    """Corrupt 2-D FFT phase while preserving magnitude — wave-like spatial scrambling."""
    trigger_types = [SegmentType.NOISE, SegmentType.DROP, SegmentType.IMPACT, SegmentType.BUILD]

    def __init__(self, amount=0.5, **kw):
        super().__init__(**kw)
        self.amount = amount   # 0.1–1.0

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        noise_scale = float(self.amount) * intensity * np.pi
        result = np.zeros_like(frame)
        if draft:
            small = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2))
            for c in range(3):
                ch = small[:, :, c].astype(np.float32)
                F = np.fft.rfft2(ch)
                phase_noise = np.random.uniform(-noise_scale, noise_scale, F.shape)
                F2 = np.abs(F) * np.exp(1j * (np.angle(F) + phase_noise))
                rec = np.clip(np.fft.irfft2(F2, s=ch.shape), 0, 255).astype(np.uint8)
                result[:, :, c] = cv2.resize(rec, (frame.shape[1], frame.shape[0]))
        else:
            for c in range(3):
                ch = frame[:, :, c].astype(np.float32)
                F = np.fft.rfft2(ch)
                phase_noise = np.random.uniform(-noise_scale, noise_scale, F.shape)
                F2 = np.abs(F) * np.exp(1j * (np.angle(F) + phase_noise))
                result[:, :, c] = np.clip(np.fft.irfft2(F2, s=ch.shape), 0, 255).astype(np.uint8)
        return result


class WaveshaperEffect(BaseEffect):
    """Tube-saturation waveshaper applied to pixel values — soft-clip colour distortion."""
    trigger_types = list(SegmentType)

    def __init__(self, drive=3.0, **kw):
        super().__init__(**kw)
        self.drive = drive   # 0.5–8.0

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        d = float(1.0 + (self.drive - 1.0) * intensity)
        d = max(0.1, d)
        f = frame.astype(np.float32) / 127.5 - 1.0
        saturated = np.tanh(f * d) / np.tanh(min(d, 50.0))
        return _ensure_uint8((saturated + 1.0) * 127.5)


class HistoLagEffect(BaseEffect):
    """Match current frame histogram to a past frame — palette time-lag."""
    trigger_types = list(SegmentType)

    def __init__(self, lag_frames=30, **kw):
        super().__init__(**kw)
        self.lag_frames = lag_frames   # 5–90
        self._history: list = []

    def _apply(self, frame, seg, draft):
        self._history.append(frame.copy())
        max_len = self.lag_frames + 2
        if len(self._history) > max_len:
            self._history.pop(0)
        ref = self._history[0]
        if ref.shape != frame.shape:
            return frame
        if draft:
            dw, dh = max(1, frame.shape[1] // 2), max(1, frame.shape[0] // 2)
            s = cv2.resize(frame, (dw, dh))
            r = cv2.resize(ref,   (dw, dh))
            matched = _match_histograms(s, r)
            return cv2.resize(matched, (frame.shape[1], frame.shape[0]))
        return _match_histograms(frame, ref)


class WrongSubsamplingEffect(BaseEffect):
    """4:1:N chroma subsampling abuse — large colour blocks bleed over sharp luminance."""
    trigger_types = list(SegmentType)

    def __init__(self, factor=4, **kw):
        super().__init__(**kw)
        self.factor = factor   # 2–8

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        factor = max(2, int(2 + (float(self.factor) - 2.0) * intensity))
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        yuv = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
        h, w = yuv.shape[:2]
        y_ch, cr, cb = yuv[:, :, 0], yuv[:, :, 1], yuv[:, :, 2]
        sw, sh = max(1, w // factor), max(1, h // factor)
        cr_s = cv2.resize(cr, (sw, sh), interpolation=cv2.INTER_AREA)
        cb_s = cv2.resize(cb, (sw, sh), interpolation=cv2.INTER_AREA)
        cr_u = cv2.resize(cr_s, (w, h), interpolation=cv2.INTER_NEAREST)
        cb_u = cv2.resize(cb_s, (w, h), interpolation=cv2.INTER_NEAREST)
        yuv_out = cv2.merge([y_ch, cr_u, cb_u])
        return cv2.cvtColor(cv2.cvtColor(yuv_out, cv2.COLOR_YCrCb2BGR), cv2.COLOR_BGR2RGB)


class GameOfLifeEffect(BaseEffect):
    """Conway Game of Life run on binarised frame — organic corruption mask."""
    trigger_types = [SegmentType.NOISE, SegmentType.IMPACT, SegmentType.DROP]

    def __init__(self, iterations=2, corrupt_strength=60, **kw):
        super().__init__(**kw)
        self.iterations = iterations         # 1–5
        self.corrupt_strength = corrupt_strength

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        iters = max(1, int(self.iterations * intensity))
        strength = max(1, int(self.corrupt_strength * intensity))

        scale = 4 if draft else 2
        small = cv2.resize(frame, (max(1, frame.shape[1] // scale),
                                   max(1, frame.shape[0] // scale)))
        gray = (cv2.cvtColor(small, cv2.COLOR_RGB2GRAY) > 128).astype(np.uint8)

        for _ in range(iters):
            neighbors = sum(
                np.roll(np.roll(gray, dy, 0), dx, 1)
                for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if (dy != 0 or dx != 0)
            )
            gray = ((neighbors == 3) | ((gray == 1) & (neighbors == 2))).astype(np.uint8)

        mask = cv2.resize(gray.astype(np.uint8) * 255,
                          (frame.shape[1], frame.shape[0]),
                          interpolation=cv2.INTER_NEAREST)
        mask3 = (mask[:, :, np.newaxis] > 127)
        noise = np.random.randint(0, strength, frame.shape, dtype=np.uint8)
        return np.where(mask3, np.bitwise_xor(frame, noise), frame).astype(np.uint8)


class ELAEffect(BaseEffect):
    """Error Level Analysis as visual layer — exposes JPEG compression boundaries."""
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD, SegmentType.NOISE]

    def __init__(self, quality=75, amplify=12, blend=0.5, **kw):
        super().__init__(**kw)
        self.quality = quality
        self.amplify = amplify
        self.blend = blend

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        amp = int(5 + self.amplify * intensity)
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        _, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        compressed = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if compressed is None:
            return frame
        diff = cv2.absdiff(bgr, compressed).astype(np.float32) * amp
        ela = cv2.cvtColor(np.clip(diff, 0, 255).astype(np.uint8), cv2.COLOR_BGR2RGB)
        alpha = float(np.clip(self.blend + (1.0 - self.blend) * (1.0 - intensity), 0.0, 1.0))
        return _ensure_uint8(cv2.addWeighted(frame.astype(np.float32), alpha,
                                              ela.astype(np.float32), 1.0 - alpha, 0))


class DtypeReinterpretEffect(BaseEffect):
    """Reinterpret frame bytes as float16, add noise, view back as uint8 — VRAM-corruption look."""
    trigger_types = [SegmentType.IMPACT, SegmentType.DROP, SegmentType.NOISE]

    def __init__(self, amount=0.05, **kw):
        super().__init__(**kw)
        self.amount = amount   # 0.01–0.4

    def _apply(self, frame, seg, draft):
        intensity = self.scaled_intensity(seg)
        scale = float(self.amount) * intensity
        raw = frame.tobytes()
        n = len(raw) // 2
        as_f16 = np.frombuffer(raw[:n * 2], dtype=np.float16).copy()
        finite = np.isfinite(as_f16)
        as_f16[finite] += (np.random.randn(int(finite.sum())) * scale).astype(np.float16)
        result_bytes = as_f16.tobytes()
        needed = frame.nbytes
        if len(result_bytes) < needed:
            result_bytes = result_bytes + b'\x00' * (needed - len(result_bytes))
        return np.frombuffer(result_bytes[:needed], dtype=np.uint8).reshape(frame.shape).copy()


class SpatialReverbEffect(BaseEffect):
    """Decaying horizontal echo along pixel rows — acoustic reverb applied to light."""
    trigger_types = [SegmentType.SUSTAIN, SegmentType.BUILD, SegmentType.DROP]

    def __init__(self, decay=0.15, reflections=6, **kw):
        super().__init__(**kw)
        self.decay = decay           # 0.05–0.45
        self.reflections = reflections

    def _apply(self, frame, seg, draft):
        if not _SCIPY_OK:
            return frame
        intensity = self.scaled_intensity(seg)
        decay = float(self.decay) * intensity

        ir_len = min(frame.shape[1] // 3, 256)
        if ir_len < 1:
            return frame
        ir = np.zeros(ir_len, dtype=np.float32)
        ir[0] = 1.0
        for k in range(1, self.reflections + 1):
            pos = min(int(ir_len * k / (self.reflections + 1)), ir_len - 1)
            ir[pos] = float((1.0 - decay) ** k) * decay * 2.0

        result = frame.astype(np.float32)
        step = 2 if draft else 1
        for c in range(3):
            for y in range(0, frame.shape[0], step):
                result[y, :, c] += fftconvolve(result[y, :, c], ir, mode='same') * intensity
                if draft and y + 1 < frame.shape[0]:
                    result[y + 1, :, c] = result[y, :, c]
        return _ensure_uint8(result)


# ── New Effects: Derivative Warp, Vortex, Fractal Noise, Self-Displacement ───


class DerivWarpEffect(BaseEffect):
    """
    Derivative-based displacement warp — closest CPU analogue to datamosh.

    Computes a Sobel gradient of the *previous* frame and uses it as a motion
    vector field to displace the *current* frame.  High-contrast edges in the
    past frame pull current pixels toward or away from them, creating organic
    smear artifacts that look like motion-vector corruption without optical flow.

    blend controls how much of the previous frame bleeds through (ghosting).
    At full intensity + high blend the image disintegrates into flowing smears.
    """
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE,
                     SegmentType.DROP, SegmentType.SUSTAIN]

    def __init__(self, blend=0.35, **kw):
        super().__init__(**kw)
        self.blend = blend          # 0.0–0.6: how much prev frame ghosts through
        self._prev: np.ndarray = None

    def apply(self, frame, seg, draft):
        """Always update _prev, even when effect does not fire."""
        result = super().apply(frame, seg, draft)
        if result is frame:          # effect did not fire — still update history
            self._prev = frame.copy()
        return result

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)

        if self._prev is None or self._prev.shape != frame.shape:
            self._prev = frame.copy()
            return frame

        # Sobel gradient of previous frame (grey)
        gray = cv2.cvtColor(self._prev, cv2.COLOR_RGB2GRAY).astype(np.float32)
        scale_f = 2 if draft else 1
        if draft:
            gray = cv2.resize(gray, (w // scale_f, h // scale_f))

        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)

        if draft:
            gx = cv2.resize(gx, (w, h))
            gy = cv2.resize(gy, (w, h))

        mag = np.sqrt(gx ** 2 + gy ** 2)
        max_mag = float(mag.max()) + 1e-6
        disp_scale = intensity * 40.0
        dx = (gx / max_mag) * disp_scale
        dy = (gy / max_mag) * disp_scale

        # Build remap coords
        xs = np.tile(np.arange(w, dtype=np.float32), (h, 1))
        ys = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w))
        map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)

        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        warped = cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_REFLECT)

        # Blend in prev for temporal smear
        blend = min(0.9, self.blend * (0.5 + intensity))
        if blend > 0:
            prev_disp = cv2.remap(self._prev, map_x * 0.4, map_y * 0.4,
                                  interp, borderMode=cv2.BORDER_REFLECT)
            warped = cv2.addWeighted(warped, 1.0 - blend, prev_disp, blend, 0)

        self._prev = frame.copy()
        return _ensure_uint8(warped)


class VortexWarpEffect(BaseEffect):
    """
    Vortex / spiral distortion — pixels rotate around the frame centre with
    angle proportional to a Gaussian falloff from the centre.

    At low intensity: subtle swirl. At high intensity: complete spiral collapse.
    Works per-pixel via cv2.remap — no Python loops, fully vectorised.
    """
    trigger_types = [SegmentType.BUILD, SegmentType.IMPACT,
                     SegmentType.SUSTAIN, SegmentType.DROP]

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)

        cx, cy = w * 0.5, h * 0.5
        sigma  = min(w, h) * 0.35          # Gaussian spread ≈ 35% of shorter side

        # Pixel coordinates relative to centre
        xs = (np.tile(np.arange(w, dtype=np.float32), (h, 1)) - cx)
        ys = (np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w)) - cy)

        r_sq = xs ** 2 + ys ** 2
        angle = intensity * 5.0 * np.exp(-r_sq / (2.0 * sigma ** 2))

        cos_a = np.cos(angle).astype(np.float32)
        sin_a = np.sin(angle).astype(np.float32)

        map_x = np.clip(xs * cos_a - ys * sin_a + cx, 0, w - 1).astype(np.float32)
        map_y = np.clip(xs * sin_a + ys * cos_a + cy, 0, h - 1).astype(np.float32)

        if draft:
            # Downscale maps for speed
            mh, mw = h // 2, w // 2
            map_xd = cv2.resize(map_x, (mw, mh)) * 0.5
            map_yd = cv2.resize(map_y, (mw, mh)) * 0.5
            small  = cv2.resize(frame, (mw, mh))
            result = cv2.remap(small, map_xd, map_yd,
                               cv2.INTER_NEAREST, borderMode=cv2.BORDER_REFLECT)
            return _ensure_uint8(cv2.resize(result, (w, h), interpolation=cv2.INTER_NEAREST))

        return _ensure_uint8(
            cv2.remap(frame, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        )


class FractalNoiseWarpEffect(BaseEffect):
    """
    Domain-warped fractal Brownian motion displacement.

    Multi-scale noise fields (4 octaves) are used as XY displacement vectors.
    Each octave is generated as Gaussian-blurred uniform noise, scaled and summed
    — exactly fBm semantics, fast and fully vectorised with numpy + cv2.

    The displacement field evolves per-segment (seeded from audio stats) so it
    changes on every cut without requiring expensive per-pixel noise evaluation.
    """
    trigger_types = list(SegmentType)

    def __init__(self, octaves=4, **kw):
        super().__init__(**kw)
        self.octaves = octaves   # 2–5

    def _make_fbm(self, h, w, seed, octaves, draft):
        """Return (dx, dy) displacement fields as float32 arrays of shape (h, w)."""
        rng = np.random.RandomState(seed)
        dx = np.zeros((h, w), dtype=np.float32)
        dy = np.zeros((h, w), dtype=np.float32)

        amp   = 1.0
        scale = 8 if draft else 4       # base coarseness (pixels per noise tile)

        for _ in range(octaves):
            nh = max(1, h // scale)
            nw = max(1, w // scale)
            nx = rng.randn(nh, nw).astype(np.float32)
            ny = rng.randn(nh, nw).astype(np.float32)
            # Bilinear upsample to full resolution
            nx_up = cv2.resize(nx, (w, h), interpolation=cv2.INTER_LINEAR)
            ny_up = cv2.resize(ny, (w, h), interpolation=cv2.INTER_LINEAR)
            dx += nx_up * amp
            dy += ny_up * amp
            scale = max(2, scale // 2)
            amp  *= 0.55

        return dx, dy

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)

        # Seed from segment content so different audio moments produce different fields
        seed = int(abs(seg.rms * 1e6 + seg.flatness * 1e4)) & 0x7FFFFFFF

        dx, dy = self._make_fbm(h, w, seed, max(2, self.octaves), draft)

        # Normalise and scale displacement
        for arr in (dx, dy):
            m = float(np.abs(arr).max()) + 1e-6
            arr /= m
        disp = intensity * 60.0        # max pixel displacement
        dx *= disp
        dy *= disp

        xs = np.tile(np.arange(w, dtype=np.float32), (h, 1))
        ys = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w))
        map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)

        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        return _ensure_uint8(
            cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_WRAP)
        )


class SelfDisplaceEffect(BaseEffect):
    """
    Self-displacement / frame-eating — uses the *previous* frame's RGB channels
    directly as XY displacement vectors for the *current* frame.

    R channel → X offset.  G channel → Y offset.
    Pixel value 128 = no displacement; <128 = negative; >128 = positive.

    The image literally uses its own past colour information to tear itself apart.
    With FeedbackLoopEffect active simultaneously the two interact recursively —
    the feedback accumulator feeds the self-displacement which feeds back into
    the accumulator — producing cascading distortion indistinguishable from
    datamosh at high chaos.

    depth controls how many frames back the displacement source is taken from.
    Multi-layer displacement (using frames at different depths) layered additively
    creates the temporal depth that makes datamosh aesthetically rich.
    """
    trigger_types = [SegmentType.IMPACT, SegmentType.NOISE,
                     SegmentType.DROP, SegmentType.BUILD, SegmentType.SUSTAIN]

    def __init__(self, depth=2, history_len=6, **kw):
        super().__init__(**kw)
        self.depth       = depth        # primary displacement source age (frames)
        self.history_len = history_len  # ring buffer size
        self._history: list = []

    def apply(self, frame, seg, draft):
        """Always append to history."""
        self._history.append(frame.copy())
        if len(self._history) > self.history_len + 1:
            self._history.pop(0)
        return super().apply(frame, seg, draft)

    def _apply(self, frame, seg, draft):
        h, w = frame.shape[:2]
        intensity = self.scaled_intensity(seg)
        n = len(self._history)

        def get_hist(age):
            idx = max(0, n - 1 - age)
            src = self._history[idx]
            if src.shape != frame.shape:
                return frame
            return src

        # Primary displacement: from depth frames ago
        d1 = get_hist(self.depth).astype(np.float32)
        # Secondary displacement: from depth*2 frames ago (adds spatial richness)
        d2 = get_hist(min(self.depth * 2, n - 1)).astype(np.float32)

        # [0,255] → [-1,+1]
        dx = ((d1[:, :, 0] - 128.0) / 128.0) * intensity * 55.0
        dy = ((d1[:, :, 1] - 128.0) / 128.0) * intensity * 55.0
        # Add secondary layer at half weight for fractal-like multi-scale effect
        dx += ((d2[:, :, 0] - 128.0) / 128.0) * intensity * 25.0
        dy += ((d2[:, :, 2] - 128.0) / 128.0) * intensity * 25.0

        xs = np.tile(np.arange(w, dtype=np.float32), (h, 1))
        ys = np.tile(np.arange(h, dtype=np.float32).reshape(-1, 1), (1, w))
        map_x = np.clip(xs + dx, 0, w - 1).astype(np.float32)
        map_y = np.clip(ys + dy, 0, h - 1).astype(np.float32)

        interp = cv2.INTER_NEAREST if draft else cv2.INTER_LINEAR
        displaced = cv2.remap(frame, map_x, map_y, interp, borderMode=cv2.BORDER_WRAP)

        # Ghost blend: mix displaced current with displaced past for smearing
        ghost_age = get_hist(1)
        ghost_map_x = np.clip(xs + dx * 0.3, 0, w - 1).astype(np.float32)
        ghost_map_y = np.clip(ys + dy * 0.3, 0, h - 1).astype(np.float32)
        ghost = cv2.remap(ghost_age, ghost_map_x, ghost_map_y,
                          interp, borderMode=cv2.BORDER_WRAP)
        blend = min(0.45, intensity * 0.4)
        result = cv2.addWeighted(displaced, 1.0 - blend, ghost, blend, 0)

        return _ensure_uint8(result)


# ── Task 13: MysterySection ──────────────────────────────────────────

class MysterySection:
    def __init__(self):
        self.VESSEL = 0.0
        self.ENTROPY_7 = 0.0
        self.DELTA_OMEGA = 0.0
        self.STATIC_MIND = 0.0
        self.RESONANCE = 0.0
        self.COLLAPSE = 0.0
        self.ZERO = 0.0
        self.FLESH_K = 0.0
        self.DOT = 0.0
        self._feedback = FeedbackLoopEffect(enabled=True, chance=1.0)
        self._ghost = GhostTrailsEffect(enabled=True, chance=1.0)
        self._scanlines = ScanLinesEffect(enabled=True, chance=1.0)
        self._colorbleed = ColorBleedEffect(enabled=True, chance=1.0)
        self._frame_buffer = []
        # Signal domain extensions
        self._resonant     = ResonantRowsEffect(enabled=True, chance=1.0)
        self._spatial_rev  = SpatialReverbEffect(enabled=True, chance=1.0)
        self._fft_phase    = FFTPhaseCorruptEffect(enabled=True, chance=1.0)
        self._temporal_rgb = TemporalRGBEffect(enabled=True, chance=1.0)
        self._histo_lag    = HistoLagEffect(enabled=True, chance=1.0)
        self._waveshaper   = WaveshaperEffect(enabled=True, chance=1.0)
        self._wrong_sub    = WrongSubsamplingEffect(enabled=True, chance=1.0)
        self._dtype_cor    = DtypeReinterpretEffect(enabled=True, chance=1.0)
        self._gameoflife   = GameOfLifeEffect(enabled=True, chance=1.0)
        self._ela          = ELAEffect(enabled=True, chance=1.0)

    def apply(self, frame, seg, draft):
        result = frame.copy()

        # ZERO seeds all probabilistic decisions for this frame.
        # Same slider values + different ZERO = completely different outcome.
        if self.ZERO > 0:
            random.seed(int(self.ZERO * 9999) ^ int(seg.rms * 1000))
            np.random.seed(random.randint(0, 2**31 - 1))

        # Cross-interaction compounds — sliders bleed into each other's behaviour
        _ve = self.VESSEL    * self.ENTROPY_7    # memory amplifies biological growth
        _rc = self.RESONANCE * self.COLLAPSE     # ring bends convergence thresholds
        _ds = self.DOT       * self.STATIC_MIND  # offset warps structure/noise ratio
        _zf = self.ZERO      * self.FLESH_K      # seed lowers the flesh threshold

        # DELTA_OMEGA scales how strongly audio amplitude pulls effect probability.
        # At 0: effects fire indifferently to loudness. At 1: loud segments dominate.
        _rg = seg.rms * (1.0 + self.DELTA_OMEGA * 3.0)

        def _gate(base, rms_w=0.0, sign=1.0):
            return random.random() < min(1.0, max(0.0, base + sign * rms_w * _rg))

        # ── FLESH_K ── analogue materiality
        # Threshold is not 0.33: ZERO lowers it, ENTROPY_7 raises it
        flesh_thr = 0.33 - _zf * 0.18 + self.ENTROPY_7 * 0.08
        if self.FLESH_K > flesh_thr and _gate(self.FLESH_K, rms_w=0.2, sign=-1.0):
            result = cv2.cvtColor(result, cv2.COLOR_RGB2YCrCb)
            # Second conversion threshold bent by memory×chaos compound
            if self.FLESH_K > 0.66 - _ve * 0.12:
                result = cv2.cvtColor(result, cv2.COLOR_YCrCb2RGB)
                result = cv2.cvtColor(result, cv2.COLOR_RGB2HSV)
                result = cv2.cvtColor(result, cv2.COLOR_HSV2RGB)
            else:
                result = cv2.cvtColor(result, cv2.COLOR_YCrCb2RGB)
            self._waveshaper.drive = 1.0 + self.FLESH_K * 7.0 + _rc * 4.0
            result = self._waveshaper._apply(result, _reseg(seg, self.FLESH_K), draft)
            self._wrong_sub.factor = 2 + int(self.FLESH_K * 6 + _ds * 4)
            result = self._wrong_sub._apply(result, _reseg(seg, self.FLESH_K), draft)

        # ── VESSEL ── temporal depth
        # DOT amplifies the lag when both are present
        ev = self.VESSEL * (1.0 + self.DOT * 0.6)
        if ev > 0 and _gate(ev, rms_w=0.1):
            self._feedback.intensity_max = ev
            self._ghost.intensity_max = max(0.0, 1.0 - ev + _ve * 0.4)
            result = self._feedback._apply(result, seg, draft)
            result = self._ghost._apply(result, seg, draft)
            self._histo_lag.lag_frames = max(2, int(ev * 60))
            result = self._histo_lag._apply(result, _reseg(seg, ev * 0.8), draft)
            split_thr = 0.5 - self.FLESH_K * 0.08
            if ev > split_thr:
                self._temporal_rgb.lag = max(1, int((ev - split_thr) * 35))
                result = self._temporal_rgb._apply(result, _reseg(seg, ev - split_thr), draft)

        # ── STATIC_MIND ── gradient warp (image deforms along its own edges)
        # Sobel gradient of the frame is used as a displacement map.
        # Strong edges pull surrounding pixels toward or away from them.
        # Smooth areas barely move; high-contrast regions tear apart.
        # Non-repeating: content-dependent, never the same twice.
        # Hidden: RESONANCE rings the deformed structure
        if self.STATIC_MIND > 0 and _gate(self.STATIC_MIND, rms_w=0.15):
            h_sm, w_sm = result.shape[:2]
            gray_sm = cv2.cvtColor(result[:, :, :3], cv2.COLOR_RGB2GRAY).astype(np.float32)
            gx = cv2.Sobel(gray_sm, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(gray_sm, cv2.CV_32F, 0, 1, ksize=3)
            max_g = max(float(np.abs(gx).max()), float(np.abs(gy).max()), 1.0)
            scale = self.STATIC_MIND * 32.0
            dx = (gx / max_g) * scale
            dy = (gy / max_g) * scale
            xm = np.tile(np.arange(w_sm, dtype=np.float32), (h_sm, 1))
            ym = np.tile(np.arange(h_sm, dtype=np.float32).reshape(-1, 1), (1, w_sm))
            x_src = np.clip(xm + dx, 0, w_sm - 1)
            y_src = np.clip(ym + dy, 0, h_sm - 1)
            interp = cv2.INTER_LINEAR if not draft else cv2.INTER_NEAREST
            result = cv2.remap(result, x_src, y_src, interp)
            ring_c = self.RESONANCE * self.STATIC_MIND
            if ring_c > 0.28 and _gate(ring_c):
                self._resonant.q = 5.0 + self.STATIC_MIND * 20.0
                self._resonant.cutoff = 0.015 + self.STATIC_MIND * 0.05
                result = self._resonant._apply(result, _reseg(seg, ring_c), draft)

        # ── RESONANCE ── frequency memory
        # ENTROPY_7 corrupts the ring quality; COLLAPSE lowers FFT phase threshold
        if self.RESONANCE > 0 and _gate(self.RESONANCE, rms_w=0.5):
            self._resonant.q = 3.0 + self.RESONANCE * 27.0 + self.ENTROPY_7 * 14.0
            self._resonant.cutoff = 0.04 + self.RESONANCE * 0.12
            result = self._resonant._apply(result, _reseg(seg, self.RESONANCE), draft)
            self._spatial_rev.decay = self.RESONANCE * 0.4 + _ve * 0.15
            result = self._spatial_rev._apply(result, _reseg(seg, self.RESONANCE), draft)
            fft_thr = 0.7 - _rc * 0.35
            if self.RESONANCE > fft_thr:
                self._fft_phase.amount = (self.RESONANCE - fft_thr) * 3.0
                result = self._fft_phase._apply(result, _reseg(seg, self.RESONANCE - fft_thr), draft)

        # ── COLLAPSE ── pixel sorting + spectral amplitude zeroing
        # Rows are pixel-sorted by luminance: similar colours pool into horizontal smear bars.
        # At higher values, FFT low-pass zeroes out high-frequency spatial information —
        # the image converges toward its own spectral average.
        # ZERO controls sort direction (ascending vs descending).
        # RESONANCE+COLLAPSE compound lowers the spectral cutoff further.
        if self.COLLAPSE > 0 and _gate(self.COLLAPSE, rms_w=0.2):
            h_c, w_c = result.shape[:2]
            n_sorted = int(h_c * self.COLLAPSE * 0.85)
            if n_sorted > 0:
                row_idx = np.random.choice(h_c, n_sorted, replace=False)
                out_c = result.copy()
                ascending = self.ZERO < 0.5
                for y in row_idx:
                    row = result[y].copy()
                    if not draft:
                        lum = (0.299 * row[:, 0].astype(float)
                               + 0.587 * row[:, 1]
                               + 0.114 * row[:, 2])
                        idx_s = np.argsort(lum)
                        out_c[y] = row[idx_s] if ascending else row[idx_s[::-1]]
                    else:
                        if random.random() < self.COLLAPSE:
                            out_c[y] = row[::-1]
                result = out_c
            fft_thr_c = 0.35 - _rc * 0.15
            if self.COLLAPSE > fft_thr_c and not draft:
                keep = max(0.08, 1.0 - (self.COLLAPSE - fft_thr_c) * 1.5 - _rc * 0.2)
                for c in range(min(3, result.shape[2])):
                    F = np.fft.fft2(result[:, :, c].astype(np.float32))
                    Fs = np.fft.fftshift(F)
                    cy2, cx2 = h_c // 2, w_c // 2
                    ry = max(1, int(cy2 * keep))
                    rx = max(1, int(cx2 * keep))
                    mask = np.zeros((h_c, w_c), dtype=np.float32)
                    mask[cy2 - ry:cy2 + ry, cx2 - rx:cx2 + rx] = 1.0
                    Fs *= mask
                    result[:, :, c] = np.clip(
                        np.abs(np.fft.ifft2(np.fft.ifftshift(Fs))), 0, 255
                    ).astype(np.uint8)

        # ── ENTROPY_7 ── biological chaos
        # VESSEL amplifies automaton iterations: accumulated past grows more life
        if self.ENTROPY_7 > 0 and _gate(self.ENTROPY_7, rms_w=0.1):
            drift = PixelDriftEffect(enabled=True, chance=1.0)
            result = drift._apply(result, _reseg(seg, self.ENTROPY_7), draft)
            self._dtype_cor.amount = self.ENTROPY_7 * 0.2 + _ve * 0.1
            result = self._dtype_cor._apply(result, _reseg(seg, self.ENTROPY_7), draft)
            base_iter = max(1, int(self.ENTROPY_7 * 4))
            if _ve > 0.12:
                base_iter = max(base_iter, int(_ve * 12))
            self._gameoflife.iterations = base_iter
            self._gameoflife.corrupt_strength = int(30 + self.ENTROPY_7 * 80)
            result = self._gameoflife._apply(result, _reseg(seg, self.ENTROPY_7), draft)

        # ── ZERO ── deterministic revelation
        # ELA probability bent by FLESH_K compound; amplification compounds too
        if self.ZERO > 0 and _gate(self.ZERO - 0.05, rms_w=-0.05):
            self._ela.amplify = int(5 + self.ZERO * 20 + _zf * 20)
            self._ela.blend = max(0.0, 1.0 - self.ZERO * 1.4 - _zf * 0.3)
            result = self._ela._apply(result, _reseg(seg, self.ZERO), draft)

        # ── DOT ── slit-scan temporal cross-section
        # Each column of the output is sampled from a different point in time.
        # Column 0 = oldest frame in buffer. Column W-1 = current frame.
        # The image becomes a spatial cross-section of its own timeline.
        # VESSEL widens the temporal window (older = more left displacement).
        # Non-repeating: depends entirely on what moved and when.
        if self.DOT > 0 and _gate(self.DOT, rms_w=0.3):
            depth = max(2, int(self.DOT * 6 + self.VESSEL * 4))
            self._frame_buffer.append(result.copy())
            if len(self._frame_buffer) > depth + 4:
                self._frame_buffer.pop(0)
            buf = self._frame_buffer
            if len(buf) >= 2:
                h_d, w_d = result.shape[:2]
                n = len(buf)
                col_to_frame = np.clip(
                    (np.arange(w_d) * (n - 1) / max(1, w_d - 1)).astype(int), 0, n - 1
                )
                out_dot = result.copy()
                for fi in range(n):
                    if buf[fi].shape != result.shape:
                        continue
                    cols = np.where(col_to_frame == fi)[0]
                    if len(cols) > 0:
                        out_dot[:, cols] = buf[fi][:, cols]
                w_slit = min(0.97, self.DOT * 1.1 + self.VESSEL * 0.15)
                result = cv2.addWeighted(out_dot, w_slit, result, 1.0 - w_slit, 0)

        random.seed()
        np.random.seed()
        return _ensure_uint8(result)

    def get_threshold_shift(self):
        return self.DELTA_OMEGA

    def get_remap_curve(self):
        return self.RESONANCE


# ── Task 14: ChromaKeyEffect + OverlayEffect ─────────────────────────

def _dominant_hue(img_rgb: np.ndarray, rank: int = 0) -> int:
    """Return OpenCV hue (0-179) of the rank-th most frequent colour cluster."""
    rgb3 = img_rgb[:, :, :3]
    hsv = cv2.cvtColor(rgb3, cv2.COLOR_RGB2HSV)
    h_ch = hsv[:, :, 0].flatten()
    s_ch = hsv[:, :, 1].flatten()
    # prefer saturated pixels to avoid greys/blacks skewing the result
    saturated = h_ch[s_ch > 40]
    source = saturated if len(saturated) > 200 else h_ch
    bins, _ = np.histogram(source, bins=18, range=(0, 180))
    order = np.argsort(bins)[::-1]
    idx = int(order[rank]) if rank < len(order) else int(order[0])
    return idx * 10 + 5  # centre of the 10-degree bin


class ChromaKeyEffect:
    def __init__(self, key_color=(0, 255, 0), tolerance=30, edge_softness=5):
        self.key_color = key_color
        self.tolerance = tolerance
        self.edge_softness = edge_softness

    @classmethod
    def from_frame(cls, img_rgb: np.ndarray, rank: int = 0,
                   tolerance: int = 30, edge_softness: int = 5) -> 'ChromaKeyEffect':
        """Build a ChromaKeyEffect targeting the rank-th most frequent hue."""
        hue = _dominant_hue(img_rgb, rank)
        key_hsv = np.uint8([[[hue, 200, 200]]])
        key_rgb = cv2.cvtColor(key_hsv, cv2.COLOR_HSV2RGB)[0, 0]
        return cls(key_color=tuple(int(x) for x in key_rgb),
                   tolerance=tolerance, edge_softness=edge_softness)

    def get_mask(self, frame):
        """Return uint8 mask: 0 = keyed out, 255 = keep."""
        hsv = cv2.cvtColor(frame[:, :, :3], cv2.COLOR_RGB2HSV)
        key_hsv = cv2.cvtColor(
            np.uint8([[list(self.key_color)]]), cv2.COLOR_RGB2HSV)[0, 0]
        h_center = int(key_hsv[0])
        lower = np.array([max(0, h_center - self.tolerance), 40, 40], dtype=np.uint8)
        upper = np.array([min(179, h_center + self.tolerance), 255, 255], dtype=np.uint8)
        keyed = cv2.inRange(hsv, lower, upper)
        mask = 255 - keyed  # 0 = keyed out, 255 = keep
        if self.edge_softness > 1:
            ks = self.edge_softness | 1
            mask = cv2.GaussianBlur(mask, (ks, ks), 0)
        return mask

    def apply_to_frame(self, frame, replacement=None):
        mask = self.get_mask(frame)
        mask3 = cv2.merge([mask, mask, mask]).astype(np.float32) / 255.0
        if replacement is None:
            replacement = np.zeros_like(frame)
        result = (frame.astype(np.float32) * mask3 +
                  replacement.astype(np.float32) * (1.0 - mask3))
        return _ensure_uint8(result)


class OverlayEffect(BaseEffect):
    """
    Composite image overlays at a scaled size and random/fixed position.

    position:  'random'        — position chosen once per segment, held for all frames
               'center'        — centred on frame
               'random_corner' — one of four corners, chosen once per segment
    scale:     fraction of frame short-side used as overlay height (0.05–1.0).
               Intensity modulates scale between scale_min and scale.
    blend_mode: 'normal' | 'screen' | 'multiply'

    The overlay decision (show/hide, frame choice, position, scale) is made
    ONCE per segment — matching the original moviepy behaviour where an overlay
    was applied to the whole clip for its full duration.  This prevents the
    per-frame flicker that occurred when the dice was re-rolled every frame.
    """
    trigger_types = list(SegmentType)

    def __init__(self, overlay_frames=None, chroma_key=None,
                 chroma_mode='none', chroma_tolerance=30, chroma_softness=5,
                 opacity=0.85, blend_mode='screen',
                 scale=0.4, scale_min=0.15,
                 position='random', **kw):
        super().__init__(**kw)
        self.overlay_frames    = overlay_frames or []
        self.chroma_key        = chroma_key       # ChromaKeyEffect for 'manual' mode
        self.chroma_mode       = chroma_mode      # 'none'|'dominant'|'secondary'|'manual'
        self.chroma_tolerance  = chroma_tolerance
        self.chroma_softness   = chroma_softness
        self.opacity           = opacity
        self.blend_mode        = blend_mode
        self.scale             = scale
        self.scale_min         = scale_min
        self.position          = position
        self._idx              = 0
        self._corner           = 0
        self._ck_cache: dict   = {}               # overlay idx → ChromaKeyEffect

        # Per-segment state — decided once when t_start changes
        self._seg_t_start: float = -1.0
        self._seg_active:  bool  = False
        self._seg_ov_idx:  int   = 0
        self._seg_x0:      int   = 0
        self._seg_y0:      int   = 0
        self._seg_tw:      int   = 0
        self._seg_th:      int   = 0

    # ---- blend helper -------------------------------------------------------
    def _blend(self, base_f, ov_f, alpha):
        """Blend ov_f over base_f with given per-pixel alpha (0–1 float)."""
        if self.blend_mode == 'screen':
            blended = 255.0 - (255.0 - base_f) * (255.0 - ov_f) / 255.0
        elif self.blend_mode == 'multiply':
            blended = base_f * ov_f / 255.0
        else:                          # normal
            blended = ov_f
        return base_f * (1.0 - alpha) + blended * alpha

    # Override apply() so the per-frame chance roll in BaseEffect is skipped —
    # the chance is consumed once per segment inside _apply() instead.
    def apply(self, frame, seg, draft):
        if not self.enabled:
            return frame
        if seg.type not in self.trigger_types:
            return frame
        return self._apply(frame, seg, draft)

    # ---- public composite (kept for backward compat) ------------------------
    def composite(self, base, overlay, opacity):
        bf = base.astype(np.float32)
        of = overlay.astype(np.float32)
        return _ensure_uint8(self._blend(bf, of, opacity))

    # ---- main apply ---------------------------------------------------------
    def _apply(self, frame, seg, draft):
        if not self.overlay_frames:
            return frame

        h, w = frame.shape[:2]

        # --- per-segment decision (original behaviour: decide once per clip) -
        if seg.t_start != self._seg_t_start:
            self._seg_t_start = seg.t_start
            # chance roll happens here, not in BaseEffect.apply, so we honour
            # the per-segment contract even when called from apply().
            self._seg_active = random.random() <= self.chance

            if self._seg_active:
                # Pick overlay frame and compute geometry once
                self._seg_ov_idx = self._idx % len(self.overlay_frames)
                self._idx += 1

                intensity = self.scaled_intensity(seg)
                cur_scale = self.scale_min + (self.scale - self.scale_min) * intensity
                cur_scale = max(0.05, min(1.0, cur_scale))

                ov_src = self.overlay_frames[self._seg_ov_idx]
                ov_h_src, ov_w_src = ov_src.shape[:2]
                th = max(4, int(h * cur_scale))
                tw = max(4, int(th * ov_w_src / max(ov_h_src, 1)))
                tw = min(tw, w)
                th = min(th, h)

                if self.position == 'center':
                    x0 = (w - tw) // 2
                    y0 = (h - th) // 2
                elif self.position == 'random_corner':
                    corners = [(0, 0), (w - tw, 0),
                               (0, h - th), (w - tw, h - th)]
                    x0, y0 = corners[self._corner % 4]
                    self._corner += 1
                else:  # 'random'
                    x0 = random.randint(0, max(0, w - tw))
                    y0 = random.randint(0, max(0, h - th))

                self._seg_x0 = max(0, min(x0, w - tw))
                self._seg_y0 = max(0, min(y0, h - th))
                self._seg_tw = tw
                self._seg_th = th

        if not self._seg_active:
            return frame

        # --- composite using the geometry decided at segment start -----------
        tw, th = self._seg_tw, self._seg_th
        x0, y0 = self._seg_x0, self._seg_y0

        ov_src = self.overlay_frames[self._seg_ov_idx]
        interp = cv2.INTER_AREA if draft else cv2.INTER_LINEAR
        ov = cv2.resize(ov_src, (tw, th), interpolation=interp)

        intensity = self.scaled_intensity(seg)
        alpha = min(1.0, self.opacity * (0.4 + intensity * 0.6))

        result = frame.copy()
        roi    = result[y0:y0 + th, x0:x0 + tw].astype(np.float32)
        ov_f   = ov[:, :, :3].astype(np.float32)

        # --- chroma key: build or retrieve cached ChromaKeyEffect ----------
        ck = None
        if self.chroma_mode == 'dominant':
            ck = self._ck_cache.get(self._seg_ov_idx)
            if ck is None:
                ck = ChromaKeyEffect.from_frame(ov_src, rank=0,
                                                tolerance=self.chroma_tolerance,
                                                edge_softness=self.chroma_softness)
                self._ck_cache[self._seg_ov_idx] = ck
        elif self.chroma_mode == 'secondary':
            ck = self._ck_cache.get(self._seg_ov_idx)
            if ck is None:
                ck = ChromaKeyEffect.from_frame(ov_src, rank=1,
                                                tolerance=self.chroma_tolerance,
                                                edge_softness=self.chroma_softness)
                self._ck_cache[self._seg_ov_idx] = ck
        elif self.chroma_mode == 'manual' and self.chroma_key is not None:
            ck = self.chroma_key

        if ck is not None:
            # Per-pixel alpha: mask (0=keyed, 255=keep) × flat opacity
            mask_src = ck.get_mask(ov_src)
            mask = cv2.resize(mask_src, (tw, th), interpolation=interp)
            per_pixel_alpha = (mask.astype(np.float32) / 255.0) * alpha
            ppa = per_pixel_alpha[:, :, np.newaxis]
            blended = self._blend(roi, ov_f, 1.0)
            blended_roi = _ensure_uint8(roi * (1.0 - ppa) + blended * ppa)
        else:
            blended_roi = _ensure_uint8(self._blend(roi, ov_f, alpha))

        result[y0:y0 + th, x0:x0 + tw] = blended_roi
        return result
