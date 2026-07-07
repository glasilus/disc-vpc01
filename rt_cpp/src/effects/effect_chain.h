#pragma once
#include <glad/glad.h>
#include <string>
#include <functional>
#include "../audio/audio_stats.h"
#include "../audio/segment.h"
#include "../video/overlay_manager.h"


// Effect identifiers. Preset compatibility is keyed by the STRING returned by
// fx_key() (not by the numeric value), so this list can be reordered/extended
// freely - old presets simply match by name and default the rest to disabled.
// NOTE: Waveshaper was removed (low visual value); old "fx_waveshaper" keys are
// silently ignored on load.
enum class FxId {
    DERIVWARP   = 0,   // replaces fx_rgb - derivative warp (datamosh-like)
    FLASH,
    STUTTER,
    PIXEL_SORT,
    GHOST,
    SCANLINES,
    BITCRUSH,
    BLOCKGLITCH,
    NEGATIVE,
    COLORBLEED,
    INTERLACE,
    BADSIGNAL,
    ZOOMGLITCH,
    MOSAIC,
    PHASESHIFT,
    DITHER,
    FEEDBACK,
    TEMPORALRGB,
    OVERLAYS,
    VORTEX,          // spiral/twist warp
    FRACTALNOISE,    // domain-warped FBM distortion
    SELFDISP,        // prev frame as displacement map (closest to datamosh)
    ASCII,           // GPU ASCII filter
    // ── Wired-in classics (shaders existed but were never hooked up) ─────────
    RGBSHIFT,        // chromatic RGB channel split
    KALI,            // kaleidoscope / mirror symmetry
    FISHEYE,         // barrel / fisheye lens
    VHSTRACK,        // VHS tracking roll
    PIXELDRIFT,      // per-row horizontal drift
    // ── Datamosh family (temporal corruption; feed off the previous output) ──
    PFRAME_LAG,      // P-frame lag / block freeze melt
    MVEC_BLOOM,      // wrong motion vectors / bloom smear
    SELF_CANNIBALIZE,// self-consuming displacement
    // ── Generative visualizers (draw imagery FROM audio, over the canvas) ────
    VIZ_PLASMA,
    VIZ_RADIAL,
    VIZ_BARS,
    VIZ_ALCHEMY,
    COUNT
};

// ── Effect metadata: SINGLE source of truth (see kFxInfo in effect_chain.cpp) ─
// The JSON preset key, the GUI label, and the GUI category for every effect all
// come from one table, so adding an effect can't leave the key/label/group
// arrays out of sync. To add an effect: append a row to kFxInfo, add the FxId,
// wire its shader (include + program + one pass block in apply()). Nothing else
// reads a parallel list.
const char* fx_key(FxId id);    // JSON preset key, e.g. "fx_ghost"
const char* fx_label(FxId id);  // GUI display name, e.g. "Ghost Trails"
const char* fx_group(FxId id);  // GUI category, e.g. "CORE"
const char* fx_tip(FxId id);    // GUI hover tooltip: how the effect LOOKS

// Category display order for the GUI effects panel (sections rendered in this
// order; any effect whose group isn't listed falls into a trailing "OTHER").
extern const char* const kFxGroupOrder[];
extern const int         kFxGroupOrderCount;

// Keyboard / display ordering. Effects are laid out grouped by category (in
// kFxGroupOrder), NOT in raw enum order, so the Q..P key banks line up with the
// grouped GUI list instead of jumping around. Both the keyboard handler and the
// GUI highlight read these so they stay in lockstep.
//   fx_slot_to_id(slot) : slot 0..COUNT-1  -> FxId index, or -1 if out of range
//   fx_id_to_slot(id)   : FxId index        -> slot 0..COUNT-1
int fx_slot_to_id(int slot);
int fx_id_to_slot(int id);

