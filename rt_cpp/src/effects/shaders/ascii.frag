#version 330 core
// GPU ASCII - настоящий bitmap-шрифтовый ASCII-арт целиком на GPU.
// Атлас шрифта загружен как маленькая GL_RED текстура (см. effect_chain.cpp).
// Ноль нагрузки на CPU, ноль задержки. Один шейдерный проход.
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;       // кадр видео
uniform sampler2D uFontAtlas; // текстура 128×8: 16 символов по 8px шириной, 8px высотой
uniform vec2  uResolution;
uniform float uIntensity;
uniform float uColor;         // 0=монохром, 1=сохранить исходный цвет

// Шкала плотности ASCII (16 уровней, от тёмного к светлому по восприятию)
// Отображает яркость [0..1] в колонку символа в атласе [0..15]
// Символы упорядочены по визуальной плотности: @ # % = + - . пробел (и вариации)
int luma_to_char(float l) {
    int idx = int(clamp(l, 0.0, 0.999) * 16.0);
    return 15 - idx; // тёмная яркость → плотный символ (индекс 0), светлая → разрежённый (индекс 15)
}

void main() {
    // Размер ячейки в пикселях (8×8 даёт классический вид терминала)
    float cell = 8.0;
    vec2 cell_uv  = floor(vUV * uResolution / cell) * cell / uResolution;
    vec2 local_px = fract(vUV * uResolution / cell); // 0..1 внутри ячейки

    // Средняя яркость этой ячейки (низкочастотный фильтр)
    vec4  cell_col = texture(uTex, cell_uv + 0.5 * cell / uResolution);
    float luma_val = dot(cell_col.rgb, vec3(0.299, 0.587, 0.114));

    // Выбираем индекс символа по шкале плотности
    int char_idx = luma_to_char(luma_val);

    // Сэмплируем атлас шрифта: атлас 16 символов шириной, каждый 8px → ширина атласа 128px
    float atlas_u = (float(char_idx) + local_px.x) / 16.0;
    float atlas_v = local_px.y;  // 0=верх, 1=низ
    float glyph   = texture(uFontAtlas, vec2(atlas_u, atlas_v)).r;

    // Итоговый цвет: маска глифа × цвет ячейки (либо монохромный зелёный)
    vec3 fg_color = mix(vec3(0.0, 1.0, 0.2), cell_col.rgb, uColor);
    vec3 bg_color = vec3(0.0);
    vec3 ascii_col = mix(bg_color, fg_color, glyph);

    // Смешиваем ASCII с оригиналом по интенсивности
    FragColor = vec4(mix(cell_col.rgb, ascii_col, uIntensity), 1.0);
}
