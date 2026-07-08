#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// ---------------------------------------------------------------------------
// VIZ: RADIAL - полярный спектроанализатор ("аудио-солнце").
// 16 бинов спектра раскладываются лепестками вокруг центра экрана. Для
// каждого бина накапливается угловой гауссов лепесток, радиальная длина
// которого - значение бина, плюс горячий светящийся кончик на внешнем крае лепестка.
//   uBins  -> длина лепестка (радиус) по каждому углу
//   uBass  -> пульсирующее яркое ядро + дыхание радиуса всего кольца
//   uTreble-> искры на кончиках лепестков
//   uTime  -> медленное вращение; uBeat подталкивает вращение и вспышку кольца
// Композитинг аддитивный, так что это читается как свет над видео.
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
uniform float uBins[16];

#define TAU 6.28318530718

vec3 hsv2rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(0.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

void main() {
    vec3 base = texture(uTex, vUV).rgb;

    vec2 p = vUV - 0.5;
    p.x *= uResolution.x / uResolution.y;

    float r   = length(p);
    // Медленное вращение, подталкиваемое на битах, слегка покачивается на мидах.
    float rot = uTime * 0.15 + uBeat * 0.35 + 0.10 * sin(uTime * 0.40) * uMid;
    float ang = atan(p.y, p.x) + rot;

    float inner = 0.055 + 0.02 * uBass;              // мёртвая зона вокруг ядра
    float reach = 0.30 + 0.06 * uBass;               // макс. вытяжение лепестка

    vec3 viz = vec3(0.0);

    // --- 16 лепестков спектра -------------------------------------------------
    for (int i = 0; i < 16; i++) {
        float v  = uBins[i];
        float ci = (float(i) + 0.5) * (TAU / 16.0);  // угол центра лепестка

        // Кратчайшее угловое расстояние (по кругу) до этого лепестка.
        float d = ang - ci;
        d = mod(d + 3.14159265, TAU) - 3.14159265;

        // Угловой гаусс: лепестки становятся чуть толще, когда громче.
        float width = 0.10 + 0.06 * v;
        float petal = exp(-(d * d) / (width * width));

        float binR = inner + v * reach;              // радиус этого лепестка

        // Мягкая заполненная спица от внутреннего кольца до binR.
        float fill = smoothstep(inner - 0.01, inner + 0.02, r)
                   * (1.0 - smoothstep(binR - 0.05, binR + 0.012, r));

        // Горячий кончик на внешнем крае лепестка (верхние частоты добавляют искры).
        float tip = exp(-abs(r - binR) * (38.0 - 10.0 * v))
                  * (0.9 + 1.4 * uTreble * v);

        // Оттенок шагает по кольцу и дрейфует во времени.
        vec3 cA = hsv2rgb(vec3(fract(float(i) / 16.0 + uTime * 0.03), 0.85, 1.0));
        vec3 cB = hsv2rgb(vec3(fract(float(i) / 16.0 + uTime * 0.03 + 0.10), 0.55, 1.0));

        viz += petal * (fill * (0.28 + 0.55 * v) * cA + tip * v * cB);
    }

    // --- пульсирующее ядро -------------------------------------------------------
    float core = exp(-r * (14.0 - 6.0 * uBass)) * (0.35 + 1.5 * uBass);
    viz += core * hsv2rgb(vec3(fract(uTime * 0.05), 0.35, 1.0));

    // --- хроматический ободок: три слегка смещённых кольца = спектральная кайма -------
    float rimR = inner + reach * (0.55 + 0.25 * uLevel) + 0.015 * sin(uTime * 0.8);
    float flash = 1.0 + 1.2 * uBeat;
    viz.r += exp(-abs(r - (rimR + 0.008)) * 90.0) * 0.30 * flash;
    viz.g += exp(-abs(r -  rimR         ) * 90.0) * 0.30 * flash;
    viz.b += exp(-abs(r - (rimR - 0.008)) * 90.0) * 0.30 * flash;

    // Аддитивное свечение над канвой; uIntensity == 0 оставляет её нетронутой.
    vec3 col = base + viz * clamp(uIntensity, 0.0, 1.0);
    FragColor = vec4(col, 1.0);
}
