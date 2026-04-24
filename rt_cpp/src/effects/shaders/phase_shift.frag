#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uTime;

float hash(float n) { return fract(sin(n) * 43758.5453); }

void main() {
    float band_h = 0.05;
    float band   = floor(vUV.y / band_h);
    float dir    = (mod(band, 2.0) < 1.0) ? 1.0 : -1.0;
    float shift  = dir * uIntensity * 0.06 * hash(band + floor(uTime * 4.0));
    FragColor    = texture(uTex, vec2(vUV.x + shift, vUV.y));
}
