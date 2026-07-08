#version 330 core

// Размещает исходную текстуру на канве одним из четырёх режимов
// соотношения сторон. Пиксели вне области источника закрашиваются чёрным
// (letterbox / pillarbox). Вся координатная математика - в нормализованных
// UV канвы.
//
//   uMode = 0  Contain - целиком внутри канвы, letterbox/pillarbox
//   uMode = 1  Cover   - заполняет канву, обрезает лишнее
//   uMode = 2  Stretch - игнорирует соотношение сторон, растягивает
//   uMode = 3  Native  - маппинг 1:1, по центру канвы

in  vec2 vUV;
out vec4 fragColor;

uniform sampler2D uTex;
uniform vec2 uSrcSize;     // native video wh
uniform vec2 uCanvasSize;  // canvas wh
uniform int  uMode;

void main() {
    if (uMode == 2) {
        // Stretch: заполняем канву. V всё равно переворачиваем - sws_scale
        // пишет от верхнего левого угла, а GL сэмплирует от нижнего левого,
        // тот же переворот применяют все остальные ветки этого шейдера.
        // Без него Stretch показывал бы картинку вверх ногами.
        fragColor = texture(uTex, vec2(vUV.x, 1.0 - vUV.y));
        return;
    }

    float srcA = uSrcSize.x / uSrcSize.y;
    float canA = uCanvasSize.x / uCanvasSize.y;

    // frac = доля канвы, которую занимает источник.
    // 1.0 по оси значит, что источник ровно заполняет эту ось.
    vec2 frac;
    if (uMode == 3) {
        // native 1:1
        frac = uSrcSize / uCanvasSize;
    } else if (uMode == 0) {
        // contain
        if (srcA > canA) frac = vec2(1.0, canA / srcA);
        else             frac = vec2(srcA / canA, 1.0);
    } else {
        // cover
        if (srcA > canA) frac = vec2(srcA / canA, 1.0);
        else             frac = vec2(1.0, canA / srcA);
    }

    // Переводим UV канвы в UV источника, по центру.
    vec2 uv = (vUV - 0.5) / frac + 0.5;

    if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0)))) {
        fragColor = vec4(0.0, 0.0, 0.0, 1.0);
    } else {
        // sws_scale пишет исходный кадр от верхнего левого угла, GL сэмплирует
        // от нижнего левого. Переворачиваем V здесь и только здесь, чтобы
        // последующие проходы эффектов (читающие FBO-текстуры в конвенции GL)
        // не переворачивали результат повторно.
        fragColor = texture(uTex, vec2(uv.x, 1.0 - uv.y));
    }
}
