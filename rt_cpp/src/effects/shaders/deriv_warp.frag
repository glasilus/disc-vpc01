#version 330 core
// Derivative Warp - смаз в духе datamosh без оптического потока.
// Считает локальный градиент яркости по кадру истории и использует его
// как вектор смещения для пикселей текущего кадра. Даёт органичный
// смаз/растекание, похожее на артефакты сжатия datamosh.
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;    // текущий кадр
uniform sampler2D uPrev;   // кадр 1 назад
uniform float uIntensity;  // 0..1
uniform vec2  uResolution;

float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

void main() {
    vec2 px = 1.0 / uResolution;

    // Градиент предыдущего кадра (в духе оператора Собеля)
    float tl = luma(texture(uPrev, vUV + vec2(-px.x,  px.y)).rgb);
    float tr = luma(texture(uPrev, vUV + vec2( px.x,  px.y)).rgb);
    float bl = luma(texture(uPrev, vUV + vec2(-px.x, -px.y)).rgb);
    float br = luma(texture(uPrev, vUV + vec2( px.x, -px.y)).rgb);
    float ml = luma(texture(uPrev, vUV + vec2(-px.x,  0.0 )).rgb);
    float mr = luma(texture(uPrev, vUV + vec2( px.x,  0.0 )).rgb);
    float tm = luma(texture(uPrev, vUV + vec2( 0.0,   px.y)).rgb);
    float bm = luma(texture(uPrev, vUV + vec2( 0.0,  -px.y)).rgb);

    float gx = (tr + 2.0*mr + br) - (tl + 2.0*ml + bl);
    float gy = (tl + 2.0*tm + tr) - (bl + 2.0*bm + br);
    vec2  grad = vec2(gx, gy);

    // Накапливаем смещение как за несколько "шагов потока"
    float scale = uIntensity * 0.13;
    vec2  disp  = grad * scale;

    // Сэмплируем текущий кадр со смещением - аналог векторов движения
    vec4 warped = texture(uTex, vUV + disp);

    // Подмешиваем историю, чтобы смаз держался дольше одного кадра
    vec4 prev   = texture(uPrev, vUV + disp * 0.5);
    float blend = uIntensity * 0.45;
    FragColor   = mix(warped, mix(warped, prev, blend), uIntensity);
}
