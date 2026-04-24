#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uTime;

float hash(float n) { return fract(sin(n) * 43758.5453123); }

void main() {
    float row     = floor(vUV.y * 480.0);
    float noise   = hash(row + uTime * 37.3) * 2.0 - 1.0;
    float shift   = noise * uIntensity * 0.03;
    float lum_noise = (hash(row * 3.7 + uTime * 11.0) - 0.5) * uIntensity * 0.12;

    vec4 col = texture(uTex, vec2(vUV.x + shift, vUV.y));
    col.rgb += vec3(lum_noise);
    FragColor = clamp(col, 0.0, 1.0);
}
