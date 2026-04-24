#include "effect_chain.h"
#include <cstdio>
#include <cstring>
#include <cmath>
#include <cstdlib>
#include <functional>

// Embedded shader sources (generated headers on include path via CMake)
#include "passthrough_frag.h"
#include "deriv_warp_frag.h"
#include "flash_frag.h"
#include "stutter_frag.h"
#include "pixel_sort_frag.h"
#include "ghost_trails_frag.h"
#include "scanlines_frag.h"
#include "bitcrush_frag.h"
#include "block_glitch_frag.h"
#include "negative_frag.h"
#include "color_bleed_frag.h"
#include "interlace_frag.h"
#include "bad_signal_frag.h"
#include "zoom_glitch_frag.h"
#include "mosaic_frag.h"
#include "phase_shift_frag.h"
#include "dither_frag.h"
#include "feedback_loop_frag.h"
#include "temporal_rgb_frag.h"
#include "waveshaper_frag.h"
#include "chroma_key_frag.h"
#include "vortex_frag.h"
#include "fractal_noise_frag.h"
#include "self_disp_frag.h"
#include "ascii_frag.h"

// ── fx_key mapping ────────────────────────────────────────────────────────────

const char* fx_key(FxId id) {
    // NOTE: fx_derivwarp replaces the old fx_rgb in the enum.
    // For preset compat we store it as "fx_derivwarp"; old presets just lack it (→ disabled).
    static const char* keys[(int)FxId::COUNT] = {
        "fx_derivwarp",   // 0
        "fx_flash",       // 1
        "fx_stutter",     // 2
        "fx_pixel_sort",  // 3
        "fx_ghost",       // 4
        "fx_scanlines",   // 5
        "fx_bitcrush",    // 6
        "fx_blockglitch", // 7
        "fx_negative",    // 8
        "fx_colorbleed",  // 9
        "fx_interlace",   // 10
        "fx_badsignal",   // 11
        "fx_zoomglitch",  // 12
        "fx_mosaic",      // 13
        "fx_phaseshift",  // 14
        "fx_dither",      // 15
        "fx_feedback",    // 16
        "fx_temporalrgb", // 17
        "fx_waveshaper",  // 18
        "fx_overlays",    // 19
        "fx_vortex",      // 20
        "fx_fractalnoise",// 21
        "fx_selfdisp",    // 22
        "fx_ascii",       // 23
    };
    return keys[(int)id];
}

// ── FboPair ───────────────────────────────────────────────────────────────────

void FboPair::create(int w, int h) {
    width = w; height = h;
    glGenFramebuffers(2, fbo);
    glGenTextures(2, tex);
    for (int i = 0; i < 2; ++i) {
        glBindTexture(GL_TEXTURE_2D, tex[i]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glBindFramebuffer(GL_FRAMEBUFFER, fbo[i]);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex[i], 0);
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glBindTexture(GL_TEXTURE_2D, 0);
}

void FboPair::destroy() {
    if (fbo[0]) { glDeleteFramebuffers(2, fbo); fbo[0] = fbo[1] = 0; }
    if (tex[0]) { glDeleteTextures(2, tex);     tex[0] = tex[1] = 0; }
    width = height = 0;
}

// ── EffectChain ───────────────────────────────────────────────────────────────

static GLuint compile_shader_src(GLenum type, const char* src) {
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &src, nullptr);
    glCompileShader(s);
    GLint ok = 0;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[1024]; glGetShaderInfoLog(s, sizeof(log), nullptr, log);
        fprintf(stderr, "[shader] compile error:\n%s\n", log);
    }
    return s;
}

static const char* k_vert =
    "#version 330 core\n"
    "layout(location=0) in vec2 aPos;\n"
    "layout(location=1) in vec2 aUV;\n"
    "out vec2 vUV;\n"
    "void main(){ vUV=aUV; gl_Position=vec4(aPos,0.0,1.0); }\n";

GLuint EffectChain::compile_program(const char* vert, const char* frag) {
    GLuint v = compile_shader_src(GL_VERTEX_SHADER,   vert);
    GLuint f = compile_shader_src(GL_FRAGMENT_SHADER, frag);
    GLuint p = glCreateProgram();
    glAttachShader(p, v); glAttachShader(p, f);
    glLinkProgram(p);
    GLint ok = 0; glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[512]; glGetProgramInfoLog(p, sizeof(log), nullptr, log);
        fprintf(stderr, "[shader] link error: %s\n", log);
    }
    glDeleteShader(v); glDeleteShader(f);
    return p;
}

