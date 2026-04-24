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
    bool  sequential        = false;
    float ck_tolerance      = 30.f;
    float ck_softness       = 5.f;
    float ck_r = 0.f, ck_g = 255.f, ck_b = 0.f;
    int   ck_mode           = 0;   // 0=none 1=dominant 2=secondary 3=manual
    EffectParams fx[(int)FxId::COUNT];
};

class RtEngine {
public:
    RtEngine()  = default;
    ~RtEngine() { destroy(); }

    bool init(int width, int height);
    void destroy();

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

    float  time_since_cut_ = 0.f;
    float  elapsed_time_   = 0.f;

    AudioStats last_stats_   = {};
    Segment    last_segment_ = {};

    int width_ = 0, height_ = 0;
};
