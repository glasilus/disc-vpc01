#version 330 core
// Self-Displacement - использует RGB ПРЕДЫДУЩЕГО кадра как карту смещений
// для ТЕКУЩЕГО кадра. Изображение буквально поедает само себя. При
// аудио-реактивной интенсивности даёт текучие глитч-хвосты, неотличимые
// от datamosh. Высокая интенсивность + включённый feedback = полный распад реальности.
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;    // текущий кадр
uniform sampler2D uPrev;   // кадр 1 назад (источник смещения)
uniform sampler2D uPrev2;  // кадр 2 назад (добавляет временную глубину)
uniform float uIntensity;
uniform float uTime;

void main() {
    // Каналы RG предыдущего кадра как вектор смещения XY
    vec4 disp_src  = texture(uPrev,  vUV);
    vec4 disp_src2 = texture(uPrev2, vUV);

    // Центрируем смещение: [0,1] → [-0.5, 0.5]
    vec2 d1 = (disp_src.rg  - 0.5) * uIntensity * 0.15;
    vec2 d2 = (disp_src2.rb - 0.5) * uIntensity * 0.07;

    // Многомасштабное смещение: крупный план из prev2, тонкая деталь из prev
    vec2 disp = d1 + d2;

    // Сэмплируем текущий кадр со смещением по содержимому prev
    vec4 displaced = texture(uTex, fract(vUV + disp));

    // Подмешиваем слегка смещённую версию prev для эффекта призрака
    vec4 ghost = texture(uPrev, fract(vUV + d1 * 0.3));
    float ghost_blend = uIntensity * 0.35;

    FragColor = mix(displaced, ghost, ghost_blend);
}
