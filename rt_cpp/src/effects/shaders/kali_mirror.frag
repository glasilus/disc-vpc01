#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
void main() {
    vec2 uv = vUV;
    // Horizontal mirror
    if (uIntensity > 0.3)
        uv.x = (uv.x > 0.5) ? 1.0 - uv.x : uv.x;
    // Vertical mirror
    if (uIntensity > 0.6)
        uv.y = (uv.y > 0.5) ? 1.0 - uv.y : uv.y;
    // Diagonal invert at high intensity
    if (uIntensity > 0.85)
        uv = 1.0 - uv;
    FragColor = texture(uTex, uv);
}