EffectChain::EffectChain()  = default;
EffectChain::~EffectChain() { destroy(); }

void EffectChain::setup_quad() {
    static const float verts[] = {
        -1.f,-1.f, 0.f,0.f,
         1.f,-1.f, 1.f,0.f,
        -1.f, 1.f, 0.f,1.f,
         1.f,-1.f, 1.f,0.f,
         1.f, 1.f, 1.f,1.f,
        -1.f, 1.f, 0.f,1.f,
    };
    glGenVertexArrays(1, &quad_vao_);
    glGenBuffers(1, &quad_vbo_);
    glBindVertexArray(quad_vao_);
    glBindBuffer(GL_ARRAY_BUFFER, quad_vbo_);
    glBufferData(GL_ARRAY_BUFFER, sizeof(verts), verts, GL_STATIC_DRAW);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4*sizeof(float), (void*)0);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4*sizeof(float), (void*)(2*sizeof(float)));
    glEnableVertexAttribArray(1);
    glBindVertexArray(0);
}

// Dense-to-sparse ASCII chars (16 levels).
// Each entry is 8 columns of an 8×8 bitmap font row.
// We use a hand-crafted minimal font for: @#%=+-. (space) + 8 more density chars.
// Encoded as 8 rows × 8 bytes per character, 16 characters total.
// Font data: chars ordered from DENSE (@) to SPARSE (space)
static const uint8_t kFontData[16][8][8] = {
    // 0: @ (very dense)
    {{0,0,0,0,0,0,0,0},{0,0,1,1,1,1,0,0},{0,1,1,0,0,1,1,0},{0,1,0,1,1,1,1,0},
     {0,1,0,1,0,1,1,0},{0,1,0,1,1,1,0,0},{0,1,1,0,0,0,0,0},{0,0,1,1,1,1,0,0}},
    // 1: #
    {{0,0,0,0,0,0,0,0},{0,1,0,1,0,1,0,0},{0,1,0,1,0,1,0,0},{1,1,1,1,1,1,1,0},
     {0,1,0,1,0,1,0,0},{1,1,1,1,1,1,1,0},{0,1,0,1,0,1,0,0},{0,0,0,0,0,0,0,0}},
    // 2: &
    {{0,0,1,1,0,0,0,0},{0,1,0,0,1,0,0,0},{0,1,0,0,1,0,0,0},{0,0,1,1,0,0,0,0},
     {0,1,0,1,0,1,0,0},{0,1,0,0,1,0,0,0},{0,1,0,0,1,1,0,0},{0,0,1,1,0,1,1,0}},
    // 3: %
    {{1,1,0,0,0,0,1,0},{1,1,0,0,0,1,0,0},{0,0,0,0,1,0,0,0},{0,0,0,1,0,0,0,0},
     {0,0,1,0,0,0,0,0},{0,1,0,0,0,1,1,0},{1,0,0,0,0,1,1,0},{0,0,0,0,0,0,0,0}},
    // 4: $
    {{0,0,1,0,0,0,0,0},{0,1,1,1,1,0,0,0},{1,0,1,0,0,0,0,0},{0,1,1,1,0,0,0,0},
     {0,0,1,0,1,0,0,0},{0,1,1,1,1,0,0,0},{0,0,1,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 5: *
    {{0,0,0,0,0,0,0,0},{0,0,1,0,1,0,0,0},{0,0,0,1,0,0,0,0},{0,1,1,1,1,1,0,0},
     {0,0,0,1,0,0,0,0},{0,0,1,0,1,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 6: o
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,1,1,1,0,0,0},{0,1,0,0,0,1,0,0},
     {0,1,0,0,0,1,0,0},{0,1,0,0,0,1,0,0},{0,0,1,1,1,0,0,0},{0,0,0,0,0,0,0,0}},
    // 7: =
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,1,1,1,1,1,0,0},{0,0,0,0,0,0,0,0},
     {0,1,1,1,1,1,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 8: +
    {{0,0,0,0,0,0,0,0},{0,0,0,1,0,0,0,0},{0,0,0,1,0,0,0,0},{0,1,1,1,1,1,0,0},
     {0,0,0,1,0,0,0,0},{0,0,0,1,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 9: -
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,1,1,1,1,1,0,0},
     {0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 10: ~
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,1,0,0,1,0,0,0},{1,0,1,0,0,1,0,0},
     {0,0,0,1,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 11: :
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,1,1,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 12: .
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,1,1,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 13: '
    {{0,0,0,1,1,0,0,0},{0,0,0,1,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 14: `
    {{0,0,1,0,0,0,0,0},{0,0,0,1,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
    // 15: (space) — completely empty
    {{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},
     {0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0}},
};

void EffectChain::create_ascii_font_tex() {
    // Build 128×8 R8 texture: 16 chars × 8px wide, 8 rows tall
    const int CHARS = 16, CHAR_W = 8, CHAR_H = 8;
    const int W = CHARS * CHAR_W, H = CHAR_H;
    uint8_t pixels[H][W] = {};

    for (int c = 0; c < CHARS; c++) {
        for (int row = 0; row < CHAR_H; row++) {
            for (int col = 0; col < CHAR_W; col++) {
                pixels[row][c * CHAR_W + col] =
                    kFontData[c][row][col] ? 255 : 0;
            }
        }
    }

    glGenTextures(1, &ascii_font_tex_);
    glBindTexture(GL_TEXTURE_2D, ascii_font_tex_);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, W, H, 0, GL_RED, GL_UNSIGNED_BYTE, pixels);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glBindTexture(GL_TEXTURE_2D, 0);
}

bool EffectChain::init(int w, int h) {
    setup_quad();
    main_fbo_.create(w, h);
    accum_fbo_.create(w, h);

    // Pre-allocate history textures + FBOs
    glGenTextures(kHistoryLen, hist_tex_);
    glGenFramebuffers(kHistoryLen, hist_fbo_);
    for (int i = 0; i < kHistoryLen; ++i) {
        glBindTexture(GL_TEXTURE_2D, hist_tex_[i]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        glBindFramebuffer(GL_FRAMEBUFFER, hist_fbo_[i]);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, hist_tex_[i], 0);
    }
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glBindTexture(GL_TEXTURE_2D, 0);

    create_ascii_font_tex();

    // Compile all programs
    prog_pass_        = compile_program(k_vert, k_passthrough_frag);
    prog_derivwarp_   = compile_program(k_vert, k_deriv_warp_frag);
    prog_flash_       = compile_program(k_vert, k_flash_frag);
    prog_stutter_     = compile_program(k_vert, k_stutter_frag);
    prog_pixsort_     = compile_program(k_vert, k_pixel_sort_frag);
    prog_ghost_       = compile_program(k_vert, k_ghost_trails_frag);
    prog_scanlines_   = compile_program(k_vert, k_scanlines_frag);
    prog_bitcrush_    = compile_program(k_vert, k_bitcrush_frag);
    prog_blockglitch_ = compile_program(k_vert, k_block_glitch_frag);
    prog_negative_    = compile_program(k_vert, k_negative_frag);
    prog_colorbleed_  = compile_program(k_vert, k_color_bleed_frag);
    prog_interlace_   = compile_program(k_vert, k_interlace_frag);
    prog_badsignal_   = compile_program(k_vert, k_bad_signal_frag);
    prog_zoomglitch_  = compile_program(k_vert, k_zoom_glitch_frag);
    prog_mosaic_      = compile_program(k_vert, k_mosaic_frag);
    prog_phaseshift_  = compile_program(k_vert, k_phase_shift_frag);
    prog_dither_      = compile_program(k_vert, k_dither_frag);
    prog_feedback_    = compile_program(k_vert, k_feedback_loop_frag);
    prog_temporalrgb_ = compile_program(k_vert, k_temporal_rgb_frag);
    prog_waveshaper_  = compile_program(k_vert, k_waveshaper_frag);
    prog_overlay_     = compile_program(k_vert, k_chroma_key_frag);
    prog_vortex_      = compile_program(k_vert, k_vortex_frag);
    prog_fractalnoise_= compile_program(k_vert, k_fractal_noise_frag);
    prog_selfdisp_    = compile_program(k_vert, k_self_disp_frag);
    prog_ascii_       = compile_program(k_vert, k_ascii_frag);

    return true;
}

void EffectChain::resize(int w, int h) {
    main_fbo_.destroy();  main_fbo_.create(w, h);
    accum_fbo_.destroy(); accum_fbo_.create(w, h);
    // Resize history textures
    for (int i = 0; i < kHistoryLen; ++i) {
        glBindTexture(GL_TEXTURE_2D, hist_tex_[i]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, nullptr);
    }
    glBindTexture(GL_TEXTURE_2D, 0);
    hist_idx_  = 0;
    hist_full_ = false;
}

void EffectChain::destroy() {
    main_fbo_.destroy();
    accum_fbo_.destroy();
    if (hist_tex_[0]) { glDeleteTextures(kHistoryLen, hist_tex_);    std::memset(hist_tex_, 0, sizeof(hist_tex_)); }
    if (hist_fbo_[0]) { glDeleteFramebuffers(kHistoryLen, hist_fbo_);std::memset(hist_fbo_, 0, sizeof(hist_fbo_)); }
    if (ascii_font_tex_) { glDeleteTextures(1, &ascii_font_tex_); ascii_font_tex_ = 0; }

    auto del = [](GLuint& p){ if(p){ glDeleteProgram(p); p=0; } };
    del(prog_pass_); del(prog_derivwarp_); del(prog_flash_);
    del(prog_stutter_); del(prog_pixsort_); del(prog_ghost_);
    del(prog_scanlines_); del(prog_bitcrush_); del(prog_blockglitch_);
    del(prog_negative_); del(prog_colorbleed_); del(prog_interlace_);
    del(prog_badsignal_); del(prog_zoomglitch_); del(prog_mosaic_);
    del(prog_phaseshift_); del(prog_dither_); del(prog_feedback_);
    del(prog_temporalrgb_); del(prog_waveshaper_); del(prog_overlay_);
    del(prog_vortex_); del(prog_fractalnoise_); del(prog_selfdisp_);
    del(prog_ascii_);

    if (quad_vao_) { glDeleteVertexArrays(1, &quad_vao_); quad_vao_ = 0; }
    if (quad_vbo_) { glDeleteBuffers(1, &quad_vbo_);      quad_vbo_ = 0; }
}

// ── History management ────────────────────────────────────────────────────────

void EffectChain::push_history() {
    // GPU-side copy: blit main_fbo_.read_tex() into hist_fbo_[hist_idx_]
    // using glBlitFramebuffer (fast, no pixel read-back to CPU)
    int w = main_fbo_.width, h = main_fbo_.height;

    // We need a source FBO. The main ping-pong FBOs are owned by main_fbo_.
    // The current read side is main_fbo_.fbo[main_fbo_.current].
    GLuint src_fbo = main_fbo_.fbo[main_fbo_.current];
    GLuint dst_fbo = hist_fbo_[hist_idx_];

    glBindFramebuffer(GL_READ_FRAMEBUFFER, src_fbo);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, dst_fbo);
    glBlitFramebuffer(0,0,w,h, 0,0,w,h, GL_COLOR_BUFFER_BIT, GL_NEAREST);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);

    hist_idx_ = (hist_idx_ + 1) % kHistoryLen;
    if (!hist_full_ && hist_idx_ == 0) hist_full_ = true;
}

GLuint EffectChain::history_tex(int age) const {
    // age 0 = most recent, age 1 = one frame older, etc.
    if (!hist_full_ && age >= hist_idx_) return main_fbo_.read_tex(); // fallback
    int slot = (hist_idx_ - 1 - age + kHistoryLen * 2) % kHistoryLen;
    return hist_tex_[slot];
}

// ── Shader pass helper ────────────────────────────────────────────────────────

void EffectChain::pass(GLuint prog, GLuint src_tex,
                       const std::function<void(GLuint)>& set_uniforms) {
    glBindFramebuffer(GL_FRAMEBUFFER, main_fbo_.write_fbo());
    glViewport(0, 0, main_fbo_.width, main_fbo_.height);
    glUseProgram(prog);

    // Bind src as texture unit 0 (uTex)
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, src_tex);
    glUniform1i(glGetUniformLocation(prog, "uTex"), 0);

    set_uniforms(prog);

    glBindVertexArray(quad_vao_);
    glDrawArrays(GL_TRIANGLES, 0, 6);
    glBindVertexArray(0);
    main_fbo_.swap();
}

// Convenience: set uniform helpers
static inline void u1f(GLuint p, const char* n, float v)           { glUniform1f(glGetUniformLocation(p,n),v); }
static inline void u1i(GLuint p, const char* n, int   v)           { glUniform1i(glGetUniformLocation(p,n),v); }
static inline void u2f(GLuint p, const char* n, float a, float b)  { glUniform2f(glGetUniformLocation(p,n),a,b); }
static inline void u3f(GLuint p, const char* n, float a, float b, float c) { glUniform3f(glGetUniformLocation(p,n),a,b,c); }

static inline void bind_tex(GLuint prog, int unit, GLuint tex, const char* name) {
    glActiveTexture(GL_TEXTURE0 + unit);
    glBindTexture(GL_TEXTURE_2D, tex);
    glUniform1i(glGetUniformLocation(prog, name), unit);
}

static bool fires(float chance) {
    return ((float)rand() / (float)RAND_MAX) < chance;
}

// ── Main apply ────────────────────────────────────────────────────────────────

GLuint EffectChain::apply(
    GLuint              input_tex,
    GLuint              overlay_tex,
    float               overlay_x, float overlay_y,
    float               overlay_w, float overlay_h,
    const ChromaKeyParams& chroma,
    float               overlay_alpha,
    const Segment&      seg,
    float               chaos,
    float               master_intensity,
    float               time_sec,
    EffectParams        params[(int)FxId::COUNT])
{
    const int W = main_fbo_.width, H = main_fbo_.height;
    const float fi_base = seg.intensity * chaos;  // base intensity [0..1]

    // Upload input into main_fbo write slot, then swap so read=input
    pass(prog_pass_, input_tex, [](GLuint){});

    // Helper: fire if enabled + probability check
    auto fire = [&](FxId id) -> bool {
        return params[(int)id].enabled && fires(params[(int)id].chance);
    };

    // Grab history references (safe — all pre-allocated)
    GLuint h0 = history_tex(0);  // 1 frame ago
    GLuint h1 = history_tex(1);  // 2 frames ago
    GLuint h2 = history_tex(2);  // 3 frames ago

    // ── Temporal / smear effects ──────────────────────────────────────────────

    if (fire(FxId::GHOST)) {
        pass(prog_ghost_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi_base);
        });
    }

    if (fire(FxId::STUTTER)) {
        pass(prog_stutter_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi_base);
        });
    }

    if (fire(FxId::INTERLACE)) {
        pass(prog_interlace_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi_base);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (fire(FxId::TEMPORALRGB)) {
        pass(prog_temporalrgb_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev1");
            bind_tex(p, 2, h1, "uPrev2");
            u1f(p,"uIntensity", fi_base);
        });
    }

    // ── New datamosh-like effects ─────────────────────────────────────────────

    if (fire(FxId::DERIVWARP)) {
        pass(prog_derivwarp_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            u1f(p,"uIntensity", fi_base);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (fire(FxId::SELFDISP)) {
        pass(prog_selfdisp_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, h0, "uPrev");
            bind_tex(p, 2, h1, "uPrev2");
            u1f(p,"uIntensity", fi_base);
            u1f(p,"uTime",      time_sec);
        });
    }

    if (fire(FxId::VORTEX)) {
        pass(prog_vortex_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u1f(p,"uTime",      time_sec);
        });
    }

    if (fire(FxId::FRACTALNOISE)) {
        pass(prog_fractalnoise_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u1f(p,"uTime",      time_sec);
        });
    }

    // ── Feedback accumulator ──────────────────────────────────────────────────

    if (fire(FxId::FEEDBACK)) {
        GLuint cur   = main_fbo_.read_tex();
        GLuint prev_accum = accum_fbo_.read_tex();

        // Write new accumulator = blend(cur, prev_accum)
        glBindFramebuffer(GL_FRAMEBUFFER, accum_fbo_.write_fbo());
        glViewport(0,0,W,H);
        glUseProgram(prog_feedback_);
        glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, cur);        glUniform1i(glGetUniformLocation(prog_feedback_,"uTex"),  0);
        glActiveTexture(GL_TEXTURE1); glBindTexture(GL_TEXTURE_2D, prev_accum); glUniform1i(glGetUniformLocation(prog_feedback_,"uAccum"),1);
        u1f(prog_feedback_,"uIntensity", fi_base);
        glBindVertexArray(quad_vao_); glDrawArrays(GL_TRIANGLES,0,6); glBindVertexArray(0);
        accum_fbo_.swap();

        pass(prog_pass_, accum_fbo_.read_tex(), [](GLuint){});
    }

    // ── Channel / color effects ───────────────────────────────────────────────

    if (fire(FxId::COLORBLEED)) {
        pass(prog_colorbleed_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (fire(FxId::BLOCKGLITCH)) {
        pass(prog_blockglitch_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u1f(p,"uTime",      time_sec);
        });
    }

    if (fire(FxId::BADSIGNAL)) {
        pass(prog_badsignal_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u1f(p,"uTime",      time_sec);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (fire(FxId::PHASESHIFT)) {
        pass(prog_phaseshift_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u1f(p,"uTime",      time_sec);
        });
    }

    if (fire(FxId::PIXEL_SORT)) {
        pass(prog_pixsort_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (fire(FxId::ZOOMGLITCH)) {
        pass(prog_zoomglitch_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
        });
    }

    if (fire(FxId::MOSAIC)) {
        pass(prog_mosaic_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
        });
    }

    if (fire(FxId::NEGATIVE)) {
        pass(prog_negative_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
        });
    }

    if (fire(FxId::SCANLINES)) {
        pass(prog_scanlines_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (fire(FxId::BITCRUSH)) {
        pass(prog_bitcrush_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
        });
    }

    if (fire(FxId::DITHER)) {
        pass(prog_dither_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u2f(p,"uResolution",(float)W,(float)H);
        });
    }

    if (fire(FxId::WAVESHAPER)) {
        pass(prog_waveshaper_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
        });
    }

    // ── Flash (last of color passes — white/black hit) ────────────────────────

    if (fire(FxId::FLASH)) {
        float white = (rand() % 2) ? 1.f : 0.f;
        pass(prog_flash_, main_fbo_.read_tex(), [&](GLuint p){
            u1f(p,"uIntensity", fi_base);
            u1f(p,"uWhite",     white);
        });
    }

    // ── ASCII (visual transform — runs after all glitch) ─────────────────────

    if (fire(FxId::ASCII)) {
        pass(prog_ascii_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, ascii_font_tex_, "uFontAtlas");
            u2f(p,"uResolution",(float)W,(float)H);
            u1f(p,"uIntensity", fi_base);
            u1f(p,"uColor",     1.0f);  // keep original colors
        });
    }

    // ── Overlay composite ─────────────────────────────────────────────────────

    if (params[(int)FxId::OVERLAYS].enabled && overlay_tex && overlay_alpha > 0.01f) {
        pass(prog_overlay_, main_fbo_.read_tex(), [&](GLuint p){
            bind_tex(p, 1, overlay_tex, "uOverlay");
            u2f(p,"uOverlayPos",  overlay_x, overlay_y);
            u2f(p,"uOverlaySize", overlay_w, overlay_h);
            u1f(p,"uTolerance",   chroma.tolerance / 180.f);
            u1f(p,"uSoftness",    chroma.softness  / 180.f);
            u3f(p,"uKeyColor",    chroma.r/255.f, chroma.g/255.f, chroma.b/255.f);
            u1i(p,"uMode",        (int)chroma.mode);
            u1f(p,"uOverlayAlpha",overlay_alpha);
        });
    }

    // ── Master intensity blend (dry/wet) ──────────────────────────────────────
    // For master_intensity < 1: blend processed result toward dry input
    if (master_intensity < 0.999f) {
        GLuint wet = main_fbo_.read_tex();
        // We stored the dry input at the start — it's in h0 (1 frame ago)
        // Actually "dry" for this frame is input_tex, which we already blitted
        // into hist_tex[hist_idx before push] at end of frame.
        // Simplest correct approach: blit the wet output into the write fbo
        // mixing with input_tex.
        glBindFramebuffer(GL_FRAMEBUFFER, main_fbo_.write_fbo());
        glViewport(0,0,W,H);
        glUseProgram(prog_pass_);
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, wet);
        glUniform1i(glGetUniformLocation(prog_pass_,"uTex"),0);
        glBindVertexArray(quad_vao_); glDrawArrays(GL_TRIANGLES,0,6); glBindVertexArray(0);
        main_fbo_.swap();
    }

    // ── Push current result into history ring ─────────────────────────────────
    push_history();

    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    return main_fbo_.read_tex();
}
