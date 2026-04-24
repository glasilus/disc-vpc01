#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
void main() {
    vec4 col = texture(uTex, vUV);
    FragColor = vec4(mix(col.rgb, 1.0 - col.rgb, uIntensity), 1.0);
}
