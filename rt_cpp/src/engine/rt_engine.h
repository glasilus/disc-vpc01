#pragma once
#include <initializer_list>
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
    // Политика выбора кадра:
    //   0 = Continuous - линейное воспроизведение одного источника, только эффекты.
    //   1 = Cut        - случайные склейки на битах / импактах / дропах.
    int   cut_mode          = 1;
    bool  sequential        = false;  // легаси, оставлено ради старых пресетов
    float ck_tolerance      = 30.f;
    float ck_softness       = 5.f;
    float ck_r = 0.f, ck_g = 255.f, ck_b = 0.f;
    int   ck_mode           = 0;   // 0=none 1=dominant 2=secondary 3=manual
    bool  ck_gate_fx        = false;
    int   ck_gate_mode      = 0;   // 0=foreground, 1=background
    int   aspect_mode       = 1;   // AspectMode: 0=Contain 1=Cover 2=Stretch 3=Native
    EffectParams fx[(int)FxId::COUNT];

    // Разумные дефолты trigger-mode для каждого эффекта, чтобы цепочка выглядела
    // адекватно сразу после старта (и после сброса пресета). "Смотрящие" фильтры
    // плавно следуют за аудио (Sustained); визуализаторы просто горят, если включены
    // (Manual); резкие глитчи остаются на дефолтном Auto (атака на акцент, спад).
    // Это только дефолты - GUI и пресеты могут переопределить любой из них.
    EngineSettings() {
        auto set_mode = [&](FxId id, TriggerMode m){ fx[(int)id].mode = (int)m; };
        for (FxId id : { FxId::SCANLINES, FxId::NEGATIVE, FxId::DITHER, FxId::BITCRUSH,
                         FxId::FISHEYE, FxId::KALI, FxId::RGBSHIFT, FxId::VHSTRACK,
                         FxId::INTERLACE, FxId::MOSAIC, FxId::ASCII, FxId::COLORBLEED,
                         FxId::PFRAME_LAG, FxId::MVEC_BLOOM, FxId::SELF_CANNIBALIZE })
            set_mode(id, TriggerMode::Sustained);
        for (FxId id : { FxId::VIZ_PLASMA, FxId::VIZ_RADIAL, FxId::VIZ_BARS, FxId::VIZ_ALCHEMY })
            set_mode(id, TriggerMode::Manual);
    }
};

struct CanvasPreset {
    const char* label;
    int         width;
    int         height;
};
// Разрешения канваса, доступные из GUI. Движок по умолчанию берёт первое.
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

    // Переконфигурировать разрешение внутреннего канваса (FBO). Можно звать в любой
    // момент из render-потока - пересоздаёт все ping-pong и history FBO.
    void set_canvas_size(int w, int h);

    int canvas_width()  const { return width_; }
    int canvas_height() const { return height_; }

    // Вызывается раз за кадр рендера. Возвращает GL-текстуру для отображения.
    GLuint process_frame(float dt, EngineSettings& settings);

    AudioAnalyzer&  audio()    { return audio_; }
    VideoPool&      video()    { return pool_; }
    OverlayManager& overlays() { return overlays_; }

    Segment current_segment() const { return last_segment_; }
    AudioStats current_stats() const { return last_stats_; }

    bool blackout = false;
    bool freeze   = false;

    // ── Tap-tempo метроном ────────────────────────────────────────────────────
    // Если включён, подмешивает синтетический бит по сетке настуканного BPM
    // (через OR с детектированным из аудио битом), чтобы склейки и эффекты
    // держали темп даже когда в материале слабый или неоднозначный transient.
    bool  metronome = false;
    void  set_bpm(float b) { bpm_ = (b < 0.f) ? 0.f : (b > 300.f ? 300.f : b); }
    float bpm() const { return bpm_; }

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
    float  bpm_            = 0.f;
    float  beat_phase_    = 0.f;

    AudioStats last_stats_   = {};
    Segment    last_segment_ = {};

    int width_ = 0, height_ = 0;

    // Состояние активного оверлея (значения фиксируются по биту и держатся между кадрами)
    GLuint current_overlay_tex_ = 0;
    float  current_overlay_x_   = 0.f;
    float  current_overlay_y_   = 0.f;
    float  current_overlay_w_   = 0.3f;
    float  current_overlay_h_   = 0.3f;
};