// How an effect's audio-reactive envelope is driven.
//   Auto      - attack on musical accents (beat OR segment change), then decay.
//   Beat      - attack strictly on detected beats, then decay.
//   Sustained - envelope continuously tracks audio loudness (no strobe).
//   Manual    - always full-on while enabled (VJ holds it); ignores audio.
enum class TriggerMode { Auto = 0, Beat = 1, Sustained = 2, Manual = 3 };

struct EffectParams {
    bool  enabled   = false;
    float intensity = 1.0f;   // user strength 0..1 (scales the shader effect)
    float chance    = 0.6f;   // probability to fire on an eligible event (Auto/Beat)
    int   mode      = (int)TriggerMode::Auto;
};

enum class AspectMode { Contain = 0, Cover = 1, Stretch = 2, Native = 3 };

// Ping-pong framebuffer pair for shader passes
struct FboPair {
    GLuint fbo[2]  = {};
    GLuint tex[2]  = {};
    int    current = 0;
    int    width   = 0, height = 0;

    void   create(int w, int h);
    void   destroy();
    GLuint read_tex()  const { return tex[current]; }
    GLuint read_fbo()  const { return fbo[current]; }
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
    // src_w/src_h are the native dimensions of input_tex; used by the
    // aspect-aware canvas placement pass.
    GLuint apply(
        GLuint              input_tex,
        int                 src_w, int src_h,
        AspectMode          aspect,
        GLuint              overlay_tex,
        float               overlay_x, float overlay_y,
        float               overlay_w, float overlay_h,
        const ChromaKeyParams& chroma,
        float               overlay_alpha,
        const Segment&      seg,
        const AudioStats&   stats,
        float               chaos,
        float               master_intensity,
        float               time_sec,
        float               dt,
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
    GLuint prog_place_  = 0;   // aspect-aware canvas placement
    GLuint prog_mix_    = 0;   // dry/wet mix for master_intensity blend
    // Dry copy of the canvas-placed input, captured before any effects run.
    // Used by the final master_intensity blend to fade the processed result
    // back toward the un-effected image.
    GLuint dry_fbo_ = 0;
    GLuint dry_tex_ = 0;
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
    GLuint prog_overlay_     = 0;
    GLuint prog_vortex_      = 0;
    GLuint prog_fractalnoise_= 0;
    GLuint prog_selfdisp_    = 0;
    GLuint prog_ascii_       = 0;
    // Wired-in classics
    GLuint prog_rgbshift_    = 0;
    GLuint prog_kali_        = 0;
    GLuint prog_fisheye_     = 0;
    GLuint prog_vhstrack_    = 0;
    GLuint prog_pixeldrift_  = 0;
    // Datamosh family
    GLuint prog_pframe_lag_  = 0;
    GLuint prog_mvec_bloom_  = 0;
    GLuint prog_self_cannib_ = 0;
    // Generative visualizers
    GLuint prog_viz_plasma_  = 0;
    GLuint prog_viz_radial_  = 0;
    GLuint prog_viz_bars_    = 0;
    GLuint prog_viz_alchemy_ = 0;

    // ── Per-effect envelope state (runtime, not persisted) ────────────────────
    // Each effect has a 0..1 envelope that attacks on trigger events and decays
    // over time (or continuously tracks audio for Sustained/Manual modes). The
    // effect is applied with strength = env * intensity; this replaces the old
    // per-frame Bernoulli firing that made everything strobe.
    float env_[(int)FxId::COUNT] = {};
    bool  prev_beat_ = false;    // rising-edge detect on stats.beat
    int   prev_seg_  = -1;       // SegmentType of previous frame (change detect)
    void  update_envelopes(const Segment& seg, const AudioStats& stats,
                           float chaos, float dt, EffectParams params[]);
    // True if any enabled effect currently consumes the frame-history ring -
    // lets us skip the per-frame history blit entirely when nothing needs it.
    bool  needs_history(EffectParams params[]) const;

    GLuint quad_vao_ = 0, quad_vbo_ = 0;

    // ASCII font texture (80×8, one-time upload)
    GLuint ascii_font_tex_ = 0;
    void   create_ascii_font_tex();
};
