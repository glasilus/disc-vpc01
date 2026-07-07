#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// ---------------------------------------------------------------------------
// VIZ: RADIAL - polar spectrum analyzer ("audio sun").
// The 16 spectrum bins are laid out as petals around the screen center.
// For every bin we accumulate an angular-gaussian petal whose radial reach
// is the bin value, plus a hot glowing tip riding at the petal's outer edge.
//   uBins  -> petal length (radius) per angle
//   uBass  -> pulsing bright core + overall ring radius breathing
//   uTreble-> sparkle on the petal tips
//   uTime  -> slow rotation; uBeat kicks the rotation and flashes the ring
// Composited additively so it reads as light glowing over the video.
// ---------------------------------------------------------------------------

uniform sampler2D uTex;
uniform vec2  uResolution;
uniform float uTime;
uniform float uIntensity;
uniform float uBass;
uniform float uMid;
uniform float uTreble;
uniform float uLevel;
uniform float uBeat;
uniform float uBins[16];

#define TAU 6.28318530718

vec3 hsv2rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

void main() {
    vec3 base = texture(uTex, vUV).rgb;

    vec2 p = vUV - 0.5;
    p.x *= uResolution.x / uResolution.y;

    float r   = length(p);
    // Slow rotation, kicked forward on beats, wobbling gently with mids.
    float rot = uTime * 0.15 + uBeat * 0.35 + 0.10 * sin(uTime * 0.40) * uMid;
    float ang = atan(p.y, p.x) + rot;

    float inner = 0.055 + 0.02 * uBass;              // dead zone around the core
    float reach = 0.30 + 0.06 * uBass;               // max petal extension

    vec3 viz = vec3(0.0);

    // --- 16 spectrum petals -------------------------------------------------
    for (int i = 0; i < 16; i++) {
        float v  = uBins[i];
        float ci = (float(i) + 0.5) * (TAU / 16.0);  // petal center angle

        // Shortest wrapped angular distance to this petal.
        float d = ang - ci;
        d = mod(d + 3.14159265, TAU) - 3.14159265;

        // Angular gaussian: petals get slightly fatter when they are loud.
        float width = 0.10 + 0.06 * v;
        float petal = exp(-(d * d) / (width * width));

        float binR = inner + v * reach;              // this petal's radius

        // Soft filled spoke from the inner ring out to binR.
        float fill = smoothstep(inner - 0.01, inner + 0.02, r)
                   * (1.0 - smoothstep(binR - 0.05, binR + 0.012, r));

        // Hot tip riding on the petal's outer edge (treble adds sparkle).
        float tip = exp(-abs(r - binR) * (38.0 - 10.0 * v))
                  * (0.9 + 1.4 * uTreble * v);

        // Hue walks around the ring and drifts over time.
        vec3 cA = hsv2rgb(vec3(fract(float(i) / 16.0 + uTime * 0.03), 0.85, 1.0));
        vec3 cB = hsv2rgb(vec3(fract(float(i) / 16.0 + uTime * 0.03 + 0.10), 0.55, 1.0));

        viz += petal * (fill * (0.28 + 0.55 * v) * cA + tip * v * cB);
    }

    // --- pulsing core -------------------------------------------------------
    float core = exp(-r * (14.0 - 6.0 * uBass)) * (0.35 + 1.5 * uBass);
    viz += core * hsv2rgb(vec3(fract(uTime * 0.05), 0.35, 1.0));

    // --- chromatic rim: three slightly offset rings = spectral fringe -------
    float rimR = inner + reach * (0.55 + 0.25 * uLevel) + 0.015 * sin(uTime * 0.8);
    float flash = 1.0 + 1.2 * uBeat;
    viz.r += exp(-abs(r - (rimR + 0.008)) * 90.0) * 0.30 * flash;
    viz.g += exp(-abs(r -  rimR         ) * 90.0) * 0.30 * flash;
    viz.b += exp(-abs(r - (rimR - 0.008)) * 90.0) * 0.30 * flash;

    // Additive glow over the canvas; uIntensity == 0 leaves it untouched.
    vec3 col = base + viz * clamp(uIntensity, 0.0, 1.0);
    FragColor = vec4(col, 1.0);
}
