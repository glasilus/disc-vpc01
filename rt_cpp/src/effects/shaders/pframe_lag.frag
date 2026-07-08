#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// P-Frame Lag - datamosh с "застыванием блоков". Имитирует декодер,
// потерявший P-кадры: картинка обновляется только там, где детектировано
// локальное движение. Для каждого макроблока сравниваем яркость uTex и
// uPrev; блоки с изменением ниже (дышащего) порога выводят uPrev вместо
// uTex. Так как uPrev - это предыдущий ВЫВОД самого движка, застывший блок
// продолжает выдавать свои же старые пиксели кадр за кадром - застывание
// накапливается, и движущиеся объекты размазывают неподвижный фон в
// застрявшую мозаику позади себя.
uniform sampler2D uTex;
uniform sampler2D uPrev;
uniform float uIntensity;
uniform float uTime;
uniform vec2  uResolution;

float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 34.45);
    return fract(p.x * p.y);
}

// Разница яркости между текущим и предыдущим выводом в точке
float chg(vec2 uv) {
    return abs(luma(texture(uTex, uv).rgb) - luma(texture(uPrev, uv).rgb));
}

void main() {
    vec3 cur = texture(uTex, vUV).rgb;

    // Сетка макроблоков ~20 px, выведена из разрешения
    vec2 grid = uResolution / 20.0;
    vec2 bid  = floor(vUV * grid);
    vec2 bctr = (bid + 0.5) / grid;

    // Оцениваем энергию движения на блок: центр + 4 внутренних сэмпла
    vec2 o = 0.3 / grid;
    float diff = chg(bctr)
               + chg(bctr + vec2( o.x,  o.y))
               + chg(bctr + vec2(-o.x,  o.y))
               + chg(bctr + vec2( o.x, -o.y))
               + chg(bctr + vec2(-o.x, -o.y));
    diff *= 0.2;

    // Порог растёт с интенсивностью (голодает больше блоков) и дышит во
    // времени, со сдвигом фазы по блоку, чтобы застывание ползло по кадру.
    float breathe = 0.8 + 0.4 * sin(uTime * 0.9 + hash21(bid) * 6.2831);
    float th = mix(0.015, 0.11, uIntensity) * breathe;

    // freeze = 1, где блок статичен (ниже порога). Мягкий край, чтобы блоки
    // оттаивали/замерзали без резких скачков.
    float freeze = 1.0 - smoothstep(th, th * 1.8, diff);

    // Пространственное сглаживание: подмешиваем поштучное изменение, чтобы
    // границы между застывшими и живыми блоками не образовывали ровную сетку.
    float pixFreeze = 1.0 - smoothstep(th, th * 1.8, chg(vUV));
    freeze = mix(freeze, pixFreeze, 0.25);

    // Часть блоков случайно вообще отказывается обновляться (ощущение потерянного слайса)
    float stuck = step(0.92, hash21(bid + floor(uTime * 0.7) * 0.613));
    freeze = max(freeze, stuck * uIntensity);

    vec3 frozen = texture(uPrev, vUV).rgb;
    vec3 moshed = mix(cur, frozen, freeze);

    // uIntensity == 0 -> ровно нетронутый текущий кадр
    FragColor = vec4(mix(cur, moshed, clamp(uIntensity, 0.0, 1.0)), 1.0);
}
