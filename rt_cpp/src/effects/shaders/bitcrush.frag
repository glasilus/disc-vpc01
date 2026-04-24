#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
void main() {
    float levels = mix(256.0, 4.0, uIntensity);
    vec4 col = texture(uTex, vUV);
    vec3 crushed = floor(col.rgb * levels) / levels;
    FragColor = vec4(crushed, 1.0);
}
