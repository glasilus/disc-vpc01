#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform vec2 uResolution;
void main() {
    vec4 col = texture(uTex, vUV);
    float line = mod(floor(vUV.y * uResolution.y), 2.0);
    float dark = mix(1.0, 0.4, uIntensity * line);
    FragColor = vec4(col.rgb * dark, 1.0);
}
