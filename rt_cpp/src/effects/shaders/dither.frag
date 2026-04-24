#version 330 core
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform vec2  uResolution;

// Bayer 4x4 matrix
float bayer4[16] = float[](
     0.0/16.0,  8.0/16.0,  2.0/16.0, 10.0/16.0,
    12.0/16.0,  4.0/16.0, 14.0/16.0,  6.0/16.0,
     3.0/16.0, 11.0/16.0,  1.0/16.0,  9.0/16.0,
    15.0/16.0,  7.0/16.0, 13.0/16.0,  5.0/16.0
);

void main() {
    vec4 col = texture(uTex, vUV);
    ivec2 p  = ivec2(mod(floor(vUV * uResolution), 4.0));
    float threshold = bayer4[p.y * 4 + p.x];
    vec3 dithered = floor(col.rgb + threshold * uIntensity) / max(1.0, uIntensity * 4.0);
    FragColor = vec4(mix(col.rgb, clamp(dithered, 0.0, 1.0), uIntensity), 1.0);
}
