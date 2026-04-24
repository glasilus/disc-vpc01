#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uTime;

float hash2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7)) + uTime * 17.3) * 43758.5453); }

void main() {
    float block_size = mix(0.1, 0.02, uIntensity);
    vec2  block      = floor(vUV / block_size);
    float r          = hash2(block);
    vec2  uv         = vUV;
    if (r < uIntensity * 0.4) {
        // Shift this block
        vec2 shift = vec2(hash2(block + 0.5) - 0.5, hash2(block + 1.5) - 0.5) * 0.15 * uIntensity;
        uv += shift;
    }
    FragColor = texture(uTex, fract(uv));
}
