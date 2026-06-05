#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform sampler2D uAccum;
uniform float uIntensity;
uniform float uFeedbackScale;    // e.g. 0.99 (pulsating scale)
uniform float uFeedbackRotation; // e.g. 0.02 (pulsating rotation)

void main() {
    vec4 cur = texture(uTex, vUV);
    
    // Transform coordinates around the center (0.5, 0.5)
    vec2 p = vUV - 0.5;
    float s = sin(uFeedbackRotation);
    float c = cos(uFeedbackRotation);
    vec2 rotated = vec2(p.x * c - p.y * s, p.x * s + p.y * c) * uFeedbackScale + 0.5;
    
    vec4 accum = texture(uAccum, rotated);
    float blend = mix(0.3, 0.85, uIntensity);
    FragColor = mix(cur, accum, blend);
}
