#include "video_source.h"
#include <cstdlib>
#include <cstring>
#include <stdexcept>

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
    if (avformat_open_input(&fmt_ctx_, path_.c_str(), nullptr, nullptr) < 0) return false;
    if (avformat_find_stream_info(fmt_ctx_, nullptr) < 0) return false;

    video_stream_idx_ = av_find_best_stream(fmt_ctx_, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
    if (video_stream_idx_ < 0) return false;

    AVStream* stream = fmt_ctx_->streams[video_stream_idx_];
    duration_ts_     = fmt_ctx_->duration;

    const AVCodec* codec = avcodec_find_decoder(stream->codecpar->codec_id);
    if (!codec) return false;

    codec_ctx_ = avcodec_alloc_context3(codec);
    avcodec_parameters_to_context(codec_ctx_, stream->codecpar);
    codec_ctx_->thread_count = 2;
    if (avcodec_open2(codec_ctx_, codec, nullptr) < 0) return false;

    av_frame_  = av_frame_alloc();
    rgb_frame_ = av_frame_alloc();
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

bool VideoSource::decode_next(DecodedFrame& out, int w, int h) {
    if (!sws_ctx_ || target_w_ != w || target_h_ != h) {
        if (sws_ctx_) sws_freeContext(sws_ctx_);
        sws_ctx_ = sws_getContext(
            codec_ctx_->width, codec_ctx_->height, codec_ctx_->pix_fmt,
            w, h, AV_PIX_FMT_RGB24,
            SWS_BILINEAR, nullptr, nullptr, nullptr);
        target_w_ = w; target_h_ = h;
    }
    if (!sws_ctx_) return false;

    out.width  = w;
    out.height = h;
    out.pixels.resize(w * h * 3);

    uint8_t* dst_data[4]    = { out.pixels.data(), nullptr, nullptr, nullptr };
    int      dst_linesize[4] = { w * 3, 0, 0, 0 };

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
                    av_frame_->data, av_frame_->linesize, 0, codec_ctx_->height,
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
            // Keep up to kTexPoolSize frames decoded ahead
            queue_cv_.wait(lk, [&]{
                return stop_thread_.load() || (int)ready_queue_.size() < kTexPoolSize;
            });
        }
        if (stop_thread_.load()) break;

        DecodedFrame frame;
        if (decode_next(frame, target_w_, target_h_)) {
            std::lock_guard<std::mutex> lk(queue_mutex_);
            ready_queue_.push_back(std::move(frame));
            queue_cv_.notify_one();
        }
    }
}

void VideoSource::pump_uploads() {
    // Upload up to 3 pending CPU frames to GPU per render frame
    std::unique_lock<std::mutex> lk(queue_mutex_);
    int uploaded = 0;
    while (!ready_queue_.empty() && uploaded < 3) {
        DecodedFrame& f = ready_queue_.front();
        GLuint tex = tex_pool_[tex_next_];
        glBindTexture(GL_TEXTURE_2D, tex);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, f.width, f.height, 0,
                     GL_RGB, GL_UNSIGNED_BYTE, f.pixels.data());
        tex_next_ = (tex_next_ + 1) % kTexPoolSize;
        if (tex_ready_count_ < kTexPoolSize) tex_ready_count_++;
        ready_queue_.pop_front();
        uploaded++;
    }
    glBindTexture(GL_TEXTURE_2D, 0);
    lk.unlock();
    queue_cv_.notify_all();
}

GLuint VideoSource::get_random_frame(int w, int h) {
    target_w_ = w; target_h_ = h;
    pump_uploads();
    if (tex_ready_count_ == 0) return 0;
    int idx = rand() % tex_ready_count_;
    return tex_pool_[idx];
}

GLuint VideoSource::get_sequential_frame(int w, int h) {
    target_w_ = w; target_h_ = h;
    pump_uploads();
    if (tex_ready_count_ == 0) return 0;
    GLuint tex = tex_pool_[seq_idx_ % tex_ready_count_];
    seq_idx_   = (seq_idx_ + 1) % kTexPoolSize;
    return tex;
}
