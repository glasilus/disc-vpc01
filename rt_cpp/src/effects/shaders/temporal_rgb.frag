#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;    // current frame (B channel)
uniform sampler2D uPrev1;  // 1 frame ago  (G channel)
uniform sampler2D uPrev2;  // 2 frames ago (R channel)
uniform float uIntensity;
void main() {
    vec4 cur   = texture(uTex,   vUV);
    vec4 prev1 = texture(uPrev1, vUV);
    vec4 prev2 = texture(uPrev2, vUV);
    vec3 temporal = vec3(prev2.r, prev1.g, cur.b);
    FragColor = vec4(mix(cur.rgb, temporal, uIntensity), 1.0);
}
