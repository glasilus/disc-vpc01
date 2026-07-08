#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uBase;    // кадр видео
uniform sampler2D uOverlay; // изображение оверлея (RGBA)
uniform vec2  uOverlayPos;  // нормализованная позиция верхнего левого угла
uniform vec2  uOverlaySize; // нормализованный размер
uniform float uTolerance;   // допуск по оттенку 0..1
uniform float uSoftness;    // мягкость края 0..1
uniform vec3  uKeyColor;    // ключевой цвет в RGB [0..1]
uniform int   uMode;        // 0=нет,1=доминантный,2=вторичный,3=вручную
uniform float uOverlayAlpha;

vec3 rgb2hsv(vec3 c) {
    vec4 K = vec4(0.0,-1.0/3.0,2.0/3.0,-1.0);
    vec4 p = mix(vec4(c.bg,K.wz),vec4(c.gb,K.xy),step(c.b,c.g));
    vec4 q = mix(vec4(p.xyw,c.r),vec4(c.r,p.yzx),step(p.x,c.r));
    float d = q.x - min(q.w,q.y);
    float e = 1.0e-10;
    return vec3(abs(q.z+(q.w-q.y)/(6.0*d+e)),d/(q.x+e),q.x);
}

void main() {
    vec4 base = texture(uBase, vUV);
    if (uOverlayAlpha < 0.01) { FragColor = base; return; }

    // Проверяем, попадает ли этот пиксель в область оверлея
    vec2 rel = (vUV - uOverlayPos) / uOverlaySize;
    if (rel.x < 0.0 || rel.x > 1.0 || rel.y < 0.0 || rel.y > 1.0) {
        FragColor = base; return;
    }

    vec4 ov = texture(uOverlay, rel);

    // Маска стартует с собственной альфы оверлея. Chroma key дополнительно
    // ослабляет её при uMode != 0; при uMode == 0 оверлей просто накладывается.
    float mask = ov.a;
    if (uMode != 0) {
        vec3  key_hsv = rgb2hsv(uKeyColor);
        vec3  ov_hsv  = rgb2hsv(ov.rgb);
        float hue_diff = abs(ov_hsv.x - key_hsv.x);
        hue_diff = min(hue_diff, 1.0 - hue_diff);
        // Пиксели, близкие по оттенку к ключевому, маскируются (alpha → 0);
        // далёкие остаются (alpha → 1).
        float alpha = smoothstep(uTolerance - uSoftness, uTolerance, hue_diff);
        mask *= alpha;
    }
    mask *= uOverlayAlpha;
    FragColor = vec4(mix(base.rgb, ov.rgb, mask), 1.0);
}
