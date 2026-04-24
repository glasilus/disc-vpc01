#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform vec2  uResolution;

// GPU pixel sort approximation: shift UV based on local luminance
float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

void main() {
    float px = 1.0 / uResolution.x;
    vec4  col = texture(uTex, vUV);
    float l   = luma(col.rgb);
    // Bright pixels get pushed right, dark stay — creates sort-like streaks
    float shift = l * uIntensity * 0.08;
    vec4  sorted = texture(uTex, vec2(vUV.x + shift, vUV.y));
    FragColor = mix(col, sorted, uIntensity);
}
