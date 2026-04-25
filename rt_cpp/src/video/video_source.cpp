#include "video_source.h"
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <stdexcept>

extern "C" {
#include <libavutil/error.h>
#include <libavutil/log.h>
}

static void log_av_error(const char* where, int err) {
    char buf[256] = {};
    av_strerror(err, buf, sizeof(buf));
    fprintf(stderr, "[video] %s failed (%d): %s\n", where, err, buf);
}

VideoSource::VideoSource(const std::string& path) : path_(path) {
    // Create GL textures (must be called from render thread)
    glGenTextures(kTexPoolSize, tex_pool_);
    for (int i = 0; i < kTexPoolSize; ++i) {
        glBindTexture(GL_TEXTURE_2D, tex_pool_[i]);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    }
    glBindTexture(GL_TEXTURE_2D, 0);

    open_ = open_decoder();
    if (!open_) return;

    // Start background decode thread
    decode_thread_ = std::thread(&VideoSource::decode_thread_fn, this);
}

VideoSource::~VideoSource() {
    stop_thread_.store(true);
    queue_cv_.notify_all();
    if (decode_thread_.joinable()) decode_thread_.join();
    close_decoder();
    glDeleteTextures(kTexPoolSize, tex_pool_);
}

bool VideoSource::open_decoder() {
    fmt_ctx_ = nullptr;
    // NOTE: path_ MUST be UTF-8. On Windows, FFmpeg decodes UTF-8 paths via
    // avutil's wchar helpers; ANSI (CP1251 etc) paths silently fail.
    int err = avformat_open_input(&fmt_ctx_, path_.c_str(), nullptr, nullptr);
    if (err < 0) { log_av_error("avformat_open_input", err); return false; }

    err = avformat_find_stream_info(fmt_ctx_, nullptr);
    if (err < 0) { log_av_error("avformat_find_stream_info", err); return false; }

    video_stream_idx_ = av_find_best_stream(fmt_ctx_, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    if (video_stream_idx_ < 0) {
        fprintf(stderr, "[video] no video stream in %s\n", path_.c_str());
        return false;
    }

    AVStream* stream = fmt_ctx_->streams[video_stream_idx_];
    duration_ts_     = fmt_ctx_->duration;
    src_w_           = stream->codecpar->width;
    src_h_           = stream->codecpar->height;

    const AVCodec* codec = avcodec_find_decoder(stream->codecpar->codec_id);
    if (!codec) {
        fprintf(stderr, "[video] no decoder for codec in %s\n", path_.c_str());
        return false;
    }

    codec_ctx_ = avcodec_alloc_context3(codec);
    avcodec_parameters_to_context(codec_ctx_, stream->codecpar);
    codec_ctx_->thread_count = 2;
    err = avcodec_open2(codec_ctx_, codec, nullptr);
    if (err < 0) { log_av_error("avcodec_open2", err); return false; }

    av_frame_  = av_frame_alloc();
    rgb_frame_ = av_frame_alloc();
    fprintf(stderr, "[video] opened %s (%dx%d)\n", path_.c_str(), src_w_, src_h_);
    return true;
}

void VideoSource::close_decoder() {
    if (sws_ctx_)   { sws_freeContext(sws_ctx_); sws_ctx_ = nullptr; }
    if (av_frame_)  { av_frame_free(&av_frame_); }
    if (rgb_frame_) { av_frame_free(&rgb_frame_); }
    if (codec_ctx_) { avcodec_free_context(&codec_ctx_); }
    if (fmt_ctx_)   { avformat_close_input(&fmt_ctx_); }
}

void VideoSource::seek_random() {
    if (!fmt_ctx_ || duration_ts_ <= 0) return;
    int64_t ts = (int64_t)((double)rand() / RAND_MAX * duration_ts_);
    av_seek_frame(fmt_ctx_, -1, ts, AVSEEK_FLAG_BACKWARD);
    avcodec_flush_buffers(codec_ctx_);
}

bool VideoSource::decode_next(DecodedFrame& out, int /*w*/, int /*h*/) {
    // Cap the decode target so a 4K video doesn't eat gigabytes of RAM when
    // multiplied by the 30-frame pool (4K × 30 ≈ 720 MB). 1920×1080 is plenty
    // for any canvas we expose, and effects run on the canvas anyway.
    constexpr int kMaxW = 1920, kMaxH = 1080;
    const int nw_src = codec_ctx_->width, nh_src = codec_ctx_->height;
    int tw = nw_src, th = nh_src;
    if (tw > kMaxW || th > kMaxH) {
        float s = std::min((float)kMaxW / tw, (float)kMaxH / th);
        tw = std::max(2, (int)(tw * s) & ~1);   // even dims keep sws happy
        th = std::max(2, (int)(th * s) & ~1);
    }

    if (!sws_ctx_) {
        sws_ctx_ = sws_getContext(
            nw_src, nh_src, codec_ctx_->pix_fmt,
            tw,     th,     AV_PIX_FMT_RGB24,
            SWS_BILINEAR, nullptr, nullptr, nullptr);
        dec_w_ = tw; dec_h_ = th;
    }
    if (!sws_ctx_) return false;

    out.width  = dec_w_;
    out.height = dec_h_;
    out.pixels.resize((size_t)dec_w_ * dec_h_ * 3);

    uint8_t* dst_data[4]    = { out.pixels.data(), nullptr, nullptr, nullptr };
    int      dst_linesize[4] = { dec_w_ * 3, 0, 0, 0 };

    AVPacket* pkt = av_packet_alloc();
    bool got_frame = false;
    int  tries = 0;

    while (!got_frame && tries < 500) {
        if (av_read_frame(fmt_ctx_, pkt) < 0) {
            // End of file — loop back
            av_seek_frame(fmt_ctx_, video_stream_idx_, 0, AVSEEK_FLAG_BACKWARD);
            avcodec_flush_buffers(codec_ctx_);
            av_packet_free(&pkt);
            pkt = av_packet_alloc();
            tries++;
            continue;
        }
        if (pkt->stream_index != video_stream_idx_) {
            av_packet_unref(pkt);
            tries++;
            continue;
        }
        if (avcodec_send_packet(codec_ctx_, pkt) == 0) {
            if (avcodec_receive_frame(codec_ctx_, av_frame_) == 0) {
                sws_scale(sws_ctx_,
                    av_frame_->data, av_frame_->linesize, 0, nh_src,
                    dst_data, dst_linesize);
                got_frame = true;
            }
        }
        av_packet_unref(pkt);
        tries++;
    }
    av_packet_free(&pkt);
    return got_frame;
}

void VideoSource::decode_thread_fn() {
    while (!stop_thread_.load()) {
        {
            std::unique_lock<std::mutex> lk(queue_mutex_);
            queue_cv_.wait(lk, [&]{
                return stop_thread_.load() || (int)ready_queue_.size() < kTexPoolSize;
            });
        }
        if (stop_thread_.load()) break;

        DecodedFrame frame;
        if (decode_next(frame, 0, 0)) {
            std::lock_guard<std::mutex> lk(queue_mutex_);
            ready_queue_.push_back(std::move(frame));
            queue_cv_.notify_one();
        }
    }
}

void VideoSource::pump_uploads() {
    std::unique_lock<std::mutex> lk(queue_mutex_);
    int uploaded = 0;
    while (!ready_queue_.empty() && uploaded < 3) {
        DecodedFrame& f = ready_queue_.front();
        GLuint tex = tex_pool_[tex_next_];
        glBindTexture(GL_TEXTURE_2D, tex);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, f.width, f.height, 0,
                     GL_RGB, GL_UNSIGNED_BYTE, f.pixels.data());
        tex_w_[tex_next_] = f.width;
        tex_h_[tex_next_] = f.height;
        tex_next_ = (tex_next_ + 1) % kTexPoolSize;
        if (tex_ready_count_ < kTexPoolSize) tex_ready_count_++;
        ready_queue_.pop_front();
        uploaded++;
    }
    glBindTexture(GL_TEXTURE_2D, 0);
    lk.unlock();
    queue_cv_.notify_all();
}

GLuint VideoSource::get_random_frame(int /*w*/, int /*h*/, int* out_w, int* out_h) {
    pump_uploads();
    if (tex_ready_count_ == 0) return 0;
    int idx = rand() % tex_ready_count_;
    if (out_w) *out_w = tex_w_[idx];
    if (out_h) *out_h = tex_h_[idx];
    return tex_pool_[idx];
}

GLuint VideoSource::get_sequential_frame(int /*w*/, int /*h*/, int* out_w, int* out_h) {
    pump_uploads();
    if (tex_ready_count_ == 0) return 0;
    int idx = seq_idx_ % tex_ready_count_;
    GLuint tex = tex_pool_[idx];
    if (out_w) *out_w = tex_w_[idx];
    if (out_h) *out_h = tex_h_[idx];
    seq_idx_ = (seq_idx_ + 1) % kTexPoolSize;
    return tex;
}
