#version 330 core
// Vortex Warp — spiral rotation from center.
// Pixels near center get more rotation than edges → creates a vortex/drain effect.
// At high intensity looks completely unhinged.
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uTime;

void main() {
    vec2  p    = vUV - 0.5;
    float r    = length(p);
    float falloff = exp(-r * r * (4.0 - uIntensity * 3.5)); // gaussian falloff from center
    float angle   = uIntensity * 8.0 * falloff;

    // Add time-based oscillation for pulsating vortex
    angle += sin(uTime * 3.0) * uIntensity * 1.5 * falloff;

    float s = sin(angle), c = cos(angle);
    vec2 rotated = vec2(p.x*c - p.y*s, p.x*s + p.y*c) + 0.5;

    // Sample with chromatic aberration on the warp itself
    float ca = uIntensity * 0.01;
    float cr = texture(uTex, rotated + vec2(ca,  0.0)).r;
    float cg = texture(uTex, rotated).g;
    float cb = texture(uTex, rotated + vec2(-ca, 0.0)).b;

    FragColor = vec4(cr, cg, cb, 1.0);
}
