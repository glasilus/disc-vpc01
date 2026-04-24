#version 330 core
// Fractal Noise Warp — domain-warped fBm distortion.
// Uses multi-octave noise to create organic, fractal UV displacement.
// Looks like the video is breathing / melting through another dimension.
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;
uniform float uIntensity;
uniform float uTime;

// Value noise hash
vec2 hash2(vec2 p) {
    p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
    return fract(sin(p) * 43758.5453123) * 2.0 - 1.0;
}

// Smooth 2D value noise
float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = dot(hash2(i),              f);
    float b = dot(hash2(i + vec2(1,0)), f - vec2(1,0));
    float c = dot(hash2(i + vec2(0,1)), f - vec2(0,1));
    float d = dot(hash2(i + vec2(1,1)), f - vec2(1,1));
    return mix(mix(a,b,u.x), mix(c,d,u.x), u.y);
}

// fBm — fractal Brownian motion
float fbm(vec2 p) {
    float v = 0.0, a = 0.5;
    mat2  rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.5));
    for (int i = 0; i < 5; i++) {
        v += a * noise(p);
        p  = rot * p * 2.1 + vec2(1.7, 9.2);
        a *= 0.5;
    }
    return v;
}

void main() {
    // Domain warping: warp the input to fbm using another fbm
    vec2 t = uTime * vec2(0.13, 0.07);
    vec2 q = vec2(fbm(vUV * 3.0 + t),
                  fbm(vUV * 3.0 + vec2(1.7, 9.2) + t));
    vec2 r = vec2(fbm(vUV * 3.0 + 4.0*q + vec2(1.7, 9.2) + 0.15*uTime),
                  fbm(vUV * 3.0 + 4.0*q + vec2(8.3, 2.8) + 0.126*uTime));

    vec2 disp = r * uIntensity * 0.12;
    FragColor  = texture(uTex, vUV + disp);
}
