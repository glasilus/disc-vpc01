#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// ---------------------------------------------------------------------------
// VIZ: BARS — 16-band spectrum bars with caps and a floor reflection.
// The screen is split into 16 columns above a baseline. Each bar is drawn
// with smoothstep-antialiased edges, a heat gradient (cool at the root, hot
// at the top), a bright cap line at the bin height, and a wide gaussian
// glow accumulated over all 16 bins so loud bars bleed light sideways.
// Below the baseline the same field is mirrored, squashed and faded as a
// glossy floor reflection.
//   uBins  -> bar heights          uBass -> gradient warmth boost
//   uBeat  -> one-frame flash      uTreble -> cap brightness
// Composited additively so the bars glow over the video.
// ---------------------------------------------------------------------------

uniform sampler2D uTex;
uniform vec2  uResolution;
uniform float uTime;
uniform float uIntensity;
uniform float uBass;
uniform float uTreble;
uniform float uLevel;
uniform float uBeat;
uniform float uBins[16];

vec3 hsv2rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

// Evaluate the bar field at (x in 0..1, h = normalized height above baseline).
// Returns accumulated light. Split out so the reflection can reuse it.
vec3 barField(float x, float h, float flash) {
    vec3 acc = vec3(0.0);

    // Which column are we in, and where inside it?
    float fx  = x * 16.0;
    int   bi  = int(clamp(floor(fx), 0.0, 15.0));
    float cx  = fract(fx) - 0.5;              // -0.5 .. 0.5 within the cell
    float v   = uBins[bi];

    // Antialiased bar body: ~72% of the cell width, soft edges.
    float halfW = 0.36;
    float aaX   = 16.0 / uResolution.x * 1.5; // ~1.5px in cell units
    float body  = 1.0 - smoothstep(halfW - aaX, halfW + aaX, abs(cx));

    // Antialiased top edge at the bin height.
    float aaY   = 2.0 / uResolution.y / 0.72; // ~2px in h units
    float under = 1.0 - smoothstep(v - aaY, v + aaY, h);

    // Heat gradient: cool hue at the root -> hot near the bar's own top.
    float heat = clamp(h / max(v, 1e-3), 0.0, 1.0);
    float hue  = fract(0.62 - 0.55 * heat + uTime * 0.015); // blue -> red/orange
    float sat  = 0.9 - 0.55 * heat * heat;                  // whitens at the top
    vec3 grad  = hsv2rgb(vec3(hue, sat, 0.55 + 0.45 * heat));
    grad *= 0.8 + 0.5 * uBass;

    acc += body * under * grad * (0.55 + 0.45 * v) * flash;

    // Bright cap line hugging the top of the bar.
    float cap = body * exp(-abs(h - v) * (uResolution.y * 0.045))
              * (1.2 + 1.6 * uTreble) * flash;
    acc += cap * hsv2rgb(vec3(fract(hue + 0.04), 0.25, 1.0)) * step(0.015, v);

    // Wide soft glow: every bin bleeds light sideways, scaled by its energy.
    for (int i = 0; i < 16; i++) {
        float bcx  = (float(i) + 0.5) / 16.0;
        float dx   = (x - bcx) * 16.0;
        float bv   = uBins[i];
        float g    = exp(-dx * dx * 2.2)
                   * exp(-max(h - bv, 0.0) * 9.0)
                   * bv * 0.10 * flash;
        acc += g * hsv2rgb(vec3(fract(0.62 - 0.45 * bv + uTime * 0.015), 0.8, 1.0));
    }

    return acc;
}

void main() {
    vec3 base = texture(uTex, vUV).rgb;

    float baseline = 0.24;             // reflection floor line
    float flash    = 1.0 + 0.6 * uBeat;

    vec3 viz = vec3(0.0);

    if (vUV.y >= baseline) {
        // Main bars above the baseline.
        float h = (vUV.y - baseline) / (1.0 - baseline);
        viz = barField(vUV.x, h, flash);
    } else {
        // Mirrored, squashed, fading reflection below the baseline.
        float h    = (baseline - vUV.y) / baseline * 1.8;   // squash x1.8
        float fade = (1.0 - h / 1.8) * 0.30;                // fades with depth
        // Subtle horizontal ripple sells the "wet floor" look.
        float wob = 0.004 * sin(vUV.y * 140.0 + uTime * 2.0) * uLevel;
        viz = barField(clamp(vUV.x + wob, 0.0, 1.0), h, flash) * fade;
    }

    // Thin glowing baseline strip ties the two halves together.
    viz += exp(-abs(vUV.y - baseline) * uResolution.y * 0.15)
         * (0.10 + 0.25 * uLevel) * hsv2rgb(vec3(fract(uTime * 0.02), 0.4, 1.0));

    // Additive over the canvas; uIntensity == 0 returns the untouched frame.
    vec3 col = base + viz * clamp(uIntensity, 0.0, 1.0);
    FragColor = vec4(col, 1.0);
}
