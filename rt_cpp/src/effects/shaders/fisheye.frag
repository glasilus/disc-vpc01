#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
void main() {
    vec2 p  = vUV * 2.0 - 1.0;
    float r = length(p);
    float k = uIntensity * 0.4;
    vec2 distorted = p * (1.0 + k * r * r);
    vec2 uv = (distorted + 1.0) * 0.5;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0)
        FragColor = vec4(0.0, 0.0, 0.0, 1.0);
    else
        FragColor = texture(uTex, uv);
}
