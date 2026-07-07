#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// P-Frame Lag - "block freeze" datamosh. Emulates a decoder that lost its
// P-frames: the image only refreshes where local motion is detected. Per
// macroblock we compare the luma of uTex vs uPrev; blocks whose change is
// below a (breathing) threshold output uPrev instead of uTex. Because uPrev
// is the engine's previous OUTPUT, a frozen block keeps outputting its own
// stale pixels frame after frame - the freeze compounds, so moving subjects
// smear the still background into a stuck mosaic behind them.
uniform sampler2D uTex;
uniform sampler2D uPrev;
uniform float uIntensity;
uniform float uTime;
uniform vec2  uResolution;

float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 34.45);
    return fract(p.x * p.y);
}

// Luma difference between current and previous output at a point
float chg(vec2 uv) {
    return abs(luma(texture(uTex, uv).rgb) - luma(texture(uPrev, uv).rgb));
}

void main() {
    vec3 cur = texture(uTex, vUV).rgb;

    // ~20 px macroblock grid derived from resolution
    vec2 grid = uResolution / 20.0;
    vec2 bid  = floor(vUV * grid);
    vec2 bctr = (bid + 0.5) / grid;

    // Estimate per-block motion energy: center + 4 inner taps
    vec2 o = 0.3 / grid;
    float diff = chg(bctr)
               + chg(bctr + vec2( o.x,  o.y))
               + chg(bctr + vec2(-o.x,  o.y))
               + chg(bctr + vec2( o.x, -o.y))
               + chg(bctr + vec2(-o.x, -o.y));
    diff *= 0.2;

    // Threshold rises with intensity (more blocks starve) and breathes with
    // time, dephased per block so freezing crawls across the frame.
    float breathe = 0.8 + 0.4 * sin(uTime * 0.9 + hash21(bid) * 6.2831);
    float th = mix(0.015, 0.11, uIntensity) * breathe;

    // freeze = 1 where the block is static (below threshold). Soft edge so
    // blocks thaw/refreeze without hard popping.
    float freeze = 1.0 - smoothstep(th, th * 1.8, diff);

    // Spatial feather: blend in the per-pixel change so block borders between
    // frozen and live regions don't form a perfectly hard grid.
    float pixFreeze = 1.0 - smoothstep(th, th * 1.8, chg(vUV));
    freeze = mix(freeze, pixFreeze, 0.25);

    // A few blocks randomly refuse to update entirely (dropped-slice feel)
    float stuck = step(0.92, hash21(bid + floor(uTime * 0.7) * 0.613));
    freeze = max(freeze, stuck * uIntensity);

    vec3 frozen = texture(uPrev, vUV).rgb;
    vec3 moshed = mix(cur, frozen, freeze);

    // uIntensity == 0 -> exactly the untouched current frame
    FragColor = vec4(mix(cur, moshed, clamp(uIntensity, 0.0, 1.0)), 1.0);
}
