#version 330 core
in  vec2 vUV;
out vec4 FragColor;
// Wrong Motion Vector - datamosh со "смазанным цветением". Имитирует декодер,
// применяющий побитые векторы движения: каждый макроблок тянет кусок
// ПРЕДЫДУЩЕГО вывода в случайном, медленно дрейфующем направлении. Так как
// uPrev - это предыдущий вывод самого движка, смещённый смаз смещается
// заново на каждом кадре и расцветает в длинные плывущие полосы. Небольшое
// хроматическое расщепление вдоль вектора усиливает ощущение деградации кодека.
uniform sampler2D uTex;
uniform sampler2D uPrev;
uniform float uIntensity;
uniform float uTime;
uniform vec2  uResolution;

// Знаковый 2D-хэш в [-1,1].
vec2 hash22(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return fract(sin(p) * 43758.5453) * 2.0 - 1.0;
}

void main() {
    vec3 cur = texture(uTex, vUV).rgb;

    // Макроблоки ~24 px.
    vec2 grid = uResolution / 24.0;
    vec2 bid  = floor(vUV * grid);

    // Фиктивный вектор движения для этого блока: направление из хэша,
    // медленно дрейфующее во времени, величина до ~5% кадра на полной интенсивности.
    vec2 dir = hash22(bid + floor(uTime * 1.3) * 0.31);
    dir += 0.5 * hash22(bid * 1.7 - vec2(uTime * 0.2));   // покачивание
    float mag = 0.006 + 0.045 * uIntensity;
    vec2 mv = dir * mag;

    // Сэмплируем предыдущий вывод, смещённый фиктивным вектором, с небольшим
    // хроматическим расщеплением вдоль вектора.
    vec2 ca = mv * 0.25;
    vec3 smear;
    smear.r = texture(uPrev, vUV + mv + ca).r;
    smear.g = texture(uPrev, vUV + mv     ).g;
    smear.b = texture(uPrev, vUV + mv - ca).b;

    // Цветение: смещённый предыдущий кадр доминирует всё сильнее с ростом
    // интенсивности, полоса нарастает и удлиняется от кадра к кадру. Держим
    // нижний порог живого кадра, чтобы картинка не отрывалась от реальности совсем.
    float feed   = 0.35 + 0.55 * uIntensity;
    vec3  moshed = mix(cur, smear, feed);
    moshed = max(moshed, cur * 0.15);

    // uIntensity == 0 -> ровно нетронутый текущий кадр.
    FragColor = vec4(mix(cur, moshed, clamp(uIntensity, 0.0, 1.0)), 1.0);
}
