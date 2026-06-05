#include "rt_engine.h"
#include <vector>
#include <cstring>
#include <algorithm>

bool RtEngine::init(int w, int h) {
    width_ = w; height_ = h;

    // Black texture (fallback / blackout). Always 1×1 — we never sample from
    // it at a specific resolution, only as a uniform color.
    uint8_t black_px[3] = {0, 0, 0};
    glGenTextures(1, &black_tex_);
    glBindTexture(GL_TEXTURE_2D, black_tex_);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, 1, 1, 0, GL_RGB, GL_UNSIGNED_BYTE, black_px);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glBindTexture(GL_TEXTURE_2D, 0);

    return fx_.init(w, h);
}

void RtEngine::set_canvas_size(int w, int h) {
    if (w == width_ && h == height_) return;
    width_ = w; height_ = h;
    fx_.resize(w, h);
    last_frame_tex_ = 0;  // dimension-dependent refs invalidated
}

void RtEngine::destroy() {
    fx_.destroy();
    if (black_tex_) { glDeleteTextures(1, &black_tex_); black_tex_ = 0; }
    audio_.stop();
}

GLuint RtEngine::process_frame(float dt, EngineSettings& settings) {
    elapsed_time_   += dt;
    time_since_cut_ += dt;

    // ── Audio ─────────────────────────────────────────────────────────────────
    last_stats_   = audio_.get_stats();
    float gate    = audio_.get_gate() * settings.sensitivity;
    last_segment_ = classify_segment(last_stats_, gate);

    if (blackout) return black_tex_;

    // ── Video frame selection ─────────────────────────────────────────────────
    // Skip GPU uploads while frozen — otherwise the decoder keeps overwriting
    // the textures that last_frame_tex_ points to, and the "frozen" image
    // visibly drifts. Decoder thread will block on the queue when its CPU
    // buffer fills (~0.1 sec of slack), which is fine.
    if (!freeze) pool_.pump_uploads();

    GLuint frame_tex = 0;
    int    frame_w = 0, frame_h = 0;
    bool   trigger_cut = false;

    if (freeze) {
        frame_tex = last_frame_tex_ ? last_frame_tex_ : black_tex_;
        frame_w   = last_frame_w_;
        frame_h   = last_frame_h_;
    } else if (settings.cut_mode == 0) {
        // Continuous mode: linear playback of one source, no cuts. Effects
        // still react to audio. This is what most VJs want when the music
        // has no clear beat or when they want a steady visual base.
        frame_tex = pool_.get_sequential_frame(width_, height_, &frame_w, &frame_h);
    } else {
        // Cut mode: random cuts on beats / impacts / drops. cut_interval
        // gates softer (build / sustain) cuts so they don't feel frantic.
        auto t = last_segment_.type;
        const float kMinCutMs = 0.030f;  // 30ms hard-trigger anti-spam
        bool hard_trigger = (t == SegmentType::IMPACT || t == SegmentType::DROP ||
                             last_stats_.beat);
        bool soft_trigger = (t == SegmentType::BUILD ||
                             (t == SegmentType::SUSTAIN && last_stats_.beat));

        if (hard_trigger && time_since_cut_ >= kMinCutMs) {
            time_since_cut_ = 0.f;
            trigger_cut = true;
        } else if (soft_trigger && time_since_cut_ >= settings.cut_interval) {
            time_since_cut_ = 0.f;
            trigger_cut = true;
        }

        frame_tex = pool_.get_cut_frame(trigger_cut, width_, height_, &frame_w, &frame_h);
    }

    if (!freeze) {
        if (!frame_tex) {
            frame_tex = last_frame_tex_ ? last_frame_tex_ : black_tex_;
            frame_w   = last_frame_w_;
            frame_h   = last_frame_h_;
        } else {
            last_frame_tex_ = frame_tex;
            last_frame_w_   = frame_w;
            last_frame_h_   = frame_h;
        }
    }

    // ── Overlay ───────────────────────────────────────────────────────────────
    if (settings.overlay_intensity <= 0.01f || overlays_.empty()) {
        current_overlay_tex_ = 0;
    } else {
        bool overlay_beat = false;
        if (settings.cut_mode == 0) {
            // In Continuous Mode: trigger a potential overlay change on beats / impacts
            overlay_beat = (last_stats_.beat || last_segment_.type == SegmentType::IMPACT);
        } else {
            // In Cut Mode: sync overlay changes precisely with video cuts
            overlay_beat = trigger_cut;
        }

        if (overlay_beat) {
            if ((float)rand() / RAND_MAX < settings.overlay_intensity) {
                const OverlayEntry* ov = overlays_.random_entry();
                if (ov) {
                    current_overlay_tex_ = ov->tex;
                    float scale = 0.3f + (float)rand() / RAND_MAX * 0.5f;
                    current_overlay_w_ = scale;
                    current_overlay_h_ = scale * ((float)ov->height / (float)ov->width)
                                                 * ((float)width_  / (float)height_);
                    current_overlay_x_ = (float)rand() / RAND_MAX * std::max(0.f, 1.f - current_overlay_w_);
                    current_overlay_y_ = (float)rand() / RAND_MAX * std::max(0.f, 1.f - current_overlay_h_);
                }
            } else {
                current_overlay_tex_ = 0;
            }
        }
    }

    // ── Chroma key ────────────────────────────────────────────────────────────
    ChromaKeyParams ck;
    ck.mode      = (ChromaMode)settings.ck_mode;
    ck.tolerance = settings.ck_tolerance;
    ck.softness  = settings.ck_softness;
    ck.r = settings.ck_r; ck.g = settings.ck_g; ck.b = settings.ck_b;

    // ── Apply effects ─────────────────────────────────────────────────────────
    AspectMode am = (AspectMode)settings.aspect_mode;
    return fx_.apply(
        frame_tex, frame_w, frame_h, am,
        current_overlay_tex_, current_overlay_x_, current_overlay_y_,
        current_overlay_w_, current_overlay_h_, ck,
        settings.overlay_intensity,
        last_segment_,
        settings.chaos,
        settings.master_intensity,
        elapsed_time_,
        settings.fx
    );
}
