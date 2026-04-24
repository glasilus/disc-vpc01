#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uWhite;  // 1=white flash, 0=black flash
void main() {
    vec4 col = texture(uTex, vUV);
    vec3 flash_color = vec3(uWhite);
    FragColor = vec4(mix(col.rgb, flash_color, uIntensity * 0.7), 1.0);
}
