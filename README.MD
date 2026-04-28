# Disc VPC 01 — User Manual

**Version:** 0.2.0
**Offline editor:** Python 3.10.x · Windows 10/11 (macOS, Ubuntu 22.* best-effort)
**Realtime (R-T):** native C++ binary · Windows 10/11 · Linux · macOS

---

## Table of Contents

- [Disc VPC 01 — User Manual](#disc-vpc-01--user-manual)
  - [Table of Contents](#table-of-contents)
  - [1. Overview](#1-overview)
  - [2. Installation](#2-installation)
    - [Offline editor (Python)](#offline-editor-python)
    - [Real-time R-T (C++)](#real-time-r-t-c)
    - [Pre-built executables](#pre-built-executables)
  - [3. Disc VPC 01 — Offline Editor](#3-disc-vpc-01--offline-editor)
    - [3.1 Interface Layout](#31-interface-layout)
    - [3.2 Loading Source Files](#32-loading-source-files)
    - [3.3 Audio Analysis Engine](#33-audio-analysis-engine)
    - [3.4 Cut Logic](#34-cut-logic)
    - [3.4a Anatomy of an Effect Block](#34a-anatomy-of-an-effect-block)
    - [3.5 Effects — Core](#35-effects--core)
    - [3.6 Effects — Glitch](#36-effects--glitch)
    - [3.7 Effects — Complex](#37-effects--complex)
    - [3.8 Effects — Degradation](#38-effects--degradation)
    - [3.9 Effects — Overlays and Chroma Key](#39-effects--overlays-and-chroma-key)
    - [Chroma Key (Green Screen)](#chroma-key-green-screen)
    - [3.10 Effects — Signal Domain](#310-effects--signal-domain)
    - [3.10b Hide Color-Altering Effects](#310b-hide-color-altering-effects)
    - [3.10c Effects — Warp](#310c-effects--warp)
    - [3.10d FORMULA Tab](#310d-formula-tab)
    - [3.11 Export Settings](#311-export-settings)
    - [3.12 Presets](#312-presets)
    - [3.13 Render Modes](#313-render-modes)
    - [3.14 Mystery Panel](#314-mystery-panel)
    - [3.15 Multiple Video Sources](#315-multiple-video-sources)
    - [3.16 Silence Treatment](#316-silence-treatment)
  - [4. Disc VPC 01 R-T — Real-Time (C++)](#4-disc-vpc-01-r-t--real-time-c)
    - [4.1 Architecture](#41-architecture)
    - [4.2 Build from source](#42-build-from-source)
    - [4.3 Interface Layout](#43-interface-layout)
    - [4.4 Loading Sources](#44-loading-sources)
    - [4.5 Audio](#45-audio)
    - [4.6 Canvas and Aspect](#46-canvas-and-aspect)
    - [4.7 Output Window (Second Display)](#47-output-window-second-display)
    - [4.8 Frame-Selection Mode](#48-frame-selection-mode)
    - [4.9 Active Video — Clip Slots](#49-active-video--clip-slots)
    - [4.10 Effects](#410-effects)
    - [4.11 Presets](#411-presets)
    - [4.12 Keyboard Shortcuts](#412-keyboard-shortcuts)
    - [4.13 Diagnostic Log](#413-diagnostic-log)
  - [5. Segment Types Reference](#5-segment-types-reference)
  - [6. Recommended Workflows](#6-recommended-workflows)
    - [Disc VPC 01 — Iterative editing](#disc-vpc-01--iterative-editing)
    - [Disc VPC 01 R-T — Live performance](#disc-vpc-01-r-t--live-performance)
  - [7. Troubleshooting](#7-troubleshooting)

---

## 1. Overview

There are two programs in this repository, and they do related jobs:

- **Disc VPC 01** *(offline editor)* — you give it an audio track and one or more source videos. It listens to the music, decides where the cuts and effects should land, and writes out a finished video file. Hands-off — start it, come back, get a clip.
- **Disc VPC 01 R-T** *(real-time, "R-T")* — same idea, but live. It listens to whatever audio your machine is hearing right now (a mic, a DJ deck, a music player) and drives video and effects on a screen as it happens. Made for VJ sets and streams.

You don't need to know any code to use either one. Both share the same internal "ear" — the part that listens to music and decides what counts as a beat, a build-up, or a drop — and the same library of visual effects.

---

## 2. Installation

> **Just want to run it?** Skip this whole section and grab a pre-built bundle from the GitHub Releases page (see *Pre-built executables* below). Building from source is only needed if you want to modify the code or there's no release for your platform.

### Offline editor (Python)

```
Python 3.10.x is required. Python 3.11+ may cause dependency errors.
```

```bash
pip install -r requirements.txt
python -m vpc
```

**Key Python dependencies:**

| Package | Purpose |
|---|---|
| `librosa` | Audio analysis, onset detection |
| `opencv-contrib-python` | Video I/O and image processing |
| `scipy` / `numpy` | Signal processing |
| `scenedetect` | Scene boundary detection |
| `opensimplex` | Noise functions for effects |
| `Pillow` | Image manipulation |
| `imageio-ffmpeg` | FFmpeg backend for video export |

### Real-time R-T (C++)

The R-T tool is a native C++ application built with CMake + vcpkg. It does not
share a runtime with the Python editor — each is independent.

```bash
# 1. Bootstrap vcpkg (one-time, anywhere on your machine)
git clone https://github.com/microsoft/vcpkg.git
./vcpkg/bootstrap-vcpkg.sh         # or .bat on Windows

# 2. Configure & build R-T
cmake -S rt_cpp -B rt_cpp/build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE=/path/to/vcpkg/scripts/buildsystems/vcpkg.cmake
cmake --build rt_cpp/build --config Release

# Windows: a static-runtime triplet is provided in rt_cpp/triplets/.
# Add  -DVCPKG_TARGET_TRIPLET=x64-windows-static-release
#      -DVCPKG_OVERLAY_TRIPLETS=rt_cpp/triplets
```

**C++ dependencies (resolved by vcpkg):**

| Package | Purpose |
|---|---|
| `glfw3` | Window + input + multi-monitor + drag-and-drop |
| `imgui` (GLFW + OpenGL3 bindings) | Control surface UI |
| `glad` | OpenGL 3.3 core loader |
| `portaudio` | Cross-platform audio capture (WASAPI / ASIO / CoreAudio / ALSA / JACK) |
| `fftw3` (single precision) | Real-time spectral analysis |
| `ffmpeg` (avcodec / avformat / swscale / swresample) | Video decoding |
| `nlohmann-json` | Preset I/O |
| `stb` | Image loading for overlays |

CI workflows in `.github/workflows/` build self-contained binaries for Windows,
macOS, and Linux on every push to `main`.

### Pre-built executables

Each release on GitHub attaches platform bundles. The Windows bundle layout:

```
Disc-VPC-01-windows/
├── DiscVPC01.exe            ← offline editor (Python build)
├── DiscVPC01-RT.exe         ← realtime (native C++)
├── presets/
│   └── rt_blank.json        ← blank R-T preset (loaded automatically)
├── README.MD                ← this document (English)
└── README_RU.MD             ← this document (Russian)
```

Run `DiscVPC01-RT.exe` directly. A `disc_vpc.log` file is written next to the
executable on every launch — see §4.13.

---

## 3. Disc VPC 01 — Offline Editor

**At a glance:** load a song, load a video (or several), pick a preset, click DRAFT to see a quick low-res preview, tweak, then click RENDER FULL VIDEO. That's the whole loop.

### 3.1 Interface Layout

The window is divided into three vertical panels:

| Panel | Content |
|---|---|
| **Left (Sidebar)** | Source file loading, presets, render buttons |
| **Center** | Tabs: EFFECTS / EXPORT / FORMULA / MYSTERY |
| **Right** | Live preview monitor, progress bar, status log |

The **EFFECTS** tab also has a **Hide color-altering effects** checkbox at the top — see §3.10b.

### 3.2 Loading Source Files

Before rendering, two files must be loaded:

**Load Audio (WAV / MP3)** — the audio track that drives the entire analysis. The engine reads this file in full before any rendering begins. Supported formats: `.wav`, `.mp3`, and any format readable by `librosa`.

**Load Source Video** — the video clip that will be cut and processed. The engine reads random or scene-detected frames from this clip to assemble the output. Any resolution is accepted; the output resolution is determined by the export settings.

A green indicator dot (●) next to each button confirms the file has been loaded successfully.

Optionally, an **overlay folder** can be loaded via the Overlays tab (see §3.9).

### 3.3 Audio Analysis Engine

Before it cuts anything, the program listens to your song from start to end and writes a kind of map: where the drum hits are, where the beat sits, which parts are loud, which parts are quiet, which parts are noisy or pure. Cuts and effects later hang off that map. You don't have to do anything to trigger this — it runs automatically the first time you press a render button.

Under the hood, this uses `librosa` and runs the following steps:

1. **Onset detection** — identifies transient attacks in the waveform (drum hits, note onsets, etc.)
2. **Beat tracking** — extracts the beat grid for snap-to-beat alignment
3. **RMS computation** — calculates root-mean-square energy for each segment
4. **Spectral flatness** — measures how "noise-like" the spectrum is (white noise = 1.0, pure tone ≈ 0)
5. **Segment classification** — assigns each segment one of six types (see §5)

This analysis runs once per session per audio file. Results are cached in memory for repeated renders.

### 3.4 Cut Logic

The **Cut Logic** section (first accordion group in the EFFECTS tab) controls how the engine selects and sequences video clips — i.e. *when* to cut and *which* frame to cut to.

---

**Smart Scene Detection** *(on/off)*

When enabled, the engine uses `PySceneDetect` to find scene boundaries in the source video before rendering. Clips are then selected starting from detected scene changes rather than random frames, producing more visually coherent transitions.

Recommended for source material with distinct visual scenes (films, footage reels). Disable for abstract or repetitive source video.

---

**Global Chaos Level** *(0.0 – 1.0)*

Controls the overall randomness of clip selection. At `0.0`, clips are chosen predictably based on scene detection or sequential order. At `1.0`, every cut selects a fully random frame regardless of scene boundaries or sequential position. Also scales the probability of most effects firing.

---

**Beat Threshold** *(0.5 – 2.0)*

The multiplier applied to the mean RMS to determine whether a segment is "loud." A segment is loud if:

```
segment_rms > rms_mean × threshold
```

Lower values → more segments classified as loud → more cuts and effects. Higher values → only the loudest peaks trigger reactions.

**Recommended starting values:**
- Quiet or dynamic material: `0.9 – 1.1`
- Dense/compressed material: `1.3 – 1.6`

---

**Transient Sensitivity** *(0.1 – 1.5)*

Controls the threshold for detecting a sharp attack (rapid RMS rise). Used to classify IMPACT segments. Lower values detect more transients; higher values only respond to very abrupt hits.

---

**Min Cut Duration (sec)** *(0.0 – 0.3)*

Segments shorter than this value are merged with the following segment and do not produce a cut. Prevents micro-cuts and flickering on dense audio. Typical useful range: `0.04 – 0.1`.

---

**Scene Buffer Size** *(2 – 30)*

The number of unique scene timestamps held in memory for random selection during high-chaos moments. A larger buffer allows more variety; a smaller buffer causes the edit to cycle through fewer scenes more rapidly.

---

**Snap Cuts to Beat Grid** *(on/off)*

When enabled, each detected onset is snapped to the nearest beat-grid position if it falls within the **Beat Snap Tolerance** window. This aligns cuts more tightly to the rhythmic pulse of the track.

**Beat Snap Tolerance** *(0.01 – 0.15 sec)* — the maximum distance an onset can be moved to snap to a beat. If the onset is farther than this from the nearest beat, it is left in place. When snap is on, the detected global BPM appears in the status log next to the segment count (`Audio: 198.4s | Segments: 412 | 142.0 BPM`).

---

### 3.4a Anatomy of an Effect Block

Every effect in the EFFECTS tab follows the same pattern. Once you understand it, all 40+ effects use the same controls.

```
┌──────────────────────────────────────────────────────────────┐
│ ☑ Effect Name  [?]                              ☐ always     │
│   Chance                              [════●═══════════] 0.45 │
│   Always-on intensity (only visible when "always" is on)     │
│                                       [════════●═════] 0.70   │
│   ─ effect-specific parameters below ─                       │
│   Param 1                             [══●══════════] 0.30   │
│   Param 2 (combo)                     [Choice ▾]             │
└──────────────────────────────────────────────────────────────┘
```

**Enable checkbox** — turns the effect on. When off, the effect is silent regardless of all other controls.

**[?] icon** — hover to read a tooltip explaining what this effect does. Tooltips are in English and Russian (separated by a divider). Almost every label, slider, and checkbox in the program has one — when in doubt, hover.

**Chance** *(0.0 – 1.0)* — probability of the effect firing when a suitable audio segment passes through. `0.0` = never, `1.0` = every eligible segment. Each effect declares a list of segment types it responds to (see §5) — even at chance `1.0` it won't fire on segments outside that list. The displayed value is the user-set chance; internally it's also scaled by **Global Chaos** (the master dial), so identical chance values produce more aggressive output at high chaos.

Effects without a chance slider (Stutter, Feedback Loop) fire on every eligible segment when enabled — they are designed to be omnipresent rather than punctual.

**always** *(checkbox)* — bypasses the segment-type triggers and the chance slider for this one effect. When checked, the effect fires on **every frame** regardless of the audio. Use this when you want a constant base treatment (e.g. permanent scanlines or fisheye) that the audio-reactive effects layer on top of.

This is per-effect: turning `always` on for *Scanlines* does not affect any other effect's audio reactivity.

**Always-on intensity** *(0.0 – 1.0)* — fixed intensity used while `always` is on. The slider is **hidden** when `always` is off (no point in showing controls that have no effect). When `always` is off, the effect uses audio-derived intensity instead.

**Effect-specific parameters** — sliders and combos beneath the standard controls. Their meaning is described in the per-effect sections below.

---

### 3.5 Effects — Core

These are the most-used visual treatments — flashes, stutters, ghost trails, pixel-sort, datamosh, ASCII. Each one has an on/off switch and, where it makes sense, a **chance** slider — that's just "how often should this fire when a suitable moment comes up." `0.0` = never. `1.0` = every time.

You don't have to understand each effect's internals — flip them on, click DRAFT, see what happens. The descriptions below are there for when you want a specific look and need to know which knob to turn.

---

**Stutter / Drill**

*Trigger: IMPACT segments only*

Repeats the first micro-segment of an impact 2, 4, or 8 times in rapid succession, creating a drill or stutter effect. There is no chance slider; the effect fires on every IMPACT if enabled.

---

**Flash Frame**

*Trigger: DROP, IMPACT*

Inserts a brief white or black frame (or a blend toward white/black) at moments of sharp energy change. The intensity of the flash scales with the segment's intensity value.

**Chance** *(0.0 – 1.0)* — probability of firing on each eligible segment.

---

**Ghost Trails**

*Trigger: SUSTAIN, BUILD*

Blends the current frame with the previous frame, creating a motion trail or ghosting effect. Suitable for slow builds and sustained sections.

**Opacity** *(0.0 – 1.0)* — how strongly the previous frame bleeds through. At `0.0` the effect is invisible; at `1.0` the image is nearly frozen.

---

**Pixel Sort**

*Trigger: NOISE, IMPACT*

Sorts columns of pixels within horizontal strips by their luminance, hue, or saturation value. Produces a characteristic glitch aesthetic that resembles codec failure or data corruption.

**Chance** *(0.0 – 1.0)* — probability per eligible segment.

**Pixel Sort Intensity** *(0.0 – 1.0)* — controls the height of the strips being sorted. At low values only thin bands are affected; at high values nearly the entire frame is sorted.

**Sort Axis** — the pixel property used as the sort key:
- `luminance` — sorts by perceived brightness (default, most legible)
- `hue` — sorts by colour angle in HSV space (more colourful result)
- `saturation` — sorts by colour purity

---

**Datamosh**

*Trigger: NOISE*

In **Draft** and **Preview** modes: simulates the P-frame bleed effect using optical flow — the previous frame's motion vectors are applied to warp the image, approximating datamosh compression artefacts.

In **Final render** mode: actual I-frame dropping is applied at the video codec level, producing authentic datamosh output.

**Chance** *(0.0 – 1.0)* — probability per eligible segment.

---

**ASCII Filter**

*Trigger: SUSTAIN*

Converts the frame to ASCII art rendered back as an image. Each character represents a block of pixels, with the character density mapping to the luminance of that block.

**Char Size (px)** *(4 – 40)* — the size of each character block in pixels. Smaller values produce finer detail; larger values give a more blocky, abstract look.

**Blend (0 – 1)** — composites the ASCII output over the original frame. At `0.0` the output is pure ASCII. At `1.0` the original frame is shown unchanged (effect invisible). Intermediate values overlay ASCII onto the source image.

**ASCII Color Mode:**
- `fixed` — all characters drawn in the configured FG color on the configured BG color
- `original` — each character takes the average color of the source pixels it represents
- `inverted` — same as `original` but with colors inverted

**ASCII FG/BG Color** — RGB sliders (0–255) for foreground and background in `fixed` mode.

---

### 3.6 Effects — Glitch

These effects simulate signal degradation, codec artefacts, and transmission errors.

| Effect | Trigger | Description |
|---|---|---|
| **RGB Shift** | IMPACT, BUILD | Shifts the red channel right and blue channel left by a pixel offset proportional to segment intensity. Produces chromatic aberration. |
| **Block Glitch** | IMPACT, DROP | Selects random rectangular blocks and either replaces them with garbage pixels or copies them from a random position. |
| **Pixel Drift** | NOISE, IMPACT | Displaces entire rows of pixels sideways by random offsets, simulating horizontal sync loss. |
| **Color Bleed / VHS Smear** | NOISE | Bleeds colour channels horizontally past edges, mimicking analog chroma delay. |
| **Freeze + Corrupt** | DROP | Freezes the current frame and applies random pixel corruption, as though the video stream has stalled. |
| **Negative** | IMPACT | Inverts all colour values (`255 - value`) for one frame. |

Each effect has a **Chance** slider controlling the probability of firing on an eligible segment.

---

### 3.7 Effects — Complex

These effects involve multi-frame state or more computationally involved transforms.

---

**Feedback Loop**

*Trigger: SUSTAIN, BUILD — fires on every eligible frame when enabled, no chance*

Blends the current frame with a decayed copy of itself accumulated over previous frames, creating a video feedback tunnel effect. The accumulation reinforces strong signals and fades quiet ones.

---

**Phase Shift** *(Chance)*

Divides the frame into horizontal bands and shifts alternating bands left and right by increasing offsets, creating a vertically layered displacement.

---

**Mosaic Pulse** *(Chance)*

Pixelates the frame by downscaling and upscaling. The block size is driven by the bass RMS value, so the pixelation pulses with low-frequency energy.

---

**Echo Compound** *(Chance)*

Composites multiple copies of the frame onto itself with hue-shifted colour offsets and slight spatial displacement, creating a layered chromatic echo.

---

**Kali Mirror** *(Chance)*

Applies a kaleidoscopic mirror transform, reflecting and rotating quadrants of the frame to create symmetric geometric patterns.

---

**Glitch Cascade** *(Chance)*

Chains several glitch effects in sequence on a single frame, compounding their results. The exact chain is randomized per frame.

---

### 3.8 Effects — Degradation

Degradation effects simulate worn or low-quality recording/playback media.

| Effect | Trigger | Description |
|---|---|---|
| **Scan Lines** | SUSTAIN, NOISE | Draws horizontal dark lines at every other row, simulating a CRT monitor. |
| **Bitcrush / Posterize** | Any | Reduces colour depth by quantizing pixel values to a smaller number of levels. |
| **JPEG Crush** | IMPACT, NOISE | Re-encodes the frame at very low JPEG quality and decodes it back, producing compression block artefacts. |
| **Fisheye / Barrel** | BUILD | Applies barrel lens distortion, bowing the image outward from the center. |
| **VHS Tracking** | NOISE, DROP | Simulates VHS tape tracking errors: horizontal displacement at the top and bottom of the frame with luminance noise. |
| **Interlace** | SUSTAIN | Splits even and odd scan lines and offsets them by a few pixels, simulating interlaced video. |
| **Bad Signal** | DROP, NOISE | Overlays digital signal breakup artefacts — horizontal stripes, colour shifts, and noise bands. |
| **Dithering** | SILENCE, SUSTAIN | Applies an ordered dither pattern, reducing the effective colour palette while adding a structured noise texture. |
| **Zoom Glitch** | IMPACT | Applies a rapid random zoom and crop for a single frame, creating a sudden jump-cut-within-a-frame pulse. |

Each effect has a **Chance** slider.

---

### 3.9 Effects — Overlays and Chroma Key

Overlays composite external image files onto video frames. Load an overlay folder via **Load Source Video → Load Overlays folder** (sidebar, below the video button), or through the Overlays tab.

**Enable Overlays** *(on/off + Chance)*

The engine selects a random image from the folder on each eligible frame and composites it onto the video.

**Opacity** *(0.0 – 1.0)* — transparency of the overlay image.

**Scale Max** *(0.05 – 1.0)* — maximum size of the overlay as a fraction of the frame dimensions.

**Scale Min** *(0.05 – 1.0)* — minimum size. The engine selects a random scale between Min and Max for each occurrence.

**Blend Mode:**
- `screen` — lightens the image (good for glows, light leaks, textures)
- `normal` — standard alpha composite over the frame
- `multiply` — darkens the image (good for grunge, film grain)

**Position:**
- `random` — the overlay is placed at a random position within the frame bounds
- `center` — the overlay is centered on the frame
- `corners` — the overlay cycles through corner positions

Supported overlay formats: `.png`, `.jpg`, `.gif` (first frame only), `.bmp`, `.mp4`, `.avi`, `.mov`, `.mkv`, `.flv`, `.wmv`, `.mpg`, `.mpeg`

Video files are loaded frame-by-frame; each frame becomes an independent overlay image in the pool.

---

### Chroma Key (Green Screen)

Removes a background colour from each overlay image before compositing, making that colour transparent.

**Mode:**
- `none` — chroma key disabled (default)
- `dominant` — automatically detects and removes the most frequent colour in the overlay (e.g. green screen)
- `secondary` — removes the second most frequent colour
- `manual` — removes the colour specified by the R/G/B sliders below

**Tolerance** *(5 – 60)* — how wide a hue range around the key colour is keyed out. Increase if green screen edges are not fully removed; decrease to avoid removing too much of the subject.

**Edge Softness** *(1 – 21)* — applies a Gaussian blur to the key mask edge, feathering the transition between keyed and non-keyed areas.

**Manual Key Color (R / G / B)** *(0 – 255 each)* — the target colour for `manual` mode. Default is pure green (0, 255, 0).

---

### 3.10 Effects — Signal Domain

The **SIGNAL DOMAIN** group is the experimental shelf — these are stranger, more abstract effects. They take ideas from audio processing, file compression, and even cellular automata, and apply them to pixels in ways those processes weren't really meant for. Results range from subtle to completely image-destroying. If you want to know exactly *why* each one looks the way it does, the descriptions below explain the underlying technique; if you just want a new look, flip them on and try.

All effects in this group are triggered by audio segment type and intensity, same as all other effects. Each has an enable checkbox, a Chance slider, and one or two parameter sliders.

---

**Resonant Rows**

Applies an IIR bandpass filter along each pixel row, treating the row as a one-dimensional audio signal. The filter creates spatial ringing at vertical edges — not blur, not sharpening, but resonance: a narrow-frequency echo around every boundary. At high Q values, the ringing overtakes the original image.

- *Freq* — centre frequency of the resonance (cycles per pixel). Lower = wider horizontal features resonate. Higher = finer detail resonates.
- *Q factor* — sharpness of the resonance peak. Low Q = gentle halo. High Q = aggressive oscillating fringe.

---

**Temporal RGB Shift**

The three colour channels are read from different points in time. Red is always the current frame. Green is N/2 frames behind. Blue is N frames behind. Moving objects cast colour-separated shadows across their own history. Stationary images are unaffected.

This is not the same as RGB Shift (which is spatial). This is chromatic temporal aberration.

- *Lag (frames)* — maximum temporal offset for the Blue channel. Scales proportionally with audio intensity.

---

**FFT Phase Corrupt**

Computes the 2D discrete Fourier transform of the frame, adds random noise to the phase of each frequency component while leaving the magnitude untouched, then transforms back. The image loses spatial coherence while preserving its overall brightness distribution — edges dissolve into wave interference patterns.

- *Phase Noise Amount* — amount of phase randomisation (0 = no effect, 1 = full random phase = maximum scramble).

---

**Waveshaper / Tube Sat**

Applies a tanh soft-clipping transfer function to pixel values, identical to tube amplifier saturation in audio production. Mid-range values compress toward the centre; peaks saturate. The result is a characteristic analogue warmth/distortion depending on drive level.

- *Drive* — gain before the clipping stage. Low = gentle saturation. High = hard, coloured distortion.

---

**Histogram Lag**

Matches the colour histogram of the current frame to the histogram of a frame from N frames ago, using per-channel cumulative distribution function remapping. The image retains its structure but borrows its colour palette from the past. When scene changes occur, the current frame takes on the colours of a completely different moment.

- *Palette Lag (frames)* — how far back in time the reference palette is drawn from.

---

**Wrong Chroma Sub (4:1:N)**

Converts the frame to YCrCb colour space, downsamples the Cr and Cb chroma channels by a large factor (2× to 8×), then upsamples them back with nearest-neighbour interpolation. The result: large, blocky colour patches overlaid on sharp luminance detail. This is the visual signature of extreme chroma subsampling — what happens when a bad codec discards too much colour information.

- *Downsample Factor* — how aggressively chroma is reduced. At 2 the effect is mild; at 8 colours appear in large rectangular blocks.

---

**Game of Life Mask**

Binarises the frame at half resolution using a luminance threshold, runs Conway's Game of Life for N iterations using the image content as the initial cell state, then uses the resulting live cells as a corruption mask — XOR-ing noise into those regions.

Brighter image regions tend to produce active cell populations. After several iterations, the pattern develops into Conway gliders, still lifes, and oscillators, creating an organic and semi-predictable corruption structure tied to the image content.

- *Iterations* — how many Game of Life generations to run per frame. More iterations = more evolved, less image-derived pattern.

---

**ELA (Error Level Analysis)**

Re-encodes the current frame as a JPEG at fixed quality, then computes the absolute difference between the original and the re-encoded version, amplified. Regions that were already heavily JPEG-compressed appear dark. Regions with fine detail, sharp edges, or recently edited content appear bright. Applied as a blend layer, this creates a forensic edge map that reveals the spatial structure of image compression.

- *ELA Blend* — 0 = pure ELA map (original image replaced by the error map), 1 = original only. Values between blend the two.

---

**Dtype Reinterpret**

Takes the raw bytes of the frame buffer and reinterprets the first half as 16-bit floating-point values. Small noise is added in the float16 domain. The bytes are then reinterpreted back as unsigned 8-bit integers and reshaped into the frame.

The corruption is not random — it is determined by the specific numerical patterns in the pixel data. Certain pixel value combinations produce NaN or Inf when interpreted as float16, which resolve to specific byte patterns (0 or 255 in uint8), creating structured bright and dark corruptions that are entirely content-dependent.

- *Noise Amount* — standard deviation of the float16-domain noise.

---

**Spatial Reverb**

Constructs a decaying impulse response (a series of diminishing echoes at fixed spatial intervals) and convolves it with each pixel row. The result is a horizontal reverberation — bright pixels produce trailing copies of themselves to the right, decaying in amplitude. This is room acoustic reverb applied to spatial light.

- *Reverb Decay* — rate at which echoes diminish. High values produce longer, louder reverb tails.

---

### 3.10b Hide Color-Altering Effects

The **EFFECTS** tab has a top-bar checkbox: **Hide color-altering effects**. When ticked, it does three things at once:

1. Force-disables the listed effects (their checkboxes go off).
2. Hides their accordion blocks entirely so they can't be re-enabled by accident while the toggle is on.
3. If `Silence Treatment` was set to `Dim`, switches it to `None` and hides the `Dim` radio button.

When you uncheck it, the previous state of every affected effect — including the `Dim` choice if it was active — is restored from a snapshot taken at the moment you ticked it. So you can flip back and forth without losing your settings.

Effects classified as "color-altering" (the ones the toggle hides):

`Flash Frame`, `RGB Shift`, `Color Bleed / VHS Smear`, `Negative`, `Bitcrush / Posterize`, `Glitch Cascade`, `Temporal RGB Shift`, `FFT Phase Corrupt`, `Waveshaper / Tube Sat`, `Dtype Reinterpret`, `ELA`, `Spatial Reverb`.

Use this when the source video's original colour grading matters (footage from a particular DP, a finished colour-corrected clip, archival material) and you want to layer cuts/glitches on top without the engine pushing the palette around. The default state is **off**.

---

### 3.10c Effects — Warp

The **WARP** group bends pixels through displacement maps. Where Glitch and Degradation effects work locally (per-pixel or per-row), Warp effects move whole regions of the image around — they're closer to optical flow than to corruption. Subtle at low intensity, image-destroying at high.

All four effects in this group support `always`-on for permanent baseline warping.

---

**Deriv Warp (gradient flow)**

*Trigger: IMPACT, NOISE, DROP, SUSTAIN*

Closest CPU analogue to true datamosh. The previous frame's spatial gradient is used as a displacement field for the current frame, so motion in the source seeds smearing in the same direction. With **Feedback Loop** also enabled, the warp accumulates and produces datamosh-grade dragging.

- *Prev Blend* *(0.0 – 0.6)* — how much of the previous frame ghosts through. `0` = pure displacement, `0.6` = heavy persistent smear.

---

**Vortex Warp (spiral)**

*Trigger: BUILD, IMPACT, SUSTAIN, DROP*

Rotates pixels around the canvas centre. Rotation angle is `intensity × gaussian_falloff(radius)`, so the centre swirls more than the edges. At low intensity it's a subtle swirl; at high intensity the entire image collapses into a spiral.

No params — strength is purely audio-driven (or fixed via the always-on intensity slider).

---

**Fractal Warp (organic)**

*Trigger: any segment type*

Builds a multi-octave Simplex noise field and uses it as XY displacement. The field is reseeded on every audio segment, so the distortion shape constantly evolves rather than locking to one pattern. Produces an organic, "melting" warp.

- *Octaves* *(2 – 5)* — number of noise scales summed. `2` = smooth blobs, `5` = jagged fractal.

---

**Self Displace (auto-warp)**

*Trigger: IMPACT, NOISE, DROP, BUILD, SUSTAIN*

A past frame's R channel becomes the X-displacement, its G channel becomes the Y-displacement. The image literally tears itself apart using its own colour values. Combined with `Feedback`, it cascades into datamosh-grade smear that reacts to scene structure rather than random noise.

- *Depth (frames back)* *(1 – 8)* — how far back the displacement source is taken from. Larger = more pronounced lag-induced tearing.

---

### 3.10d FORMULA Tab

The **FORMULA** tab (third tab in the centre panel, BSOD-blue palette) is a free-form NumPy expression effect editor. Type a formula, hit `RENDER FULL VIDEO`, the engine evaluates it on every frame.

This is a single effect — it appears in the effect chain alongside everything else — but its dedicated tab gives it more room than a normal accordion block, with a status line, snippet shortcuts, and four live parameter sliders.

**Enable checkbox + Chance + Blend** — top row.
- `enable` toggles the effect.
- `chance` (0–1) — probability per eligible frame. The formula effect listens to every segment type, so chance gates everything.
- `blend` (0–1) — composites the formula output with the original frame. `0.0` = pure formula, `1.0` = original frame visible only (effect invisible).

**Editor** — multi-line text box. Type any NumPy expression that returns an `H×W×3 uint8` array. The expression is recompiled on every keystroke; the status line below shows `OK compiled clean — formula ready` on success or the syntax error on failure. A failed compile or runtime error silently falls back to the original frame, so a typo never crashes the render — but no effect is applied.

**Live params a / b / c / d** — four sliders (0.0–1.0) you can wire into the expression. Use them when you want to "play" the formula in real time across a render — a single formula can take very different shapes at `(a=0.1, b=0.7)` vs `(a=0.9, b=0.1)`.

**Snippets** — clickable starting points. Clicking replaces the editor contents with the snippet. Available:

| Snippet | What it does |
|---|---|
| Identity | `frame` — passthrough; useful base for blends |
| Invert | `255 - frame` — colour negative |
| Pulse | brightness pulses in time with `t` |
| Channel sweep | Red channel rolls right, Blue rolls left, magnitude driven by `a · sin(t·3)` |
| Posterize | quantize colour depth, depth driven by `a` |
| Scanlines | every other row darkened by `1-a` |
| Wave | sinusoidal vertical displacement |
| Plasma | classic demoscene plasma overlay |
| Threshold | binary threshold whose level oscillates with `t` |
| Mirror | left half mirrors right half |

**Available variables in the expression namespace:**

| Name | Type | Meaning |
|---|---|---|
| `frame` | `(H, W, 3) uint8` | current input frame |
| `r`, `g`, `b` | `(H, W) uint8` | per-channel views of `frame` |
| `x`, `y` | `(H, W) float32` | pixel coordinate grids |
| `t` | `float` | segment start time in seconds |
| `i` | `float, 0..1` | current scaled intensity |
| `a`, `b`, `c`, `d` | `float, 0..1` | live slider values from this tab |
| `np`, `cv2` | modules | full NumPy and OpenCV available |
| `sin cos tan abs clip sqrt exp log pi` | aliases | ergonomic shortcuts |

The output is auto-broadcast to the frame shape and clipped to uint8. Returning a single number, a 1D array, an `H×W` array, or an `H×W×3` array all work.

**Reference example formulas:**

```python
255 - frame                                      # full invert
np.roll(frame, int(20 * np.sin(t*5)), 1)        # horizontal slide
cv2.GaussianBlur(frame, (15, 15), 0)             # blur via cv2
frame * (1 + 0.5 * a * np.sin(t*3))              # pulse driven by `a`
np.where((y % 2 == 0)[:,:,None], frame, frame//2)  # interlace
```

Saving formulas: the expression and the four `a/b/c/d` values are part of the cfg, so saving a Preset persists them. To share a formula, save it as a preset and send the user the JSON entry.

---

### 3.11 Export Settings

Located in the **EXPORT** tab.

**Frame Rate (FPS)** — `12 / 24 / 30 / 60`, default `24`. Does not affect source audio timing; only controls how many video frames are generated per second of output.

**Resolution Mode** — three policies for the output dimensions:

| Mode | Behavior |
|---|---|
| **Preset** | Pick from `240p / 360p / 480p / 720p / 1080p`. Default `720p`. |
| **Match source** | Output dimensions match the first loaded source video pixel-for-pixel. |
| **Custom** | Type any width/height in the spinboxes (`64 – 7680` × `64 – 4320`). |

In all three modes, dimensions are silently rounded down to the nearest **even** number — `yuv420p` (and ProRes 422) require chroma subsampling on even axes, and odd numbers would crash the ffmpeg pipe. So `1281 × 721 → 1280 × 720`. This includes `Match source` when the source itself has an odd dimension.

**Quality CRF** *(0 – 51, default 18)* — Constant Rate Factor for H.264 / H.265 / VP9 encoding. Lower = higher quality + larger file. `0` is lossless, `18` is visually near-lossless, `23` is the H.264 default, `28` is small-file territory, `51` is artifact art. ProRes ignores CRF (the profile flag controls quality there).

**Codec / Container** — pick a codec/container combination from the dropdown:

| Choice | Container | Video codec | Audio codec | Notes |
|---|---|---|---|---|
| **H.264 (MP4)** | `.mp4` | `libx264` | AAC | Universal compatibility. |
| **H.265 (MP4)** | `.mp4` | `libx265` (`hvc1` tag) | AAC | Smaller files, slower encode, less compatible playback. |
| **H.264 (MKV)** | `.mkv` | `libx264` | AAC | Matroska — accepts almost any codec, useful when MP4's strict moov layout is a problem. |
| **H.265 (MKV)** | `.mkv` | `libx265` | AAC | |
| **H.264 (MOV)** | `.mov` | `libx264` | AAC | QuickTime container. |
| **ProRes (MOV)** | `.mov` | `prores_ks` (profile 3 = 422 HQ) | PCM 16-bit | Editing-grade master, very large files, perfect for handing off to a DaVinci/Premiere timeline. |
| **VP9 (WebM)** | `.webm` | `libvpx-vp9` | Opus | Web/streaming-friendly. Slow encode. |

The Save dialog auto-uses the matching extension. If you type a different one, the program logs a warning and respects your choice (useful for unusual codec/container combinations).

**ffmpeg Preset** *(ultrafast / fast / medium / slow)* — encoder speed/efficiency trade-off for x264 / x265. Has no effect on ProRes or VP9 (those have their own internal speed controls).

---

### 3.12 Presets

The **Presets** panel (sidebar) lists every preset stored in `presets.json` in the program directory.

**Built-in presets:**

| Preset | Character |
|---|---|
| **Empty** | All effects off, all sliders at 0, mystery zeroed, silence treatment `none`. The neutral starting point. |

That's the only built-in. The previous "stylistic" presets (Drillcore, Datamosh, ASCII Rave, Death Grips, Rhythm Flash, Vortex Dream) have been removed — they were placeholders, not curated for production use. New curated presets will be added later.

If you upgrade from an older release that wrote those built-ins into your `presets.json`, the program drops them on first launch and keeps any presets you saved yourself. The `Empty` entry is regenerated automatically.

**Working with presets:**

- **Double-click** or select + **Load** — applies the stored parameter values to the entire UI (effects, sliders, mystery, export, formula, silence mode, custom resolution).
- **Save Current** — prompts for a name and writes the current state to `presets.json` as a non-builtin entry.
- **Delete** — removes the selected preset (the `Empty` builtin is regenerated if you delete it).
- Active preset name shows below the list.

---

### 3.13 Render Modes

Three render buttons are pinned to the bottom of the sidebar:

---

**DRAFT (5 sec / 480p)**

Renders the first 5 seconds of the audio track at 480p resolution. Effects that are computationally expensive (datamosh optical flow, ASCII) use simplified fast paths. Use this to quickly check cut timing and basic effect behavior.

---

**PREVIEW (5 sec)**

Renders the first 5 seconds at full configured resolution and quality. All effects run at full fidelity. Use this to validate the visual result before committing to a full render.

---

**RENDER FULL VIDEO**

Renders the complete output video for the entire duration of the audio track. A progress bar and status log show real-time progress.

A native Save dialog opens first; the suggested filename is `disc_XXXX.<ext>` where `XXXX` is a random 4-digit number and `<ext>` matches the codec/container chosen in EXPORT (`.mp4`, `.mkv`, `.mov`, `.webm`). Pick a folder and confirm — the render starts immediately. The final output path is echoed to the status log on completion.

The **STOP** button (right panel) interrupts any active render or preview playback.

---

### 3.14 Mystery Panel

The **[ ? ]** tab holds nine sliders. They are named.

The names are not metaphors.

Each one addresses a quantity that exists in the pipeline — a real parameter that shapes the signal in a real way — but the relationship between the name and what it does is not described here, because the description would be inaccurate. Not because we are concealing it. Because the accurate description requires a frame of reference that this document was not written in.

The sliders are not independent. What you give to one changes what another is capable of. A position that does nothing alone may do a great deal in the presence of another. The system has memory of the other sliders. So do the effects.

**VESSEL** feeds. It is not empty. What you gave it earlier is still present — weighted, layered behind what you are giving it now. At high values, what was begins to outweigh what is. It speaks to DOT.

**ENTROPY\_7** erodes a boundary that was never supposed to be there. The erosion is not random — it is living. It reads the frame as a condition and continues from it. What VESSEL has accumulated, ENTROPY\_7 grows inside.

**DELTA\_OMEGA** governs the speed of a transformation that happens whether you turn it on or not. You are only adjusting how much of it the rest of the system is allowed to hear.

**STATIC\_MIND** is a gate, but it gates the wrong thing. What passes through at 0 is not what passes through at 1. The transition between them is not a blend. RESONANCE, if present, does something to the structure that STATIC\_MIND was trying to hold in place.

**RESONANCE** does not merely resonate. Rows ring. At sufficient values the image stops being an image and becomes the record of its own frequency. It speaks to COLLAPSE. Together they lower each other's thresholds.

**COLLAPSE** initiates. It works in two directions simultaneously, though the second is not visible until the first is already underway. ZERO changes what it arrives at.

**ZERO** is not zero. It is a seed. The same values, a different ZERO — a different outcome. It also holds a record the original does not know it carries. FLESH\_K amplifies what ZERO reveals.

**FLESH\_K** is what it says. It converts. The conversion is not clean. What exits is not in the same space as what entered. At low values nothing appears to happen. The threshold is not fixed.

**DOT** is small. It separates what arrived together. Three things depart from the same moment but do not arrive at the same time. It lengthens when VESSEL is present.

Move them. Render. Observe what changed. Then move them again.

No combination is wrong. Some combinations are irreversible within the session.

### 3.15 Multiple Video Sources

The **Load Source Video** button accepts multiple files simultaneously. Hold `Ctrl` or `Shift` to select several files in the dialog, or drag-select a range.

When more than one file is loaded, the engine maintains an independent video reader for each source. At the beginning of every audio segment, a source is selected at random from the pool. Within that segment, frames are read sequentially from the chosen source.

This means:
- Different segments can come from completely different video files with no preparation or manual splicing
- Scene detection (if enabled) runs on all loaded files and collects cut points from each
- Datamosh pre-processing uses the first file in the selection as the P-frame source
- Files of different resolutions and frame rates are all valid; each is read at its native rate and scaled to the output resolution

The sidebar label shows the filename when one file is loaded, and the count (`3 videos loaded`) when multiple are selected.

---

### 3.16 Silence Treatment

Controls how the engine processes frames during **SILENCE** segments longer than 1 second.

Located in the **Cut Logic** section, under *Silence Treatment*. Default: **None**.

| Option | Behavior |
|---|---|
| **None** *(default)* | No treatment. Silent segments are rendered identically to active ones. Use when silence is compositionally intentional and should not be visually marked. |
| **Dim** | Multiplies all pixel values by 0.6, reducing brightness. Good for maintaining visual contrast between active and quiet moments. (Hidden when *Hide color-altering effects* is on.) |
| **Blur** | Applies a Gaussian blur (15×15 kernel). The image stays full brightness but loses sharpness, creating a dreamlike soft-focus look during quiet passages. |
| **Both** | A lighter blur (11×11) combined with a 0.7 brightness multiplier. The strongest treatment — defocused and darkened simultaneously. |

---

## 4. Disc VPC 01 R-T — Real-Time (C++)

This is the live VJ version. You point it at whatever audio is playing — a microphone in front of the speakers, a "what's playing on this PC" loopback, the line-out of a mixer — and it runs video and effects on a screen, in time with the music, as it happens.

**Typical setup:** a laptop, a projector or second monitor, a music source (DJ, band, Spotify, anything). You drop a few video clips on the program, click START, and the program does the rest. The "control window" stays on your laptop screen; the "output window" goes fullscreen on the projector.

> **Just want to use it, not understand the internals?** Skip §4.1 and §4.2 and go straight to §4.3 (interface). The build/architecture sections are only for people compiling the program themselves.
>
> If you grabbed a release bundle, double-click `DiscVPC01-RT.exe` and you're up.

### 4.1 Architecture

```
   ┌────────────────────┐         ┌────────────────────┐
   │  Control Window    │         │  Output Window     │   (optional, on
   │  (GUI + thumbnail) │         │  (clean canvas)    │    chosen monitor,
   └──────────┬─────────┘         └──────────┬─────────┘    borderless fullscreen)
              │     shared OpenGL context — zero-copy      │
              └─────────────────────┬───────────────────────┘
                                    ▼
                    ┌──────────────────────────────┐
                    │  Internal canvas FBO         │   1280×720 / 1920×1080
                    │  (effect chain renders here) │   / 1024×768 (chosen
                    └──────────────────────────────┘    in GUI, runtime)
                                    ▲
                       canvas_place.frag (aspect)
                                    │
                    ┌──────────────────────────────┐
                    │  Video pool                  │   N sources, native
                    │  (decode threads, GPU pool)  │   resolution, capped
                    └──────────────────────────────┘    at 1920×1080
```

The control window holds the GUI. The output window is opened on demand on a
chosen monitor as borderless fullscreen — it shares the OpenGL context with
the control window, so the canvas FBO texture is sampled directly from both
sides without any copy.

All effects run in **canvas space** at the chosen canvas resolution, decoupled
from any window's actual size. This means dragging or resizing the control
window does not affect render cost or visual quality.

### 4.2 Build from source

See §2 for the full vcpkg + CMake instructions. The output is a single
`DiscVPC01-RT` executable plus a `presets/` folder.

### 4.3 Interface Layout

**Audio device selector**

The dropdown lists all audio input devices detected by the system. This includes:
- Microphones
- Line inputs
- WASAPI loopback devices (e.g., "Stereo Mix", virtual cables, VoiceMeeter) for capturing system audio

Select the desired device before starting. The **↺** button refreshes the device list.

**Changing the device while running:** Select a new device from the dropdown while the engine is active. The audio stream switches immediately and recalibration begins automatically.

**📁 Video** — load one or more video files to be played and processed. Supported formats: `.mp4`, `.avi`, `.mov`, `.mkv`, `.flv`, `.wmv`, `.mpg`, `.mpeg`. Hold `Ctrl` or `Shift` to select multiple files at once.

When multiple files are loaded, the engine keeps a separate sequential reader for each. Random cuts (triggered by audio events) jump to a random frame from a randomly selected source. Sequential playback stays within the current source until its end, then continues from the next source on the following call. Pre-caching runs on all sources in parallel on startup. The label shows `N videos loaded` when more than one file is active.

**📁 Overlays** — load a folder of overlay images (same formats as §3.9).

---

The control window has a fixed top strip and a four-column control grid below.

```
┌─────────────────┬────────────────────────────────────────────────────┐
│                 │   Transport                                        │
│  Preview        │    [START/STOP]  Freeze  Blackout                  │
│  thumbnail      │    Mode: Continuous / Cut on beat   FPS            │
│  320 × 180      │    Bass / Mid / Treble  ─ live meters              │
│                 │    Segment   ·   Device   ·   Audio cb counter     │
│                 │    Video: #N of M  /  pool                         │
├─────────────────┴────────────────────────────────────────────────────┤
│  [Master]      [Effects]      [Video / Audio]    [Overlays /         │
│   Chaos         24 toggles     File list with     Presets / Output]  │
│   Intensity                    [*] active marker  Chroma key sliders │
│   Threshold                    Audio device       Preset combo       │
│   Cut Interval                  combo + meters    Monitor selector   │
│   Canvas + Aspect                                 Open / Close output│
└──────────────────────────────────────────────────────────────────────┘
```

Most of the window is dedicated to controls. The preview is a small thumbnail
because the dedicated **Output Window** (§4.7) is the primary surface for
audience-facing video — opened on whichever monitor the projector is on.

Press **Tab** to hide the GUI: the canvas fills the control window, with no
chrome.

---

### 4.4 Loading Sources

The fastest path is **drag-and-drop**: drop video files or whole folders onto
the control window. Folders are scanned recursively; any video files found
become individual sources in the pool. If the folder also contains images,
they are loaded together as the overlay set.

Supported video extensions:
`.mp4 .avi .mov .mkv .mpg .mpeg .wmv .webm .m4v .flv`

Supported overlay image extensions:
`.png .jpg .jpeg .bmp .gif .tga`

On Windows, **Add Videos** in the *Video / Audio* panel opens a native file
dialog. macOS / Linux rely on drag-and-drop only.

All paths are handled as UTF-8 end-to-end — Cyrillic and other non-ASCII names
load and display correctly. The video list highlights the active clip slot
(see §4.9) with a `[*]` marker and shows its slot number for the keyboard
shortcut at a glance.

---

### 4.5 Audio

This is where you tell the program *what to listen to*. Pick a mic, a line-in, or a "loopback" device that captures whatever your computer is playing — then start. The first ~1.5 seconds the program quietly listens to the room and decides what counts as silence; after that, every louder sound is a potential trigger.

If meters stay flat after START, see §7 — most of the time it's the device pick.

**Device list**

The dropdown lists detected input devices with their host API in brackets:
`[WASAPI]`, `[ASIO]`, `[CoreAudio]`, `[ALSA]`, `[JACK]`, etc. The list is
deduplicated by name with API priority, so the same physical device doesn't
appear once per host API. WASAPI loopback devices (system-audio capture for VJ
sets driven by a DAW or media player) appear at the bottom.

If no device is selected when **START** is pressed, the engine auto-picks the
platform default input — you can leave the dropdown alone unless you need a
specific device.

**Sample rate & channels**

The engine queries the device's native sample rate (typically 48 kHz on
WASAPI) and uses it directly. Multi-channel inputs are downmixed to mono in
the audio callback. The actual sample rate granted by the OS is shown in the
Transport panel:  `sr=48000 Hz`.

**Calibration**

For roughly 1.5 seconds after START, the engine measures the ambient noise
floor and sets the segment-classification gate to `4 ×` that value. During
calibration, segments are classified as SILENCE — effects do not fire.

**Live diagnostics**

The Transport panel shows:

- `Audio cb: N` — total audio callbacks received since START. Should grow
  rapidly (≈ sample_rate / buffer_size per second). If it stays at zero, the
  stream is open but the OS isn't delivering samples — typical sign of a
  misconfigured loopback or muted exclusive-mode device.
- Live `Bass / Mid / Treble` bars and `Segment` readout.
- `BEAT` indicator that flashes on detected beats.

**Sensitivity** *(slider, 0.1 – 3.0)* — multiplier applied to the calibrated
gate. Higher = engine demands louder signal before reacting. Lower = engine
reacts to quieter passages.

---

### 4.6 Canvas and Aspect

The engine renders into an internal canvas FBO at one of three resolutions,
selected from the **Master** panel or via preset:

| Preset | Resolution | Aspect |
|---|---|---|
| Default | 1280 × 720 | 16:9 |
| HD | 1920 × 1080 | 16:9 |
| Classic | 1024 × 768 | 4:3 |

Each video is decoded at its **native resolution** (capped at 1920×1080 to
keep memory bounded) and composited onto the canvas with one of four aspect
modes:

| Mode | Behaviour |
|---|---|
| **Contain** *(default)* | Whole video fits inside the canvas; black bars on the short axis. |
| **Cover** | Video fills the canvas; the long axis is cropped. |
| **Stretch** | Video is stretched to fill the canvas, ignoring aspect. |
| **Native 1:1** | Video is drawn at one canvas pixel per video pixel, centered, with black around it. |

All effect passes operate at canvas resolution — picking a smaller canvas
(e.g. 1024×768) gives a noticeable FPS boost on weaker GPUs without affecting
the audio pipeline.

---

### 4.7 Output Window (Second Display)

The control window is for you — buttons, sliders, the small preview thumbnail. The **output window** is what the audience sees: just the picture, fullscreen, no controls, on whatever monitor or projector you point it at.

In the *Overlays / Presets / Output* panel:

1. **Monitor** — drop down listing every connected monitor with its native
   resolution and OS-reported name. Default selection is monitor 0 (primary).
2. **Open Output** — opens a borderless fullscreen window on the chosen
   monitor. The window has no decorations, no title bar, no controls — only
   the canvas, letterboxed to fit the monitor's aspect.
3. **Close Output** — closes it. So does **Esc** while focus is on the output
   window, or **Shift+O** anywhere.

The two windows share the OpenGL context, so the canvas texture is rendered
into the output window with one shader pass — no CPU read-back, no GPU copy.
Both windows have vsync enabled.

If only one monitor is available, you can still use the output window by
opening it on monitor 0 and toggling the control window with **Tab**.

---

### 4.8 Frame-Selection Mode

The **Mode** combo in the Transport panel toggles between two frame-picking
policies:

**Continuous** — linear playback through whichever video is active (or
round-robin across the pool if no video is focused). Effects still react to
audio. Use for steady visual beds where the audio doesn't have a clear pulse.

**Cut on beat** *(default)* — random frames on hard triggers (`IMPACT`,
`DROP`, or any detected beat) with a 30 ms anti-spam floor; soft triggers
(`BUILD`, sustained beat) gated by the **Cut Interval** slider. Between
triggers, playback continues sequentially from wherever the cut landed.

Switch live with the **M** key.

---

### 4.9 Active Video — Clip Slots

Think of the videos you loaded as numbered cards in your hand. By default the program shuffles through all of them. But you can also lock onto one card — say, the third one — and every cut will pull from that one until you let go. That's the "clip slot" idea, and it's how most VJs play live: load some footage before the set, then trigger pieces of it with number keys as the music moves.

The video pool can be in one of two focus states:

- **Pool** *(default)* — every video in the list participates: random cuts
  pick from any source, sequential rotates through them.
- **Focused** — exactly one video is on stage. Both cut and continuous modes
  serve all frames from that source.

Focusing a clip slot:

| Method | Action |
|---|---|
| Press `1`–`9` | Focus video at that slot (1 = first loaded, 9 = ninth) |
| Press `0` | Focus the tenth video |
| Press the same number again | Release focus, return to pool |
| Press `` ` `` (grave / backtick) | Release focus explicitly |
| Click a row in the *Video* list | Focus that row |
| Click the row marked `[*]` | Release focus |

The Transport panel shows the current state:
`Video: #3 of 7   3=release` (focused) or `Video: pool (7 files)` (released).

This is the core "clip slot" workflow used by every modern VJ tool: load a
handful of videos before the set, then trigger them on the fly as the music
moves through sections. Combined with **Cut on beat**, focusing one clip
gives you classic beat-locked random-seek glitch editing within a single
source.

---

### 4.10 Effects

24 visual effects, each a checkbox. Tick one on and the program will fire it from time to time when the music gives it an excuse — how often is set by that effect's *chance* slider, and how *strong* it looks is shaped by **Chaos** and **Master Intensity** at the top of the panel.

You don't have to memorize the table below. The fast way is: pick a preset, then start clicking effects on and off live to taste.

**Intensity model** *(the math behind how strong each effect feels — safe to skip)*:

```
seg_boost = sqrt(segment.intensity)                         (0..1)
drive     = chaos × (0.6 + 0.4 × master_intensity)          (0..1)
fi_base   = clamp(0.25 + 1.4 × drive × seg_boost, 0, 1)     (0..1)
```

The 0.25 floor keeps fired effects visible during quiet passages. The sqrt
curve boosts modest segments so SUSTAIN and NOISE are not flattened to ~10 %
strength. Both **Chaos** and **Master Intensity** sliders modulate effect
strength.

The 24 effects:

| # | Effect | Behaviour |
|---|---|---|
| 0 | **Deriv Warp** | Frame derivative used as displacement map — feedback-like flow that grows with motion. |
| 1 | **Flash** | Brief white or black flash on the canvas, scaled to intensity. |
| 2 | **Stutter** | Holds the previous frame and repeats it, giving a glitched freeze. |
| 3 | **Pixel Sort** | Sorts horizontal stripes of pixels by luminance. |
| 4 | **Ghost Trails** | Blends previous frames in for a motion-trail/echo. |
| 5 | **Scanlines** | Darkens every N-th row — CRT look. |
| 6 | **Bitcrush** | Quantizes colour values to a small palette. |
| 7 | **Block Glitch** | Random rectangular displacements / replacements. |
| 8 | **Negative** | Inverts colour for the affected frame. |
| 9 | **Color Bleed** | Horizontal smear of a single channel — VHS chroma delay. |
| 10 | **Interlace** | Even rows from current frame, odd rows from a previous frame. |
| 11 | **Bad Signal** | Vertical noise bars + horizontal row jitter — broadcast loss. |
| 12 | **Zoom Glitch** | Punch-in zoom on the canvas centre with nearest-neighbour scaling. |
| 13 | **Mosaic** | Pulsing block mosaic with bass-driven block size. |
| 14 | **Phase Shift** | Alternating horizontal bands shifted left / right. |
| 15 | **Dither** | Bayer dither at reduced colour depth. |
| 16 | **Feedback** | Accumulator-based visual feedback / burn-in. |
| 17 | **Temporal RGB** | R / G / B channels sampled from different past frames — chromatic temporal aberration. |
| 18 | **Waveshaper** | tanh soft-clip on pixel values — colour saturation. |
| 19 | **Overlays** | Composites a random image from the loaded overlay set onto the canvas. |
| 20 | **Vortex** | Spiral rotation around the canvas centre with chromatic split. |
| 21 | **Fractal Noise** | Domain-warped fBm distortion — organic "melting" warp. |
| 22 | **Self Displace** | Previous frame used as a displacement map — closest analogue to true datamosh. |
| 23 | **ASCII** | GPU ASCII filter rendering the canvas as character-density blocks. |

The first ten effects (rows 0..9 above) are mapped to the **Q W E R T Y U I O P**
keys for direct toggling during a set.

**Overlays** — load an image folder by dragging it onto the window or via
*Load Folder* in the *Overlays* panel. **Overlay Intensity** in the GUI
controls how often a random overlay image is composited on top of the canvas.

**Chroma Key** — applies to overlays. Modes `none / dominant / secondary /
manual` with **Tolerance** and **Softness** sliders. In `manual` mode, an
RGB picker chooses the keyed colour. See §3.9 for the full chroma-key
parameter reference.

---

### 4.11 Presets

Presets save the entire `EngineSettings` snapshot — chaos, intensity,
threshold, cut interval, canvas resolution, aspect mode, all 24 effect enable
states and their `chance` probabilities, chroma-key configuration, overlay
intensity. They are JSON files in the `presets/` folder next to the
executable.

The release bundle ships `presets/rt_blank.json` (loaded automatically at
launch — it's the neutral starting point with all effects off) along with
several stylistic presets.

In the *Overlays / Presets / Output* panel:

- **Combo box** — lists every `*.json` in `presets/`, selects the active one.
- **Load** — applies the selected preset.
- **Save As…** — prompts for a name, writes `presets/rt_<name>.json`.
- **Refresh** — re-scans the folder (useful if you dropped new presets in).

To load presets via keyboard, hold **Shift** while pressing a number:
`Shift+1` loads the first preset, ..., `Shift+0` loads the tenth.

---

### 4.12 Keyboard Shortcuts

All bindings are global to the control window and are ignored while focus is
inside an ImGui text-input field.

**Transport**

| Key | Action |
|---|---|
| `Space` | Start / Stop audio |
| `B` | Blackout toggle |
| `F` | Freeze toggle (also stops the GPU upload pump — frame stays still) |
| `M` | Toggle Continuous / Cut on beat mode |
| `Tab` | Hide / show GUI overlay (canvas fills the control window when hidden) |
| `F11` | Fullscreen the control window |
| `Esc` | Close output window if open, else exit |

**Clips**

| Key | Action |
|---|---|
| `1` – `9` | Focus video slot 1..9 |
| `0` | Focus video slot 10 |
| Same digit again | Release focus to the pool |
| `` ` `` (grave) | Release focus explicitly |

**Effects**

| Key | Action |
|---|---|
| `Q W E R T Y U I O P` | Toggle effects 0..9 |

**Parameters**

| Key | Action |
|---|---|
| `[` / `]` | Decrease / increase **Chaos** by 0.02 |
| `,` / `.` | Decrease / increase **Cut Interval** by 0.02 |

**Output / Presets**

| Key | Action |
|---|---|
| `Shift + 1`…`Shift + 0` | Load preset 1..10 |
| `Shift + O` | Close output window from anywhere |

---

### 4.13 Diagnostic Log

On every launch the program writes `disc_vpc.log` to its working directory
with unbuffered, line-flushed output. The log captures:

- The full audio device table from PortAudio (host API, channel counts,
  default sample rate per device).
- Every `Pa_OpenStream` attempt with parameters and exact error text on
  failure.
- The actual sample rate / latency granted by the OS for the running stream.
- Video file open events with native resolution and decode resolution after
  the 1920×1080 cap.
- First GPU upload dimensions for every loaded source.
- A Windows SEH crash handler logs the exception code and address before
  the OS terminates the process.

If something doesn't behave as expected, the log file is the first place to
look. It is always overwritten on launch, so it never grows unbounded.

---

## 5. Segment Types Reference

Both programs use the same segment classifier. Understanding segment types helps predict which effects will fire.

| Type | Condition | Typical audio moment |
|---|---|---|
| **IMPACT** | Short duration (< 0.3 s) + loud + sharp attack | Drum hit, snare, sharp transient |
| **BUILD** | Rising RMS trend over recent history | Pre-drop buildup, crescendo |
| **DROP** | Falling RMS after a loud period | Post-peak decay, breakdown |
| **SUSTAIN** | Loud (above threshold), no strong trend | Sustained note, chord, steady beat |
| **NOISE** | High spectral flatness (> 1.5 × mean) | Distortion, white noise, dense texture |
| **SILENCE** | RMS below threshold | Quiet passage, gap between phrases |

Effects declare which segment types they respond to. An effect will not fire on a segment type not in its trigger list, regardless of the chance setting.

---

## 6. Recommended Workflows

### Disc VPC 01 — Iterative editing

1. Load audio and video source files
2. Select a built-in preset matching your intended style
3. Click **DRAFT** to render a quick 5-second 480p preview
4. Review in the preview monitor. Adjust Beat Threshold if cuts feel too frequent or too sparse
5. Toggle specific effects on/off and adjust their chance sliders
6. Click **PREVIEW** to verify at full quality
7. Repeat steps 4–6 until satisfied
8. Click **RENDER FULL VIDEO**

### Disc VPC 01 R-T — Live performance

1. Drag-and-drop a handful of video files (or a folder) onto the control
   window — these become your clip slots
2. Open the **Output** panel (right column), pick the projector / second
   monitor, click **Open Output** for borderless fullscreen
3. Optionally drop an image folder for overlays
4. Pick an audio device or leave the dropdown empty for the platform default
   — WASAPI loopback is the right pick when reacting to a DAW or media
   player
5. Press **Space** (or click START). Calibration runs ~1.5 sec
6. Watch `Audio cb` in the Transport panel: it should grow rapidly. If it
   stays at 0, the device is open but the OS isn't delivering samples — try
   a different host API or check loopback configuration
7. Adjust **Threshold (sensitivity)** if the engine reacts to silence or
   fails to react to audio
8. Pick a starting preset with `Shift+1`…`Shift+0`, then sculpt with
   **Chaos** (`[ ]`), individual effect toggles (`Q`–`P`), and clip slot
   focus (`1`–`9`, `0`)
9. Press **Tab** to hide the control GUI mid-set if you want the control
   window to mirror the canvas; **M** toggles Cut / Continuous on the fly

---

## 7. Troubleshooting

If something isn't working, don't panic — almost every issue has a one-line cause and a one-line fix, and most of them are listed below.

For the live (R-T) program: there's a file called `disc_vpc.log` that's written next to the program every time you start it. It records what audio devices were seen, what happened when you tried to open one, what happened when a video was loaded, and (if the program crashes) where it crashed. Open that file in any text editor — most issues become obvious within a few lines.

**R-T: audio meters flat — no reaction to sound**

The audio stream may not have opened, or the OS isn't delivering samples.
Open `disc_vpc.log` and look for `[audio]` lines. The Transport panel also
shows `Audio cb: N` — if it stays at 0 after pressing START, samples aren't
arriving. Common causes: muted exclusive-mode WASAPI device, picking a
loopback device that isn't enabled in Windows Sound settings. Try a
different device from the dropdown. Pressing START with no device selected
auto-picks the platform default — use that as a sanity check.

**R-T: video doesn't load**

Open the log and find the `[video]` lines for the file you tried. If it
shows `avformat_open_input failed`, the path or format is the issue. UTF-8
paths with Cyrillic / non-ASCII characters are supported — drag-and-drop
delivers UTF-8 directly. Verify the file plays in another player.

**R-T: engine reacts to silence**

Increase **Threshold** in the Master panel. The first ~1.5 sec after START
are calibration; if the room is loud during that window the gate stays low.
Press STOP / START to recalibrate.

**R-T: low FPS**

Drop the canvas resolution to **1024×768** in the Master panel — effect
passes get noticeably cheaper. Disable expensive effects: ASCII, Self
Displace, Fractal Noise, and Feedback are the heaviest. Close other
GPU-heavy apps. Check `Audio cb:` is growing normally so audio isn't
starving the loop.

**R-T: Cyrillic file names show as garbage**

This was fixed in 0.2.0 — make sure you are running the latest build. Both
`OPENFILENAMEW` (Windows native dialog) and GLFW drag-and-drop deliver
UTF-8 paths, and the GUI displays them via `fs::u8path`.

**R-T: freeze doesn't actually freeze the image**

Also fixed in 0.2.0 — the GPU upload pump now skips while freeze is on, so
the texture under `last_frame_tex_` no longer drifts.

**R-T: effects feel weak even with Chaos and Master at max**

The intensity model in 0.2.0 applies a sqrt boost to segment intensity and a
0.25 floor to ensure fired effects are visible. If you're on an older build,
update. If on 0.2.0+ and still subtle, consider that some effects (Ghost,
Scanlines, Dither) are intentionally subtle by design — pair with a stronger
warp (Vortex, Fractal Noise, Self Displace) for a punchier mix.

**Offline editor: engine reacts to silence / fires constantly**

The noise gate has not calibrated properly. Click **Recalibrate** and wait
2 seconds. If the problem persists, increase **Beat Threshold** in the Cut
Logic section.

**Offline editor: `Illegal Audio-MPEG-Header` in console**

Benign FFmpeg warning from malformed MP3 headers. The audio is still
decoded correctly. Safely ignored.

**Offline editor: `Invalid NAL unit size`, `partial file`, `moov atom not found` in console**

Expected and harmless when the **Datamosh** effect is enabled. Datamosh
deliberately produces a broken H.264 stream with no I-frames — that's the
whole point of the effect — and OpenCV's bundled ffmpeg honestly reports it
as broken when it reads the file. The program suppresses these warnings
internally on launch (`OPENCV_FFMPEG_LOGLEVEL=-8`); if you still see them,
you're on a build older than this fix. Stale `*_dmosh_src.mp4` files left
behind by an aborted render are also cleaned up before each new render to
prevent the `moov atom` warning specifically.

**Offline editor: video ends before the song does**

Fixed in the latest build. The render now tracks total target frames
explicitly (`round(target_duration × fps)`), pays back any rounding
shortfall by padding the tail with the last produced frame, and drops the
ffmpeg `-shortest` flag (which used to clip the audio under a too-short
video stream). If you still see the issue, you're running an older
checkout — pull and re-run.

**Offline editor: Beat Snap Tolerance slider doesn't move / always reads 0**

Fixed. The slider was being formatted as an integer (rounded `0.05` → `0`),
which made it look stuck. Pull the latest build and the slider now displays
the actual float value.

**Offline editor: ffmpeg crash on render with custom resolution**

`yuv420p` (and ProRes 422) require even width and height. The program now
silently rounds custom dimensions down to the nearest even number, so
`1281 × 721` becomes `1280 × 720` automatically. If you're on an older
build and ffmpeg crashes immediately, type even values directly.

**Offline editor: `madmom` install fails on Python 3.11+**

Use Python 3.10.x exactly. `madmom` requires Cython extensions that do not
build on newer Python versions.

**R-T build: vcpkg can't find `pa_jack.h` on Linux**

The provided `rt_cpp/CMakeLists.txt` adds explicit `jack` and `asound` link
deps because vcpkg's static portaudio embeds `pa_jack.c` / `pa_alsa.c`
without propagating the transitive link requirements. Make sure `libjack-dev`
and `libasound2-dev` are installed system-wide before configuring.

---

*Disc VPC 01 / Disc VPC 01 R-T*
