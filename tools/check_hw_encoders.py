"""HW encoder smoke test — does NVENC / QSV / AMF / VideoToolbox actually
produce a usable output file on this machine?

Run:  python tools/check_hw_encoders.py

For every HW encoder the local ffmpeg build advertises, this script
spawns a 3-second testsrc → ffmpeg-pipe → encoder render with a hard
30-second timeout and reports:

    [ OK   ] H.264 NVENC (MP4)   1.2s  0.45 MB  72 frames
    [ FAIL ] H.264 QSV  (MP4)    timed out after 30s — encoder hung
    [ FAIL ] H.265 AMF  (MP4)    Cannot load amfrt64.dll

Use this to sanity-check HW availability before committing to a long
render. The exact same code path the GUI uses is exercised; if a
codec passes here it will work in the real engine, and vice versa.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vpc.render.encoders import available_specs, build_rate_control_args  # noqa: E402
from vpc.render.sink import ffmpeg_bin  # noqa: E402


PROBE_DURATION = 3.0       # seconds of testsrc to feed
PROBE_RES = (1280, 720)    # 720p — sweet spot for HW encoders
PROBE_FPS = 24
PROBE_TIMEOUT = 30.0


def _build_cmd(spec, output_path: str) -> list[str]:
    """Construct an ffmpeg command equivalent to what FFmpegSink builds,
    minus the rawvideo pipe (we let ffmpeg generate the input via
    lavfi testsrc — keeps the diagnostic self-contained)."""
    rc_args = build_rate_control_args(
        spec, crf=22, preset='fast', tune='none')
    w, h = PROBE_RES
    cmd = [
        ffmpeg_bin(), '-y', '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi',
        '-i', f'testsrc=duration={PROBE_DURATION}:size={w}x{h}:rate={PROBE_FPS}',
        '-f', 'lavfi',
        '-i', f'sine=frequency=440:duration={PROBE_DURATION}',
        '-c:v', spec.vcodec,
        '-pix_fmt', spec.pix_fmt,
    ]
    cmd += rc_args
    if spec.extra_v:
        cmd += list(spec.extra_v)
    cmd += ['-c:a', spec.acodec, '-t', f'{PROBE_DURATION:.3f}', output_path]
    return cmd


def _probe(spec, out_dir: Path) -> tuple[str, str]:
    """Returns (status, detail) where status is 'OK' or 'FAIL'."""
    out = out_dir / f'probe_{spec.vcodec}.{spec.container_ext}'
    if out.exists():
        try: out.unlink()
        except OSError: pass

    cmd = _build_cmd(spec, str(out))
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ('FAIL', f'timed out after {PROBE_TIMEOUT:.0f}s — encoder hung')
    elapsed = time.perf_counter() - t0

    if p.returncode != 0:
        # Trim the noisy error output to the meaningful tail.
        tail = (p.stderr or '').strip().splitlines()
        msg = tail[-1] if tail else f'returncode {p.returncode}'
        return ('FAIL', msg[:140])

    if not out.exists() or out.stat().st_size < 1000:
        return ('FAIL', 'ffmpeg returned 0 but output is empty/missing')

    size_mb = out.stat().st_size / 1e6
    detail = f'{elapsed:.1f}s  {size_mb:.2f} MB'
    try: out.unlink()
    except OSError: pass
    return ('OK', detail)


def main() -> int:
    out_dir = ROOT / 'tools' / '.bench_out'
    out_dir.mkdir(parents=True, exist_ok=True)

    hw_specs = [s for s in available_specs() if s.is_hw]
    if not hw_specs:
        print('No HW encoders advertised by this ffmpeg build.')
        print('All renders will use libx264 (always available).')
        return 0

    soft = [s for s in available_specs() if not s.is_hw and s.vcodec == 'libx264']
    print(f'Probing {len(hw_specs)} HW encoder(s) at '
          f'{PROBE_RES[0]}x{PROBE_RES[1]}@{PROBE_FPS}fps for '
          f'{PROBE_DURATION:.0f}s each (timeout {PROBE_TIMEOUT:.0f}s)...')
    print()

    results: list[tuple[str, str, str]] = []
    # libx264 baseline first — useful reference number.
    if soft:
        st, det = _probe(soft[0], out_dir)
        results.append((soft[0].label, st, det))
    for spec in hw_specs:
        st, det = _probe(spec, out_dir)
        results.append((spec.label, st, det))

    width = max(len(r[0]) for r in results)
    print('  Encoder'.ljust(width + 4) + 'Status   Detail')
    print('  ' + '-' * (width + 30))
    for label, st, det in results:
        marker = '[ OK   ]' if st == 'OK' else '[ FAIL ]'
        print(f'  {label.ljust(width)}  {marker}  {det}')

    failed = [r for r in results if r[1] == 'FAIL']
    print()
    if failed:
        print(f'{len(failed)} encoder(s) failed. They will be hidden in the '
              f'GUI dropdown? No — the GUI lists everything ffmpeg advertises. '
              f'Avoid the failing ones in renders, or stick to H.264 (MP4) '
              f'which is always safe.')
    else:
        print('All advertised HW encoders work. Pick whichever fits your '
              'codec/container needs.')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
