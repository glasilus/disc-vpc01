#version 330 core
// Self-Displacement — uses the PREVIOUS frame's RGB as a displacement map
// for the CURRENT frame. The image literally eats itself. With audio-reactive
// intensity this creates flowing glitch trails indistinguishable from datamosh.
// High intensity + feedback enabled = complete reality collapse.
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;    // current frame
uniform sampler2D uPrev;   // 1 frame ago (displacement source)
uniform sampler2D uPrev2;  // 2 frames ago (adds temporal depth)
uniform float uIntensity;
uniform float uTime;

void main() {
    // Use prev frame's RG channels as XY displacement vector
    vec4 disp_src  = texture(uPrev,  vUV);
    vec4 disp_src2 = texture(uPrev2, vUV);

    // Center the displacement: [0,1] → [-0.5, 0.5]
    vec2 d1 = (disp_src.rg  - 0.5) * uIntensity * 0.15;
    vec2 d2 = (disp_src2.rb - 0.5) * uIntensity * 0.07;

    // Multi-scale displacement: large-scale from prev2, fine detail from prev
    vec2 disp = d1 + d2;

    // Sample current frame displaced by prev's content
    vec4 displaced = texture(uTex, fract(vUV + disp));

    // Mix in a slightly displaced version of prev for ghosting
    vec4 ghost = texture(uPrev, fract(vUV + d1 * 0.3));
    float ghost_blend = uIntensity * 0.35;

    FragColor = mix(displaced, ghost, ghost_blend);
}
