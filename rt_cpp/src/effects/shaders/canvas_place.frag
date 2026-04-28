#version 330 core

// Places a source texture onto the canvas using one of four aspect modes.
// Pixels outside the source region are rendered as solid black (letterbox /
// pillarbox). All coordinate math is in normalized canvas UV.
//
//   uMode = 0  Contain — fit entirely inside canvas, letterbox/pillarbox
//   uMode = 1  Cover   — fill canvas, crop overflow
//   uMode = 2  Stretch — ignore aspect, fill canvas
//   uMode = 3  Native  — 1:1 pixel mapping centered on canvas

in  vec2 vUV;
out vec4 fragColor;

uniform sampler2D uTex;
uniform vec2 uSrcSize;     // native video wh
uniform vec2 uCanvasSize;  // canvas wh
uniform int  uMode;

void main() {
    if (uMode == 2) {
        // Stretch: fill canvas. Still V-flip the source sample — sws_scale is
        // top-left and GL is bottom-left, the same flip every other branch in
        // this shader applies. Without it Stretch shows the image upside down.
        fragColor = texture(uTex, vec2(vUV.x, 1.0 - vUV.y));
        return;
    }

    float srcA = uSrcSize.x / uSrcSize.y;
    float canA = uCanvasSize.x / uCanvasSize.y;

    // frac = fraction of canvas that the source occupies.
    // 1.0 on an axis means the source fills that axis exactly.
    vec2 frac;
    if (uMode == 3) {
        // native 1:1
        frac = uSrcSize / uCanvasSize;
    } else if (uMode == 0) {
        // contain
        if (srcA > canA) frac = vec2(1.0, canA / srcA);
        else             frac = vec2(srcA / canA, 1.0);
    } else {
        // cover
        if (srcA > canA) frac = vec2(srcA / canA, 1.0);
        else             frac = vec2(1.0, canA / srcA);
    }

    // Map canvas UV into source UV, centered.
    vec2 uv = (vUV - 0.5) / frac + 0.5;

    if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0)))) {
        fragColor = vec4(0.0, 0.0, 0.0, 1.0);
    } else {
        // sws_scale writes the source frame top-left, GL samples bottom-left.
        // Flip V here (and only here) so subsequent effect passes — which read
        // GL-convention FBO textures — don't double-flip the result.
        fragColor = texture(uTex, vec2(uv.x, 1.0 - uv.y));
    }
}
