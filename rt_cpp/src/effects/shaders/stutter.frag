#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform sampler2D uPrev;
uniform float uIntensity;
void main() {
    vec4 cur  = texture(uTex,  vUV);
    vec4 prev = texture(uPrev, vUV);
    FragColor = mix(cur, prev, uIntensity * 0.65);
}
