#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// ---------------------------------------------------------------------------
// VIZ: PLASMA - классическое демосценовое плазменное поле.
// Четыре слоя синусоид, вычисленные на domain-warp-плоскости, дают плавное
// интерференционное поле; значение поля управляет оттенком через HSV-рампу,
// циклирующуюся во времени, так что результат читается как текучий жидкий цвет.
//   uMid    -> пространственная частота + глубина искажения (плотнее поле на мидах)
//   uBass   -> огибающая яркости (всё поле дышит вместе с низкими)
//   uTreble -> тонкое высокочастотное мерцание поверх канала value
//   uBeat   -> всплеск насыщенности/яркости на один кадр
// Композитинг по всему полю: mix(canvas, plasma, uIntensity).
// ---------------------------------------------------------------------------

uniform sampler2D uTex;
uniform vec2  uResolution;
uniform float uTime;
uniform float uIntensity;
uniform float uBass;
uniform float uMid;
uniform float uTreble;
uniform float uLevel;
uniform float uBeat;

vec3 hsv2rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

void main() {
    vec3 base = texture(uTex, vUV).rgb;

    // Центрированные координаты с поправкой на соотношение сторон, чтобы
    // пятна оставались круглыми.
    vec2 p = vUV - 0.5;
    p.x *= uResolution.x / uResolution.y;

    float t = uTime;

    // Миды раскрывают пространственную частоту и глубину искажения.
    float freq = 3.0 + 4.5 * uMid;
    float warp = 0.25 + 0.45 * uMid;

    // Domain warp: изгибаем плоскость перед сэмплированием слоёв синусоид.
    vec2 q = p;
    q += warp * vec2(sin(p.y * freq * 1.30 + t * 0.90),
                     cos(p.x * freq * 1.10 - t * 0.70));
    q += 0.5 * warp * vec2(sin((p.x + p.y) * freq * 0.70 - t * 0.50),
                           cos((p.x - p.y) * freq * 0.60 + t * 0.65));

    // Классическая сумма четырёх слоёв плазмы (диапазон примерно -4..4).
    float v = 0.0;
    v += sin(q.x * freq              + t * 1.00);
    v += sin(q.y * freq * 0.83       - t * 0.70);
    v += sin((q.x + q.y) * freq * 0.6 + t * 0.45);
    v += sin(length(q + vec2(sin(t * 0.30), cos(t * 0.23))) * freq * 1.4 - t * 0.85);
    v *= 0.25; // -> -1..1

    // Тонкое высокочастотное мерцание от верхних частот.
    v += uTreble * 0.12 * sin(dot(p, vec2(41.0, 37.0)) + t * 6.0)
                        * sin(dot(p, vec2(-29.0, 47.0)) - t * 5.0);

    // Оттенок циклирует во времени и следует за полем, поэтому полосы цвета текут.
    float hue = 0.5 + 0.5 * v;
    hue = fract(hue * 0.55 + t * 0.035);

    // Насыщенность немного проседает на гребнях (горячие ядра выглядят кремовыми).
    float sat = 0.85 - 0.30 * smoothstep(0.5, 1.0, abs(v)) + 0.10 * uBeat;

    // Value: мягкое базовое свечение, сильно пульсирующее вместе с басом.
    float val = 0.45 + 0.35 * (0.5 + 0.5 * sin(v * 3.14159 + t * 0.5));
    val *= 0.55 + 0.65 * uBass + 0.15 * uLevel;
    val += uBeat * 0.18;

    vec3 plasma = hsv2rgb(vec3(hue, clamp(sat, 0.0, 1.0), clamp(val, 0.0, 1.3)));

    // Мягкое виньетирование не даёт полю плоско срезаться в углах.
    plasma *= 1.0 - 0.35 * dot(p, p);

    // Смешение по всему полю: uIntensity == 0 возвращает нетронутую канву.
    vec3 col = mix(base, plasma, clamp(uIntensity, 0.0, 1.0));
    FragColor = vec4(col, 1.0);
}
