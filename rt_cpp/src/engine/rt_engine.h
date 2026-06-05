#pragma once
#include "../audio/audio_analyzer.h"
#include "../audio/segment.h"
#include "../video/video_pool.h"
#include "../video/overlay_manager.h"
#include "../effects/effect_chain.h"

struct EngineSettings {
    float chaos             = 0.5f;
    float sensitivity       = 1.0f;
    float master_intensity  = 1.0f;
    float cut_interval      = 0.3f;
    float overlay_intensity = 0.0f;
    // Frame-selection policy:
    //   0 = Continuous — linear playback through one source, effects only.
    //   1 = Cut        — random cuts on beats / impacts / drops.
    int   cut_mode          = 1;
    bool  sequential        = false;  // legacy; preserved for old presets
    float ck_tolerance      = 30.f;
    float ck_softness       = 5.f;
    float ck_r = 0.f, ck_g = 255.f, ck_b = 0.f;
    int   ck_mode           = 0;   // 0=none 1=dominant 2=secondary 3=manual
    int   aspect_mode       = 1;   // AspectMode: 0=Contain 1=Cover 2=Stretch 3=Native
    EffectParams fx[(int)FxId::COUNT];
};

struct CanvasPreset {
    const char* label;
    int         width;
    int         height;
};
// Canvas resolutions exposed to the GUI. Engine defaults to the first one.
static constexpr CanvasPreset kCanvasPresets[] = {
    {"1280 x 720  (16:9)",  1280,  720},
    {"1920 x 1080 (16:9)",  1920, 1080},
    {"1024 x 768  (4:3)",   1024,  768},
};
static constexpr int kCanvasPresetCount = (int)(sizeof(kCanvasPresets) / sizeof(kCanvasPresets[0]));

class RtEngine {
public:
    RtEngine()  = default;
    ~RtEngine() { destroy(); }

    bool init(int width, int height);
    void destroy();

    // Reconfigure the internal canvas (FBO) resolution. Safe to call at any
    // time from the render thread — recreates all ping-pong / history FBOs.
    void set_canvas_size(int w, int h);

    int canvas_width()  const { return width_; }
    int canvas_height() const { return height_; }

    // Call once per render frame. Returns GL texture to display.
    GLuint process_frame(float dt, EngineSettings& settings);

    AudioAnalyzer&  audio()    { return audio_; }
    VideoPool&      video()    { return pool_; }
    OverlayManager& overlays() { return overlays_; }

    Segment current_segment() const { return last_segment_; }
    AudioStats current_stats() const { return last_stats_; }

    bool blackout = false;
    bool freeze   = false;

private:
    AudioAnalyzer  audio_;
    VideoPool      pool_;
    OverlayManager overlays_;
    EffectChain    fx_;

    GLuint black_tex_      = 0;
    GLuint last_frame_tex_ = 0;
    int    last_frame_w_   = 0;
    int    last_frame_h_   = 0;

    float  time_since_cut_ = 0.f;
    float  elapsed_time_   = 0.f;

    AudioStats last_stats_   = {};
    Segment    last_segment_ = {};

    int width_ = 0, height_ = 0;

    // Active overlay state (beat-snapped persistent values)
    GLuint current_overlay_tex_ = 0;
    float  current_overlay_x_   = 0.f;
    float  current_overlay_y_   = 0.f;
    float  current_overlay_w_   = 0.3f;
    float  current_overlay_h_   = 0.3f;
};
