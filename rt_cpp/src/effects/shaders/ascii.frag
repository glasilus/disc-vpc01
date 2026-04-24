#version 330 core
// GPU ASCII — true bitmap-font ASCII art entirely on GPU.
// Font atlas uploaded as a tiny GL_RED texture (see effect_chain.cpp).
// Zero CPU overhead, zero latency. One shader pass.
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;       // video frame
uniform sampler2D uFontAtlas; // 128×8 texture: 16 chars × 8px wide, 8px tall
uniform vec2  uResolution;
uniform float uIntensity;
uniform float uColor;         // 0=mono, 1=keep original color

// ASCII density ramp (16 levels, darkest → lightest perceived weight)
// Maps luminance [0..1] → character column in font atlas [0..15]
// Characters ordered by visual density: @ # % = + - . space (and variants)
int luma_to_char(float l) {
    // 16 steps
    int idx = int(clamp(l, 0.0, 0.999) * 16.0);
    return 15 - idx; // dark luma → dense char (index 0), bright → sparse (index 15)
}

void main() {
    // Cell size in pixels (8×8 gives classic terminal look)
    float cell = 8.0;
    vec2 cell_uv  = floor(vUV * uResolution / cell) * cell / uResolution;
    vec2 local_px = fract(vUV * uResolution / cell); // 0..1 within cell

    // Sample average luminance of this cell (low-pass)
    vec4  cell_col = texture(uTex, cell_uv + 0.5 * cell / uResolution);
    float luma_val = dot(cell_col.rgb, vec3(0.299, 0.587, 0.114));

    // Pick character index from density ramp
    int char_idx = luma_to_char(luma_val);

    // Sample font atlas: atlas is 16 chars wide, each 8px → atlas width 128px
    float atlas_u = (float(char_idx) + local_px.x) / 16.0;
    float atlas_v = local_px.y;  // 0=top, 1=bottom
    float glyph   = texture(uFontAtlas, vec2(atlas_u, atlas_v)).r;

    // Final color: glyph mask × cell color (or monochrome green)
    vec3 fg_color = mix(vec3(0.0, 1.0, 0.2), cell_col.rgb, uColor);
    vec3 bg_color = vec3(0.0);
    vec3 ascii_col = mix(bg_color, fg_color, glyph);

    // Blend ASCII with original based on intensity
    FragColor = vec4(mix(cell_col.rgb, ascii_col, uIntensity), 1.0);
}
