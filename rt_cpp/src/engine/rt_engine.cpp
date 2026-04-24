#include "rt_engine.h"
#include <vector>
#include <cstring>
#include <algorithm>

bool RtEngine::init(int w, int h) {
    width_ = w; height_ = h;

    // Black texture (fallback / blackout)
    std::vector<uint8_t> black(w * h * 3, 0);
    glGenTextures(1, &black_tex_);
    glBindTexture(GL_TEXTURE_2D, black_tex_);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, black.data());
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glBindTexture(GL_TEXTURE_2D, 0);

    return fx_.init(w, h);
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
    pool_.pump_uploads();

    GLuint frame_tex = 0;
    if (freeze) {
        frame_tex = last_frame_tex_ ? last_frame_tex_ : black_tex_;
    } else {
        if (settings.sequential) {
            frame_tex = pool_.get_sequential_frame(width_, height_);
        } else {
            auto t    = last_segment_.type;
            bool reactive = (t == SegmentType::IMPACT ||
                             t == SegmentType::BUILD   ||
                             t == SegmentType::DROP    ||
                             (t == SegmentType::SUSTAIN && last_stats_.beat));

            if (reactive && time_since_cut_ >= settings.cut_interval) {
                time_since_cut_ = 0.f;
                frame_tex = pool_.get_random_frame(width_, height_);
            } else {
                frame_tex = pool_.get_sequential_frame(width_, height_);
            }
        }
        if (!frame_tex) frame_tex = last_frame_tex_ ? last_frame_tex_ : black_tex_;
        last_frame_tex_ = frame_tex;
    }

    // ── Overlay ───────────────────────────────────────────────────────────────
    GLuint ov_tex = 0;
    float  ov_x = 0.f, ov_y = 0.f, ov_w = 0.3f, ov_h = 0.3f;
    if (!overlays_.empty() && settings.overlay_intensity > 0.01f) {
        if ((float)rand() / RAND_MAX < settings.overlay_intensity) {
            const OverlayEntry* ov = overlays_.random_entry();
            if (ov) {
                ov_tex = ov->tex;
                float scale = 0.3f + (float)rand() / RAND_MAX * 0.5f;
                ov_w = scale;
                ov_h = scale * ((float)ov->height / (float)ov->width)
                             * ((float)width_  / (float)height_);
                ov_x = (float)rand() / RAND_MAX * std::max(0.f, 1.f - ov_w);
                ov_y = (float)rand() / RAND_MAX * std::max(0.f, 1.f - ov_h);
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
    return fx_.apply(
        frame_tex,
        ov_tex, ov_x, ov_y, ov_w, ov_h, ck,
        settings.overlay_intensity,
        last_segment_,
        settings.chaos,
        settings.master_intensity,
        elapsed_time_,
        settings.fx
    );
}
