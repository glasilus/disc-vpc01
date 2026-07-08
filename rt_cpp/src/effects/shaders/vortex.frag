#version 330 core
// Vortex Warp - спиральное вращение от центра.
// Пиксели у центра вращаются сильнее краевых → эффект воронки/водостока.
// На высокой интенсивности выглядит совершенно безумно.
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uTime;
uniform float uBass;

void main() {
    vec2  p    = vUV - 0.5;
    float r    = length(p);
    float falloff = exp(-r * r * (4.0 - uIntensity * 3.5)); // гауссово затухание от центра
    float angle   = (uIntensity + uBass * 0.8) * 8.0 * falloff;

    // Добавляем колебание по времени для пульсирующей воронки
    angle += sin(uTime * 3.0) * uIntensity * 1.5 * falloff;

    float s = sin(angle), c = cos(angle);
    vec2 rotated = vec2(p.x*c - p.y*s, p.x*s + p.y*c) + 0.5;

    // Сэмплируем с хроматической аберрацией прямо на искажённых координатах
    float ca = uIntensity * 0.01;
    float cr = texture(uTex, rotated + vec2(ca,  0.0)).r;
    float cg = texture(uTex, rotated).g;
    float cb = texture(uTex, rotated + vec2(-ca, 0.0)).b;

    FragColor = vec4(cr, cg, cb, 1.0);
}
