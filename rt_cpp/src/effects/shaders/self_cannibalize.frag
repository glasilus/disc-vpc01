#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// Self Cannibalize - изображение поедает само себя. Поле смещений строится
// из содержимого ПРЕДЫДУЩЕГО вывода (его градиента яркости) и искажает кадр
// его же формами; так как uPrev - это предыдущий вывод самого движка,
// искажение накапливается от кадра к кадру в текущие самопоглощающие
// потоки. uPrev2 добавляет второе, более крупномасштабное притяжение для
// глубины, чтобы яркие/контрастные области поглощали соседние.
uniform sampler2D uTex;
uniform sampler2D uPrev;
uniform sampler2D uPrev2;
uniform float uIntensity;
uniform float uTime;
uniform vec2  uResolution;

float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

void main() {
    vec3 cur = texture(uTex, vUV).rgb;
    vec2 px  = 1.0 / uResolution;

    // Градиент яркости предыдущего вывода. Кадр течёт вдоль собственных
    // контуров, поэтому яркие формы растекаются в соседние тёмные области.
    float lC = luma(texture(uPrev, vUV).rgb);
    float lx = luma(texture(uPrev, vUV + vec2(px.x, 0.0)).rgb) - lC;
    float ly = luma(texture(uPrev, vUV + vec2(0.0, px.y)).rgb) - lC;
    vec2  grad = vec2(lx, ly);

    // Более крупномасштабное притяжение из кадра 2 назад (RG переведён в
    // направление) с медленным вращением, чтобы порча закручивалась, а не
    // скользила по прямой.
    vec2  pull = texture(uPrev2, vUV).rg * 2.0 - 1.0;
    float a    = uTime * 0.15;
    pull = mat2(cos(a), -sin(a), sin(a), cos(a)) * pull;

    // Поле смещений. grad задаёт тонкую деталь самопоедания; pull добавляет
    // текучее движение. Всё стремится к нулю при нулевой интенсивности.
    vec2 disp = (grad * 0.09 + pull * 0.035) * uIntensity;

    // Искажаем полем и текущий кадр, и предыдущий вывод, затем смешиваем;
    // именно слагаемое с предыдущим кадром даёт порче держаться и течь.
    vec3 warpCur  = texture(uTex,  vUV + disp).rgb;
    vec3 warpPrev = texture(uPrev, vUV + disp * 1.7).rgb;
    vec3 moshed   = mix(warpCur, warpPrev, 0.45 + 0.35 * uIntensity);

    // uIntensity == 0 -> ровно нетронутый текущий кадр.
    FragColor = vec4(mix(cur, moshed, clamp(uIntensity, 0.0, 1.0)), 1.0);
}
