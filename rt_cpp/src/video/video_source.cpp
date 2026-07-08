#include "video_source.h"
#include <GLFW/glfw3.h>
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
    fprintf(stderr, "[video] VideoSource ctor: %s\n", path.c_str());

    // Создаем GL-текстуры (вызывать обязательно из render-потока)
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
    if (!open_) { fprintf(stderr, "[video] open_decoder failed for %s\n", path.c_str()); return; }

    // Запускаем фоновый поток декодирования
    fprintf(stderr, "[video] launching decode thread for %s\n", path.c_str());
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
    // ВАЖНО: path_ обязан быть UTF-8. На Windows FFmpeg разбирает UTF-8 пути
    // через wchar-хелперы avutil; ANSI-пути (CP1251 и т.п.) молча ломаются.
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

    // Частота кадров: нужен avg_frame_rate. Если он нулевой, откатываемся на
    // r_frame_rate (для VFR-источников он может врать, но хотя бы не ноль).
    AVRational fr = stream->avg_frame_rate;
    if (fr.num <= 0 || fr.den <= 0) fr = stream->r_frame_rate;
    src_fps_ = (fr.num > 0 && fr.den > 0) ? av_q2d(fr) : 30.0;
    if (src_fps_ < 1.0 || src_fps_ > 240.0) src_fps_ = 30.0;

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
    // Ограничиваем разрешение декодирования, иначе 4K-видео сожрет гигабайты
    // RAM при умножении на пул из 30 кадров (4K × 30 ≈ 720 МБ). 1920×1080
    // с запасом хватает под любой канвас, который мы показываем, а эффекты
    // все равно работают уже на канвасе.
    constexpr int kMaxW = 1920, kMaxH = 1080;
    const int nw_src = codec_ctx_->width, nh_src = codec_ctx_->height;
    int tw = nw_src, th = nh_src;
    if (tw > kMaxW || th > kMaxH) {
        float s = std::min((float)kMaxW / tw, (float)kMaxH / th);
        tw = std::max(2, (int)(tw * s) & ~1);   // sws не любит нечетные размеры
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
            // Конец файла - зацикливаем сначала
            av_seek_frame(fmt_ctx_, video_stream_idx_, 0, AVSEEK_FLAG_BACKWARD);
            avcodec_flush_buffers(codec_ctx_);
            loop_count_.fetch_add(1, std::memory_order_relaxed);
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
        // Обрабатываем запрос на seek в начале итерации, чтобы он вытеснял
        // любое незавершенное декодирование. Уже готовые кадры в очереди
        // выбрасываем - они относятся к СТАРОЙ позиции и проиграются поверх cut'а.
        if (seek_request_.exchange(false)) {
            seek_random();
            std::lock_guard<std::mutex> lk(queue_mutex_);
            ready_queue_.clear();
            // Заставляем pump_uploads залить кадры без ожидания темпа, чтобы
            // новые кадры после seek попали на экран уже на следующем render-тике.
            last_upload_time_ = 0.0;
        }

        {
            std::unique_lock<std::mutex> lk(queue_mutex_);
            queue_cv_.wait(lk, [&]{
                return stop_thread_.load() || seek_request_.load() ||
                       (int)ready_queue_.size() < kTexPoolSize;
            });
        }
        if (stop_thread_.load()) break;
        if (seek_request_.load()) continue;  // перезапускаем итерацию ради обработки seek

        DecodedFrame frame;
        if (decode_next(frame, 0, 0)) {
            std::lock_guard<std::mutex> lk(queue_mutex_);
            ready_queue_.push_back(std::move(frame));
            queue_cv_.notify_one();
        }
    }
}

void VideoSource::pump_uploads() {
    // Задаем темп заливки на GPU по нативному FPS источника. Без этого
    // ограничения render-цикл вычерпывал бы очередь декодированных кадров
    // со своей скоростью (~60 Гц), проигрывая 30-fps видео вдвое быстрее.
    // Поток декодера сам блокируется, когда очередь заполнена, так что
    // CPU/RAM остаются в разумных пределах.
    const double now      = glfwGetTime();
    const double interval = 1.0 / std::max(1.0, src_fps_);
    if (last_upload_time_ == 0.0) last_upload_time_ = now;
    double behind = now - last_upload_time_;
    int budget = (int)(behind / interval);
    if (budget <= 0) return;
    if (budget > 3) budget = 3;   // ограничиваем догон, чтобы не было рывка кадров
    last_upload_time_ += budget * interval;

    std::unique_lock<std::mutex> lk(queue_mutex_);
    int uploaded = 0;
    while (!ready_queue_.empty() && uploaded < budget) {
        DecodedFrame& f = ready_queue_.front();

        // Подстраховка: не отдаем в glTexImage2D нулевые/отрицательные размеры
        // или буфер меньше ожидаемого w*h*3. Битый DecodedFrame здесь может
        // уронить GL-драйвер на некоторых видеокартах.
        if (f.width <= 0 || f.height <= 0 ||
            f.pixels.size() < (size_t)f.width * f.height * 3) {
            fprintf(stderr, "[video] skipping bad frame: %dx%d buf=%zu\n",
                    f.width, f.height, f.pixels.size());
            ready_queue_.pop_front();
            continue;
        }

        GLuint tex = tex_pool_[tex_next_];
        glBindTexture(GL_TEXTURE_2D, tex);
        // Строки RGB произвольной ширины не обязаны быть выровнены по 4 байта - сообщаем GL.
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        // После того как слот один раз заполнен кадром, последующие заливки
        // того же размера идут через glTexSubImage2D - он просто перезаписывает
        // пиксели в уже выделенном хранилище. glTexImage2D при каждом вызове
        // заново выделяет память на GPU, фрагментируя ее в драйвере и подсаживая
        // render-цикл на слабом железе.
        if (tex_w_[tex_next_] == f.width && tex_h_[tex_next_] == f.height) {
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0,
                            f.width, f.height, GL_RGB, GL_UNSIGNED_BYTE,
                            f.pixels.data());
        } else {
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, f.width, f.height, 0,
                         GL_RGB, GL_UNSIGNED_BYTE, f.pixels.data());
            tex_w_[tex_next_] = f.width;
            tex_h_[tex_next_] = f.height;
        }
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        if (tex_ready_count_ == 0) {
            fprintf(stderr, "[video] first GPU upload: %dx%d (path=%s)\n",
                    f.width, f.height, path_.c_str());
        }
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
    // Показываем самый свежий залитый слот. pump_uploads сама ограничена
    // темпом src_fps_, так что воспроизведение идет на нативной скорости
    // независимо от FPS рендера. Индексация seq_idx_ по темпу рендера
    // давала двойную скорость - 12-слотовый пул прокручивался быстрее,
    // чем успевали появляться новые кадры.
    int idx = (tex_next_ - 1 + kTexPoolSize) % kTexPoolSize;
    if (out_w) *out_w = tex_w_[idx];
    if (out_h) *out_h = tex_h_[idx];
    return tex_pool_[idx];
}
