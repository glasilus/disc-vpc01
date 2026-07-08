#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform sampler2D uAccum;
uniform float uIntensity;
uniform float uFeedbackScale;    // напр. 0.99 (пульсирующий масштаб)
uniform float uFeedbackRotation; // напр. 0.02 (пульсирующее вращение)

void main() {
    vec4 cur = texture(uTex, vUV);

    // Трансформация координат вокруг центра (0.5, 0.5)
    vec2 p = vUV - 0.5;
    float s = sin(uFeedbackRotation);
    float c = cos(uFeedbackRotation);
    vec2 rotated = vec2(p.x * c - p.y * s, p.x * s + p.y * c) * uFeedbackScale + 0.5;

    vec4 accum = texture(uAccum, rotated);

    // Простой mix даёт размытую вымытую картинку, поэтому вместо него -
    // аддитивный/max-подход с коэффициентом затухания: так строятся
    // светящиеся хвосты в стандартном VJ-фидбеке.
    float decay = mix(0.7, 0.98, uIntensity);

    // Небольшой сдвиг оттенка на каждом витке фидбека даёт психоделический хвост
    accum.rgb *= vec3(0.99, 0.95, 0.92);
    accum *= decay;

    // Смешиваем текущий кадр с затухающим фидбек-хвостом
    FragColor = max(cur, accum);
}
