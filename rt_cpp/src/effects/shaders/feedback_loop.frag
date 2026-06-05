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
    
    // Instead of a simple mix which looks blurry and washed out, 
    // we use an additive/max approach with a decay factor to create 
    // glowing trails, which is the standard for VJ software feedback.
    float decay = mix(0.7, 0.98, uIntensity);
    
    // Slightly shift hue/color on the feedback loop for a psychedelic trail
    accum.rgb *= vec3(0.99, 0.95, 0.92); 
    accum *= decay;
    
    // Combine current frame with the decaying feedback trail
    FragColor = max(cur, accum);
}
