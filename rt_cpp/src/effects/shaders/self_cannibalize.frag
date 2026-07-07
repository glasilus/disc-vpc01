#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// Self Cannibalize — the image eats itself. A displacement field is built from
// the PREVIOUS output's own content (its luma gradient) and used to warp the
// frame by its own shapes; because uPrev is the last output, the warp compounds
// every frame into flowing, self-consuming trails. uPrev2 adds a second,
// larger-scale pull for depth so bright/edgy regions devour their neighbours.
uniform sampler2D uTex;
uniform sampler2D uPrev;
uniform sampler2D uPrev2;
uniform float uIntensity;
uniform float uTime;
uniform vec2  uResolution;

float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

void main() {
    vec3 cur = texture(uTex, vUV).rgb;
    vec2 px  = 1.0 / uResolution;

    // Gradient of the previous output's luma. The frame flows along its own
    // contours, so bright shapes bleed into the darker regions beside them.
    float lC = luma(texture(uPrev, vUV).rgb);
    float lx = luma(texture(uPrev, vUV + vec2(px.x, 0.0)).rgb) - lC;
    float ly = luma(texture(uPrev, vUV + vec2(0.0, px.y)).rgb) - lC;
    vec2  grad = vec2(lx, ly);

    // Larger-scale pull from two frames ago (RG remapped to a direction) with a
    // slow rotation so the corruption swirls rather than sliding in a line.
    vec2  pull = texture(uPrev2, vUV).rg * 2.0 - 1.0;
    float a    = uTime * 0.15;
    pull = mat2(cos(a), -sin(a), sin(a), cos(a)) * pull;

    // The displacement field. grad dominates the fine self-eating detail; pull
    // adds flowing motion. Everything scales to zero with intensity.
    vec2 disp = (grad * 0.09 + pull * 0.035) * uIntensity;

    // Warp both the current frame and the previous output by the field and mix;
    // the previous term is what makes the corruption persist and flow.
    vec3 warpCur  = texture(uTex,  vUV + disp).rgb;
    vec3 warpPrev = texture(uPrev, vUV + disp * 1.7).rgb;
    vec3 moshed   = mix(warpCur, warpPrev, 0.45 + 0.35 * uIntensity);

    // uIntensity == 0 -> exactly the untouched current frame.
    FragColor = vec4(mix(cur, moshed, clamp(uIntensity, 0.0, 1.0)), 1.0);
}
