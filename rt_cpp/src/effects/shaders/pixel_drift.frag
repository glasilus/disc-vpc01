#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uTime;

float hash(float n) { return fract(sin(n * 127.1 + uTime * 311.7) * 43758.5453); }

void main() {
    float row   = floor(vUV.y * 480.0);
    float noise = (hash(row) * 2.0 - 1.0) * uIntensity * 0.05;
    FragColor   = texture(uTex, vec2(vUV.x + noise, vUV.y));
}
