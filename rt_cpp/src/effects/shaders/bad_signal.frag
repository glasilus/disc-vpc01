#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uTime;
uniform vec2  uResolution;

float hash(float n) { return fract(sin(n) * 43758.5453); }

void main() {
    // Vertical noise bars
    float bar_x     = floor(vUV.x * uResolution.x / 8.0);
    float bar_noise = (hash(bar_x + floor(uTime * 12.0)) * 2.0 - 1.0) * uIntensity * 0.04;
    // Row shift
    float row       = floor(vUV.y * uResolution.y);
    float row_shift = (hash(row * 0.31 + uTime * 5.7) * 2.0 - 1.0) * uIntensity * 0.02;
    vec2  uv        = vec2(vUV.x + bar_noise + row_shift, vUV.y);
    FragColor = texture(uTex, fract(uv));
}
