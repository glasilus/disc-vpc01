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
    const std::vector<std::string>& paths() const { return paths_; }

    // Call from render thread each frame to pump GPU uploads
    void pump_uploads();

    GLuint get_random_frame(int w, int h);
    GLuint get_sequential_frame(int w, int h);

private:
    std::vector<std::unique_ptr<VideoSource>> sources_;
    std::vector<std::string>                  paths_;
    int                                       round_robin_ = 0;
};
