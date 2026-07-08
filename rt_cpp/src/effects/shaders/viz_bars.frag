#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// ---------------------------------------------------------------------------
// VIZ: BARS - 16-полосный спектр столбиками с шапками и отражением от пола.
// Экран делится на 16 колонок над базовой линией. Каждый столбик рисуется со
// сглаженными краями (smoothstep), тепловым градиентом (холодный у основания,
// горячий на вершине), яркой линией-шапкой на высоте бина и широким гауссовым
// свечением, накопленным по всем 16 бинам, так что громкие столбики
// растекаются светом в стороны. Ниже базовой линии то же поле отражается,
// сжимается и затухает, изображая глянцевый отражающий пол.
//   uBins  -> высоты столбиков       uBass -> усиление теплоты градиента
//   uBeat  -> вспышка на один кадр   uTreble -> яркость шапок
// Композитинг аддитивный, так что столбики светятся поверх видео.
// ---------------------------------------------------------------------------

uniform sampler2D uTex;
uniform vec2  uResolution;
uniform float uTime;
uniform float uIntensity;
uniform float uBass;
uniform float uTreble;
uniform float uLevel;
uniform float uBeat;
uniform float uBins[16];

vec3 hsv2rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

// Вычисляет поле столбиков в точке (x в 0..1, h = нормализованная высота над
// базовой линией). Возвращает накопленный свет. Вынесено в функцию, чтобы
// отражение могло переиспользовать этот же расчёт.
vec3 barField(float x, float h, float flash) {
    vec3 acc = vec3(0.0);

    // В какой мы колонке и в каком месте внутри неё?
    float fx  = x * 16.0;
    int   bi  = int(clamp(floor(fx), 0.0, 15.0));
    float cx  = fract(fx) - 0.5;              // -0.5 .. 0.5 внутри ячейки
    float v   = uBins[bi];

    // Сглаженное тело столбика: ~72% ширины ячейки, мягкие края.
    float halfW = 0.36;
    float aaX   = 16.0 / uResolution.x * 1.5; // ~1.5px в единицах ячейки
    float body  = 1.0 - smoothstep(halfW - aaX, halfW + aaX, abs(cx));

    // Сглаженный верхний край на высоте бина.
    float aaY   = 2.0 / uResolution.y / 0.72; // ~2px в единицах h
    float under = 1.0 - smoothstep(v - aaY, v + aaY, h);

    // Тепловой градиент: холодный оттенок у основания -> горячий у вершины столбика.
    float heat = clamp(h / max(v, 1e-3), 0.0, 1.0);
    float hue  = fract(0.62 - 0.55 * heat + uTime * 0.015); // синий -> красно-оранжевый
    float sat  = 0.9 - 0.55 * heat * heat;                  // белеет к вершине
    vec3 grad  = hsv2rgb(vec3(hue, sat, 0.55 + 0.45 * heat));
    grad *= 0.8 + 0.5 * uBass;

    acc += body * under * grad * (0.55 + 0.45 * v) * flash;

    // Яркая линия-шапка на самой вершине столбика.
    float cap = body * exp(-abs(h - v) * (uResolution.y * 0.045))
              * (1.2 + 1.6 * uTreble) * flash;
    acc += cap * hsv2rgb(vec3(fract(hue + 0.04), 0.25, 1.0)) * step(0.015, v);

    // Широкое мягкое свечение: каждый бин растекается светом в стороны
    // пропорционально своей энергии.
    for (int i = 0; i < 16; i++) {
        float bcx  = (float(i) + 0.5) / 16.0;
        float dx   = (x - bcx) * 16.0;
        float bv   = uBins[i];
        float g    = exp(-dx * dx * 2.2)
                   * exp(-max(h - bv, 0.0) * 9.0)
                   * bv * 0.10 * flash;
        acc += g * hsv2rgb(vec3(fract(0.62 - 0.45 * bv + uTime * 0.015), 0.8, 1.0));
    }

    return acc;
}

void main() {
    vec3 base = texture(uTex, vUV).rgb;

    float baseline = 0.24;             // линия пола для отражения
    float flash    = 1.0 + 0.6 * uBeat;

    vec3 viz = vec3(0.0);

    if (vUV.y >= baseline) {
        // Основные столбики над базовой линией.
        float h = (vUV.y - baseline) / (1.0 - baseline);
        viz = barField(vUV.x, h, flash);
    } else {
        // Отражённое, сжатое, затухающее отражение под базовой линией.
        float h    = (baseline - vUV.y) / baseline * 1.8;   // сжатие x1.8
        float fade = (1.0 - h / 1.8) * 0.30;                // затухает с глубиной
        // Лёгкая горизонтальная рябь для ощущения "мокрого пола".
        float wob = 0.004 * sin(vUV.y * 140.0 + uTime * 2.0) * uLevel;
        viz = barField(clamp(vUV.x + wob, 0.0, 1.0), h, flash) * fade;
    }

    // Тонкая светящаяся полоса на базовой линии связывает обе половины воедино.
    viz += exp(-abs(vUV.y - baseline) * uResolution.y * 0.15)
         * (0.10 + 0.25 * uLevel) * hsv2rgb(vec3(fract(uTime * 0.02), 0.4, 1.0));

    // Аддитивно поверх канвы; uIntensity == 0 возвращает нетронутый кадр.
    vec3 col = base + viz * clamp(uIntensity, 0.0, 1.0);
    FragColor = vec4(col, 1.0);
}
