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

    // Returns a GL texture ID with a decoded frame at NATIVE resolution
    // (caller must NOT delete). Writes out the texture dimensions through
    // out_w/out_h if non-null — needed by the canvas-placement shader for
    // correct aspect handling. The target_w/target_h args are kept for
    // backwards compatibility but ignored.
    GLuint get_random_frame(int target_w, int target_h, int* out_w = nullptr, int* out_h = nullptr);
    GLuint get_sequential_frame(int target_w, int target_h, int* out_w = nullptr, int* out_h = nullptr);

    int native_width()  const { return src_w_; }
    int native_height() const { return src_h_; }

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
    int              src_w_ = 0, src_h_ = 0;   // native video dimensions
    int              dec_w_ = 0, dec_h_ = 0;   // capped decode dimensions

    // GL texture pool — dimensions tracked per-slot because decode runs at
    // native resolution (which can vary if we ever cache mixed-size frames).
    GLuint tex_pool_[kTexPoolSize] = {};
    int    tex_w_[kTexPoolSize] = {};
    int    tex_h_[kTexPoolSize] = {};
    int    tex_next_        = 0;
    int    tex_ready_count_ = 0;
    int    seq_idx_         = 0;

    // Background decode thread
    std::thread              decode_thread_;
    std::atomic<bool>        stop_thread_{false};
    std::mutex               queue_mutex_;
    std::condition_variable  queue_cv_;
    std::deque<DecodedFrame> ready_queue_;  // CPU frames awaiting GPU upload
    int target_w_ = 1280, target_h_ = 720;
};
