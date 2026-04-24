#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
void main() {
    vec4 col  = texture(uTex, vUV);
    float gain = 1.0 + uIntensity * 4.0;
    vec3  shaped = tanh(col.rgb * gain) / tanh(gain);
    FragColor = vec4(mix(col.rgb, shaped, uIntensity), 1.0);
}
