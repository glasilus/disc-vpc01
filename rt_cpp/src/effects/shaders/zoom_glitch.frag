#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
void main() {
    float scale = 1.0 - uIntensity * 0.15;
    vec2 uv = (vUV - 0.5) * scale + 0.5;
    FragColor = texture(uTex, uv);
}
