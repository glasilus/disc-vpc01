#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform sampler2D uAccum;
uniform float uIntensity;
void main() {
    vec4 cur   = texture(uTex,   vUV);
    vec4 accum = texture(uAccum, vUV);
    float blend = mix(0.3, 0.85, uIntensity);
    FragColor = mix(cur, accum, blend);
}
