#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
void main() {
    float cells = mix(64.0, 8.0, uIntensity);
    vec2 uv = floor(vUV * cells) / cells;
    FragColor = texture(uTex, uv);
}
