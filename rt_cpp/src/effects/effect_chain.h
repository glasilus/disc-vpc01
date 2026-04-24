#pragma once
#include <glad/glad.h>
#include <string>
#include <functional>
#include "../audio/segment.h"
#include "../video/overlay_manager.h"

// Maps exactly to rt_blank.json fx_state keys (minus fx_datamosh, plus new effects)
enum class FxId {
    DERIVWARP   = 0,   // replaces fx_rgb — derivative warp (datamosh-like)
    FLASH       = 1,
    STUTTER     = 2,
    PIXEL_SORT  = 3,
    GHOST       = 4,
    SCANLINES   = 5,
    BITCRUSH    = 6,
    BLOCKGLITCH = 7,
    NEGATIVE    = 8,
    COLORBLEED  = 9,
    INTERLACE   = 10,
    BADSIGNAL   = 11,
    ZOOMGLITCH  = 12,
    MOSAIC      = 13,
    PHASESHIFT  = 14,
    DITHER      = 15,
    FEEDBACK    = 16,
    TEMPORALRGB = 17,
    WAVESHAPER  = 18,
    OVERLAYS    = 19,
    VORTEX      = 20,  // new: spiral/twist warp
    FRACTALNOISE= 21,  // new: domain-warped FBM distortion
    SELFDISP    = 22,  // new: prev frame as displacement map (closest to datamosh)
    ASCII       = 23,  // new: GPU ASCII filter
    COUNT       = 24
};

// JSON preset key for each FxId
const char* fx_key(FxId id);

struct EffectParams {
    bool  enabled   = false;
    float chance    = 0.5f;
};

// Ping-pong framebuffer pair for shader passes
struct FboPair {
    GLuint fbo[2]  = {};
    GLuint tex[2]  = {};
    int    current = 0;
    int    width   = 0, height = 0;

    void   create(int w, int h);
    void   destroy();
    GLuint read_tex()  const { return tex[current]; }
    GLuint write_fbo() const { return fbo[1 - current]; }
    void   swap()            { current = 1 - current; }
};

class EffectChain {
public:
    EffectChain();
    ~EffectChain();

    bool init(int width, int height);
    void resize(int w, int h);
    void destroy();

    // Apply all enabled effects. Returns final output GL texture.
    // Call every render frame from the OpenGL thread.
    GLuint apply(
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
        EffectParams        params[(int)FxId::COUNT]
    );

    int width()  const { return main_fbo_.width; }
    int height() const { return main_fbo_.height; }

private:
    GLuint compile_program(const char* vert, const char* frag);
    void   setup_quad();

    // Blit src_tex into dst_fbo via passthrough shader, then swap main_fbo_
    void   pass(GLuint prog, GLuint src_tex,
                const std::function<void(GLuint prog)>& set_uniforms);

    // Copy current main_fbo read_tex into history slot
    void   push_history();
    // history[0] = 1 frame ago, history[1] = 2 frames ago, history[2] = 3 frames ago
    GLuint history_tex(int age) const; // age 0..kHistoryLen-1

    // ── Framebuffers ─────────────────────────────────────────────────────────
    FboPair main_fbo_;
    FboPair accum_fbo_;  // fx_feedback persistent accumulator

    // History ring: kHistoryLen pre-allocated FBO/texture pairs
    static constexpr int kHistoryLen = 4;
    GLuint hist_fbo_[kHistoryLen] = {};
    GLuint hist_tex_[kHistoryLen] = {};
    int    hist_idx_ = 0;  // slot that will be written next
    bool   hist_full_ = false;

    // ── Shader programs ───────────────────────────────────────────────────────
    GLuint prog_pass_   = 0;
    GLuint prog_derivwarp_   = 0;
    GLuint prog_flash_       = 0;
    GLuint prog_stutter_     = 0;
    GLuint prog_pixsort_     = 0;
    GLuint prog_ghost_       = 0;
    GLuint prog_scanlines_   = 0;
    GLuint prog_bitcrush_    = 0;
    GLuint prog_blockglitch_ = 0;
    GLuint prog_negative_    = 0;
    GLuint prog_colorbleed_  = 0;
    GLuint prog_interlace_   = 0;
    GLuint prog_badsignal_   = 0;
    GLuint prog_zoomglitch_  = 0;
    GLuint prog_mosaic_      = 0;
    GLuint prog_phaseshift_  = 0;
    GLuint prog_dither_      = 0;
    GLuint prog_feedback_    = 0;
    GLuint prog_temporalrgb_ = 0;
    GLuint prog_waveshaper_  = 0;
    GLuint prog_overlay_     = 0;
    GLuint prog_vortex_      = 0;
    GLuint prog_fractalnoise_= 0;
    GLuint prog_selfdisp_    = 0;
    GLuint prog_ascii_       = 0;

    GLuint quad_vao_ = 0, quad_vbo_ = 0;

    // ASCII font texture (80×8, one-time upload)
    GLuint ascii_font_tex_ = 0;
    void   create_ascii_font_tex();
};
