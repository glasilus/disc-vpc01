#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
void main() {
    vec2 uv = vUV;
    // Горизонтальное зеркало
    if (uIntensity > 0.3)
        uv.x = (uv.x > 0.5) ? 1.0 - uv.x : uv.x;
    // Вертикальное зеркало
    if (uIntensity > 0.6)
        uv.y = (uv.y > 0.5) ? 1.0 - uv.y : uv.y;
    // Диагональная инверсия на высокой интенсивности
    if (uIntensity > 0.85)
        uv = 1.0 - uv;
    FragColor = texture(uTex, uv);
}
