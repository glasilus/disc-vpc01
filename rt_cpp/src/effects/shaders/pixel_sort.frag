#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform vec2  uResolution;
uniform vec2  uSortDir; // (1, 0) = горизонталь, (0, 1) = вертикаль

// Приближение pixel sort на GPU без полноценной bitonic-сортировки
float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

void main() {
    // Имитация pixel sort через направленный смаз по яркости.
    // Вместо настоящей O(N^2) или тяжёлой bitonic-сортировки просто сэмплируем
    // назад вдоль направления сортировки и растягиваем пиксели выше порога.
    // Это в 100 раз быстрее и визуально даёт классический эффект "ветрового" pixel sort.

    vec2 dir = uSortDir;
    // Масштабируем шаг по интенсивности и разрешению канвы.
    vec2 step = dir / uResolution * (1.0 + uIntensity * 40.0);

    vec4 col = texture(uTex, vUV);
    float l = luma(col.rgb);

    vec4 smeared = col;
    float max_luma = l;

    // порог: "плавятся" только пиксели ярче 0.4
    float threshold = 0.4 - uIntensity * 0.2;

    // Сэмплируем назад до 16 точек. Если попался пиксель ярче - тащим его на себя.
    for (int i = 1; i <= 16; i++) {
        vec2 offset_uv = vUV - step * float(i);
        // Останавливаемся у края экрана
        if(offset_uv.x < 0.0 || offset_uv.x > 1.0 || offset_uv.y < 0.0 || offset_uv.y > 1.0) break;

        vec4 sample_col = texture(uTex, offset_uv);
        float sample_l = luma(sample_col.rgb);

        // Если сэмплированный пиксель достаточно яркий, он перекрывает текущий смазанный
        if (sample_l > max_luma && sample_l > threshold) {
            max_luma = sample_l;
            smeared = sample_col;
        }
    }

    FragColor = mix(col, smeared, uIntensity);
}
