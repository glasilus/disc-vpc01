#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform vec2  uResolution;
void main() {
    vec4 col = texture(uTex, vUV);
    float px = 1.0 / uResolution.x;
    float spread = uIntensity * 12.0;
    float bleed_r = 0.0;
    for (float i = 1.0; i <= 6.0; i += 1.0)
        bleed_r += texture(uTex, vUV + vec2(i * px * spread / 6.0, 0.0)).r;
    bleed_r /= 6.0;
    FragColor = vec4(mix(col.r, bleed_r, uIntensity * 0.7), col.g, col.b, 1.0);
}
