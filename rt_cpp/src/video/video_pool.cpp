#include "video_pool.h"
#include <algorithm>

void VideoPool::add_source(const std::string& path) {
    // Avoid duplicates
    if (std::find(paths_.begin(), paths_.end(), path) != paths_.end()) return;
    auto src = std::make_unique<VideoSource>(path);
    if (src->is_open()) {
        paths_.push_back(path);
        sources_.push_back(std::move(src));
    }
}

void VideoPool::clear() {
    sources_.clear();
    paths_.clear();
    round_robin_ = 0;
}

void VideoPool::pump_uploads() {
    for (auto& s : sources_) s->pump_uploads();
}

GLuint VideoPool::get_random_frame(int w, int h) {
    if (sources_.empty()) return 0;
    int idx = rand() % (int)sources_.size();
    return sources_[idx]->get_random_frame(w, h);
}

GLuint VideoPool::get_sequential_frame(int w, int h) {
    if (sources_.empty()) return 0;
    int idx = round_robin_ % (int)sources_.size();
    round_robin_ = (round_robin_ + 1) % (int)sources_.size();
    return sources_[idx]->get_sequential_frame(w, h);
}
