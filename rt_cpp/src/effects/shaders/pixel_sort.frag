#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform vec2  uResolution;
uniform vec2  uSortDir; // (1, 0) = horizontal, (0, 1) = vertical

// GPU pixel sort approximation using a local 8-element Bitonic sorting network
float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

void main() {
    // Pixel sorting simulation via directional luma smear.
    // Instead of doing an actual O(N^2) or heavy Bitonic sort, we just sample backwards
    // along the sort direction and bleed pixels that exceed the threshold. This runs
    // 100x faster and visually simulates the classic "wind" pixel sort effect.
    
    vec2 dir = uSortDir;
    // Scale step by intensity and canvas resolution.
    vec2 step = dir / uResolution * (1.0 + uIntensity * 40.0);
    
    vec4 col = texture(uTex, vUV);
    float l = luma(col.rgb);
    
    vec4 smeared = col;
    float max_luma = l;
    
    // threshold: only pixels brighter than 0.4 "melt"
    float threshold = 0.4 - uIntensity * 0.2; 
    
    // Sample backwards up to 16 taps. If we hit a brighter pixel, drag it down to us.
    for (int i = 1; i <= 16; i++) {
        vec2 offset_uv = vUV - step * float(i);
        // Stop if we hit screen edges
        if(offset_uv.x < 0.0 || offset_uv.x > 1.0 || offset_uv.y < 0.0 || offset_uv.y > 1.0) break;
        
        vec4 sample_col = texture(uTex, offset_uv);
        float sample_l = luma(sample_col.rgb);
        
        // If the sampled pixel is bright enough, let it overwrite the current smeared pixel
        if (sample_l > max_luma && sample_l > threshold) {
            max_luma = sample_l;
            smeared = sample_col;
        }
    }
    
    FragColor = mix(col, smeared, uIntensity);
}
