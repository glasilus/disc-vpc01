#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;  // 0..1
void main() {
    float shift = uIntensity * 0.04;
    float r = texture(uTex, vUV + vec2(shift, 0.0)).r;
    float g = texture(uTex, vUV).g;
    float b = texture(uTex, vUV - vec2(shift, 0.0)).b;
    FragColor = vec4(r, g, b, 1.0);
}
