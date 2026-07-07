#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// Wrong Motion Vector — "bloom smear" datamosh. Emulates a decoder applying
// corrupted motion vectors: each macroblock drags a chunk of the PREVIOUS
// output in a bogus, slowly-drifting direction. Because uPrev is the engine's
// last output, the displaced smear is re-displaced every frame and blooms into
// long swimming streaks. A little chroma split along the vector sells the
// decaying-codec feel.
uniform sampler2D uTex;
uniform sampler2D uPrev;
uniform float uIntensity;
uniform float uTime;
uniform vec2  uResolution;

// Signed 2D hash in [-1,1].
vec2 hash22(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return fract(sin(p) * 43758.5453) * 2.0 - 1.0;
}

void main() {
    vec3 cur = texture(uTex, vUV).rgb;

    // ~24 px macroblocks.
    vec2 grid = uResolution / 24.0;
    vec2 bid  = floor(vUV * grid);

    // Bogus motion vector for this block: a hashed direction that drifts slowly
    // in time, magnitude up to ~5% of the frame at full intensity.
    vec2 dir = hash22(bid + floor(uTime * 1.3) * 0.31);
    dir += 0.5 * hash22(bid * 1.7 - vec2(uTime * 0.2));   // wobble
    float mag = 0.006 + 0.045 * uIntensity;
    vec2 mv = dir * mag;

    // Sample the previous output dragged by the fake vector, with a little
    // chroma separation along the vector.
    vec2 ca = mv * 0.25;
    vec3 smear;
    smear.r = texture(uPrev, vUV + mv + ca).r;
    smear.g = texture(uPrev, vUV + mv     ).g;
    smear.b = texture(uPrev, vUV + mv - ca).b;

    // Bloom: the displaced previous dominates more as intensity rises, so the
    // streak feeds forward and lengthens every frame. Keep a floor of the live
    // frame so it never fully detaches from reality.
    float feed   = 0.35 + 0.55 * uIntensity;
    vec3  moshed = mix(cur, smear, feed);
    moshed = max(moshed, cur * 0.15);

    // uIntensity == 0 -> exactly the untouched current frame.
    FragColor = vec4(mix(cur, moshed, clamp(uIntensity, 0.0, 1.0)), 1.0);
}
