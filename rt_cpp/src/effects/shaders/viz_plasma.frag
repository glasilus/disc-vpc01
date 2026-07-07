#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// ---------------------------------------------------------------------------
// VIZ: PLASMA - classic demoscene plasma field.
// Four layered sine waves evaluated on a domain-warped plane produce a
// smooth interference field; the field value drives hue through an HSV
// ramp that cycles over time, so the result reads as flowing liquid color.
//   uMid    -> spatial frequency + warp amount (busier field on mids)
//   uBass   -> brightness envelope (the whole field breathes with the low end)
//   uTreble -> fine high-frequency shimmer layered on the value channel
//   uBeat   -> one-frame saturation/brightness pop
// Composited full-field: mix(canvas, plasma, uIntensity).
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

vec3 hsv2rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

void main() {
    vec3 base = texture(uTex, vUV).rgb;

    // Centered, aspect-correct coordinates so blobs stay round.
    vec2 p = vUV - 0.5;
    p.x *= uResolution.x / uResolution.y;

    float t = uTime;

    // Mids open up the spatial frequency and the warp depth.
    float freq = 3.0 + 4.5 * uMid;
    float warp = 0.25 + 0.45 * uMid;

    // Domain warp: bend the plane before sampling the sine layers.
    vec2 q = p;
    q += warp * vec2(sin(p.y * freq * 1.30 + t * 0.90),
                     cos(p.x * freq * 1.10 - t * 0.70));
    q += 0.5 * warp * vec2(sin((p.x + p.y) * freq * 0.70 - t * 0.50),
                           cos((p.x - p.y) * freq * 0.60 + t * 0.65));

    // Classic four-layer plasma sum (range roughly -4..4).
    float v = 0.0;
    v += sin(q.x * freq              + t * 1.00);
    v += sin(q.y * freq * 0.83       - t * 0.70);
    v += sin((q.x + q.y) * freq * 0.6 + t * 0.45);
    v += sin(length(q + vec2(sin(t * 0.30), cos(t * 0.23))) * freq * 1.4 - t * 0.85);
    v *= 0.25; // -> -1..1

    // Fine treble shimmer: a delicate high-frequency ripple on the field.
    v += uTreble * 0.12 * sin(dot(p, vec2(41.0, 37.0)) + t * 6.0)
                        * sin(dot(p, vec2(-29.0, 47.0)) - t * 5.0);

    // Hue cycles with time and follows the field, so bands of color flow.
    float hue = 0.5 + 0.5 * v;
    hue = fract(hue * 0.55 + t * 0.035);

    // Saturation dips slightly at the crests (hot cores look creamy).
    float sat = 0.85 - 0.30 * smoothstep(0.5, 1.0, abs(v)) + 0.10 * uBeat;

    // Value: soft base glow that pulses hard with bass.
    float val = 0.45 + 0.35 * (0.5 + 0.5 * sin(v * 3.14159 + t * 0.5));
    val *= 0.55 + 0.65 * uBass + 0.15 * uLevel;
    val += uBeat * 0.18;

    vec3 plasma = hsv2rgb(vec3(hue, clamp(sat, 0.0, 1.0), clamp(val, 0.0, 1.3)));

    // Gentle vignette keeps the field from clipping flat at the corners.
    plasma *= 1.0 - 0.35 * dot(p, p);

    // Full-field blend: uIntensity == 0 returns the untouched canvas.
    vec3 col = mix(base, plasma, clamp(uIntensity, 0.0, 1.0));
    FragColor = vec4(col, 1.0);
}
