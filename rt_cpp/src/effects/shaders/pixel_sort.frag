#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform vec2  uResolution;
uniform vec2  uSortDir; // (1, 0) = horizontal, (0, 1) = vertical

// GPU pixel sort approximation using a local 8-element Bitonic sorting network
float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

#define SWAP(i, j) if (lumas[i] > lumas[j]) { \
    float tempL = lumas[i]; lumas[i] = lumas[j]; lumas[j] = tempL; \
    vec4 tempC = colors[i]; colors[i] = colors[j]; colors[j] = tempC; \
}

void main() {
    vec2 step = uSortDir / uResolution;
    // Determine step size based on intensity. Higher = wider sorting blocks
    float step_scale = 1.0 + uIntensity * 12.0;
    
    // Sample 8 pixels locally along the sorting direction
    vec4 colors[8];
    float lumas[8];
    for (int i = 0; i < 8; i++) {
        vec2 uv_offset = vUV + step * float(i - 4) * step_scale;
        colors[i] = texture(uTex, uv_offset);
        lumas[i] = luma(colors[i].rgb);
    }

    // 8-element Bitonic sorting network (19 comparisons)
    SWAP(0, 1); SWAP(2, 3); SWAP(4, 5); SWAP(6, 7);
    SWAP(0, 2); SWAP(1, 3); SWAP(4, 6); SWAP(5, 7);
    SWAP(1, 2); SWAP(5, 6);
    SWAP(0, 4); SWAP(1, 5); SWAP(2, 6); SWAP(3, 7);
    SWAP(2, 4); SWAP(3, 5);
    SWAP(1, 2); SWAP(3, 4); SWAP(5, 6);
    SWAP(1, 3); SWAP(4, 5);
    SWAP(2, 3);

    // Map the current pixel to one of the sorted slots based on the local fraction
    float coord_projected = dot(vUV * uResolution, uSortDir);
    float frac = fract(coord_projected / (8.0 * step_scale));
    int index = int(frac * 8.0);
    index = clamp(index, 0, 7);
    
    vec4 sorted_col = colors[index];
    FragColor = mix(texture(uTex, vUV), sorted_col, uIntensity);
}
