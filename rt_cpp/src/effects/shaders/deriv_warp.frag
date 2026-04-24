#version 330 core
// Derivative Warp — datamosh-like smearing without optical flow.
// Computes local luminance gradient from history frame, uses it as
// a displacement vector to pull current frame pixels. Creates organic
// smear/bleed that resembles datamosh compression artifacts.
in  vec2 vUV;
out vec4 FragColor;
uniform sampler2D uTex;    // current frame
uniform sampler2D uPrev;   // 1 frame ago
uniform float uIntensity;  // 0..1
uniform vec2  uResolution;

float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

void main() {
    vec2 px = 1.0 / uResolution;

    // Compute gradient of previous frame (Sobel-like)
    float tl = luma(texture(uPrev, vUV + vec2(-px.x,  px.y)).rgb);
    float tr = luma(texture(uPrev, vUV + vec2( px.x,  px.y)).rgb);
    float bl = luma(texture(uPrev, vUV + vec2(-px.x, -px.y)).rgb);
    float br = luma(texture(uPrev, vUV + vec2( px.x, -px.y)).rgb);
    float ml = luma(texture(uPrev, vUV + vec2(-px.x,  0.0 )).rgb);
    float mr = luma(texture(uPrev, vUV + vec2( px.x,  0.0 )).rgb);
    float tm = luma(texture(uPrev, vUV + vec2( 0.0,   px.y)).rgb);
    float bm = luma(texture(uPrev, vUV + vec2( 0.0,  -px.y)).rgb);

    float gx = (tr + 2.0*mr + br) - (tl + 2.0*ml + bl);
    float gy = (tl + 2.0*tm + tr) - (bl + 2.0*bm + br);
    vec2  grad = vec2(gx, gy);

    // Accumulate displacement over several "flow steps"
    float scale = uIntensity * 0.08;
    vec2  disp  = grad * scale;

    // Sample current frame with displacement — like motion vectors
    vec4 warped = texture(uTex, vUV + disp);

    // Blend in history for smear persistence
    vec4 prev   = texture(uPrev, vUV + disp * 0.5);
    float blend = uIntensity * 0.45;
    FragColor   = mix(warped, mix(warped, prev, blend), uIntensity);
}
