#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// ---------------------------------------------------------------------------
// VIZ: ALCHEMY — procedural kaleidoscopic mandala (single pass, no feedback).
// The plane is folded into 6-fold mirror symmetry, then domain-warped with
// cheap fBm. Inside the folded space, three families of thin glowing lines
// are drawn with exp(-|sin|) filigree: concentric rings, radial harmonic
// spokes, and a woven lattice — layered into an ornate illuminated sigil.
//   uTime  -> slow rotation + hue cycling      uBass -> radius breathing
//   uMid   -> warp depth / detail agitation    uTreble -> outer sparkle ring
//   uBins  -> per-ring energy (rings light up at the radius of their band)
//   uBeat  -> one-frame bloom
// Composited with luminance gating: mix(base, mandala, uIntensity * mask),
// so the dark negative space stays transparent and the video shows through.
// ---------------------------------------------------------------------------

uniform sampler2D uTex;
uniform vec2  uResolution;
uniform float uTime;
uniform float uIntensity;
uniform float uBass;
uniform float uMid;
uniform float uTreble;
uniform float uBeat;
uniform float uBins[16];

#define PI  3.14159265359
#define TAU 6.28318530718

vec3 hsv2rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

mat2 rot2(float a) {
    float c = cos(a), s = sin(a);
    return mat2(c, s, -s, c);
}

vec2 hash2(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return fract(sin(p) * 43758.5453123) * 2.0 - 1.0;
}

float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = dot(hash2(i),               f);
    float b = dot(hash2(i + vec2(1, 0)),  f - vec2(1, 0));
    float c = dot(hash2(i + vec2(0, 1)),  f - vec2(0, 1));
    float d = dot(hash2(i + vec2(1, 1)),  f - vec2(1, 1));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 3; i++) {
        v += a * noise(p);
        p = p * 2.13 + vec2(1.7, 9.2);
        a *= 0.5;
    }
    return v;
}

// Thin bright line where sin(x) crosses zero; k controls line sharpness.
float filigree(float x, float k) {
    return exp(-abs(sin(x)) * k);
}

void main() {
    vec3 base = texture(uTex, vUV).rgb;

    vec2 p = vUV - 0.5;
    p.x *= uResolution.x / uResolution.y;

    float t = uTime;

    // Slow rotation of the whole sigil, kicked slightly on beats.
    p = rot2(t * 0.06 + uBeat * 0.12) * p;

    // Radius breathes with the low end.
    float breath = 1.0 + 0.10 * uBass + 0.03 * sin(t * 0.55);
    p /= breath;

    float r   = length(p);
    float ang = atan(p.y, p.x);

    // ---- 6-fold kaleidoscopic mirror fold ---------------------------------
    float N      = 6.0;
    float sector = TAU / N;
    float fa     = mod(ang + t * 0.02, sector);        // sectors drift slowly
    fa           = abs(fa - sector * 0.5);             // mirror inside sector
    vec2  q      = vec2(cos(fa), sin(fa)) * r;         // folded cartesian

    // ---- domain warp: mids agitate the filigree ----------------------------
    float warpAmt = 0.10 + 0.22 * uMid;
    float w  = fbm(q * 3.5 + vec2(t * 0.10, -t * 0.07));
    float w2 = fbm(q * 6.0 - vec2(t * 0.06,  t * 0.09));
    float rr = r  + w  * warpAmt * 0.5;                // warped radius
    float wa = fa + w2 * warpAmt * 1.2;                // warped fold angle

    // Which spectrum band lives at this radius? Rings light with their bin.
    int   bi   = int(clamp(rr * 22.0, 0.0, 15.0));
    float binE = uBins[bi];

    // ---- layered ornament ---------------------------------------------------
    // 1) Concentric rings, drifting inward, energized by their band.
    float rings = filigree(rr * 14.0 - t * 0.8 + w * 3.0, 5.0)
                * (0.45 + 0.9 * binE);

    // 2) Radial harmonic spokes: stacked polar sine harmonics in folded space.
    float spokes = filigree(wa * N * 4.0 + rr * 9.0 + t * 0.35, 4.0) * 0.55
                 + filigree(wa * N * 9.0 - rr * 5.0 - t * 0.22, 6.0) * 0.35;

    // 3) Woven lattice: crossing sine sheets in folded cartesian coords.
    float lattice = exp(-abs(sin(q.x * 16.0 + t * 0.4)
                           * sin(q.y * 16.0 - t * 0.3)) * 4.5) * 0.40;

    // 4) Petal envelope: a soft rose curve gives the sigil its silhouette.
    float rose  = 0.32 + 0.16 * cos(fa * N * 2.0)
                + 0.05 * sin(rr * 20.0 - t) * uMid;
    float petal = exp(-abs(rr - rose) * 9.0);

    // 5) Outer sparkle ring on the treble.
    float halo = exp(-abs(rr - (0.46 + 0.03 * sin(t * 0.7))) * 26.0)
               * (0.15 + 0.85 * uTreble)
               * (0.6 + 0.4 * sin(wa * N * 12.0 + t * 3.0));

    // Combine: ornament lines gated by the petal envelope + radial falloff.
    float env = 1.0 - smoothstep(0.15, 0.62, r);       // fade out at the edge
    float ink = (rings * 0.85 + spokes * 0.75 + lattice) * (0.35 + 0.85 * petal);
    ink = ink * env + halo * env;

    // Molten core: small, hot, breathing with bass.
    float core = exp(-r * (16.0 - 6.0 * uBass)) * (0.5 + 1.6 * uBass);
    ink += core;

    // Beat bloom: the whole sigil flares for one frame.
    ink *= 1.0 + 0.55 * uBeat;

    // ---- color --------------------------------------------------------------
    // Hue: gold/teal alchemy palette cycling slowly, shifting along the radius.
    float hue = fract(0.09 + t * 0.012 + rr * 0.22 + w * 0.06);
    float sat = clamp(0.85 - 0.65 * smoothstep(0.8, 2.2, ink), 0.0, 1.0);
    float val = clamp(ink, 0.0, 1.6);
    vec3 mandala = hsv2rgb(vec3(hue, sat, min(val, 1.0)))
                 + vec3(1.0, 0.95, 0.8) * max(val - 1.0, 0.0) * 0.6; // white-hot cores

    // ---- luminance-gated composite -------------------------------------------
    // Dark negative space stays transparent; bright filigree replaces the video,
    // plus a touch of additive glow so lines read over bright footage too.
    float mask = clamp(ink * 1.4, 0.0, 1.0);
    float k    = clamp(uIntensity, 0.0, 1.0);
    vec3 col = mix(base, mandala, k * mask);
    col += mandala * 0.25 * k * mask;

    FragColor = vec4(col, 1.0);
}
