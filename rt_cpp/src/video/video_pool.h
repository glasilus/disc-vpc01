#pragma once
#include "video_source.h"
#include <vector>
#include <string>
#include <memory>

class VideoPool {
public:
    void add_source(const std::string& path);
    void clear();
    bool empty() const { return sources_.empty(); }
    int  size()  const { return (int)sources_.size(); }
    const std::vector<std::string>& paths() const { return paths_; }

    // Call from render thread each frame to pump GPU uploads
    void pump_uploads();

    GLuint get_random_frame(int w, int h, int* out_w = nullptr, int* out_h = nullptr);
    GLuint get_sequential_frame(int w, int h, int* out_w = nullptr, int* out_h = nullptr);

    // VJ-style "active clip" focus. When active_idx_ is in [0, size()-1],
    // every frame request — random or sequential, in cut or continuous
    // mode — is served from exactly that source. Set to -1 to fall back
    // to the full-pool behaviour (round-robin sequential / random across
    // all sources). The caller is responsible for clamping the index;
    // an out-of-range value resets to -1.
    void set_active(int idx);
    int  active() const { return active_idx_; }

private:
    std::vector<std::unique_ptr<VideoSource>> sources_;
    std::vector<std::string>                  paths_;
    int                                       round_robin_ = 0;
    int                                       active_idx_  = -1;
    // Loop-count snapshot of the current round-robin source. When the live
    // counter exceeds this, the source has played through once and we hop to
    // the next entry in the pool — giving "play A fully, then B, then C"
    // semantics without focus, instead of switching every render frame.
    int                                       rr_loop_baseline_ = 0;
};
