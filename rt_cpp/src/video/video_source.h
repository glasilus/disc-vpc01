#pragma once
#include <glad/glad.h>
#include <string>
#include <vector>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <deque>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libswscale/swscale.h>
}

static constexpr int kTexPoolSize = 30;

// One decoded frame stored in CPU memory (RGB24, ready to upload)
struct DecodedFrame {
    std::vector<uint8_t> pixels;
    int width  = 0;
    int height = 0;
};

// Manages decoding of a single video file + a pool of GL textures
class VideoSource {
public:
    explicit VideoSource(const std::string& path);
    ~VideoSource();

    bool is_open() const { return open_; }
    const std::string& path() const { return path_; }

    // Returns a GL texture ID with a decoded frame (caller must NOT delete)
    // upload_ctx must be called from the render (OpenGL) thread.
    GLuint get_random_frame(int target_w, int target_h);
    GLuint get_sequential_frame(int target_w, int target_h);

    // Call once per frame from render thread to pump pending GPU uploads
    void pump_uploads();

private:
    void decode_thread_fn();
    bool open_decoder();
    void close_decoder();
    bool decode_next(DecodedFrame& out, int w, int h);
    void seek_random();

    std::string path_;
    bool        open_ = false;

    // FFmpeg handles
    AVFormatContext* fmt_ctx_   = nullptr;
    AVCodecContext*  codec_ctx_ = nullptr;
    SwsContext*      sws_ctx_   = nullptr;
    AVFrame*         av_frame_  = nullptr;
    AVFrame*         rgb_frame_ = nullptr;
    int              video_stream_idx_ = -1;
    int64_t          duration_ts_      = 0;

    // GL texture pool
    GLuint tex_pool_[kTexPoolSize] = {};
    int    tex_next_        = 0;  // next upload slot (circular)
    int    tex_ready_count_ = 0;  // how many slots filled at least once
    int    seq_idx_         = 0;  // sequential playback cursor (per-instance)

    // Background decode thread
    std::thread              decode_thread_;
    std::atomic<bool>        stop_thread_{false};
    std::mutex               queue_mutex_;
    std::condition_variable  queue_cv_;
    std::deque<DecodedFrame> ready_queue_;  // CPU frames awaiting GPU upload
    int target_w_ = 1280, target_h_ = 720;
};
