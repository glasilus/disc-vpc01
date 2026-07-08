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

// Размер пула = максимум декодированных кадров в полете на один источник.
// 12 ≈ 0.4 сек при 30 fps - с запасом для случайного выбора кадра при cut.
// Больше смысла не имеет: только жрет VRAM и RAM, а разнообразие cut'ов
// заметно не растет (соседние кадры одного плана выглядят почти одинаково).
static constexpr int kTexPoolSize = 12;

// Один декодированный кадр в CPU-памяти (RGB24, готов к заливке на GPU)
struct DecodedFrame {
    std::vector<uint8_t> pixels;
    int width  = 0;
    int height = 0;
};

// Декодирует один видеофайл и держит пул GL-текстур для него
class VideoSource {
public:
    explicit VideoSource(const std::string& path);
    ~VideoSource();

    bool is_open() const { return open_; }
    const std::string& path() const { return path_; }

    // Возвращает ID GL-текстуры с декодированным кадром в НАТИВНОМ разрешении
    // (вызывающий код не должен ее удалять). Если out_w/out_h не null, туда
    // пишутся размеры текстуры - они нужны шейдеру размещения на канвасе для
    // корректного аспекта. Аргументы target_w/target_h оставлены для
    // совместимости сигнатуры, но игнорируются.
    GLuint get_random_frame(int target_w, int target_h, int* out_w = nullptr, int* out_h = nullptr);
    GLuint get_sequential_frame(int target_w, int target_h, int* out_w = nullptr, int* out_h = nullptr);

    int native_width()  const { return src_w_; }
    int native_height() const { return src_h_; }

    // Вызывать раз в кадр из render-потока, чтобы прокачать отложенные загрузки на GPU
    void pump_uploads();

private:
    void decode_thread_fn();
    bool open_decoder();
    void close_decoder();
    bool decode_next(DecodedFrame& out, int w, int h);
    void seek_random();

    std::string path_;
    bool        open_ = false;

    // Хендлы FFmpeg
    AVFormatContext* fmt_ctx_   = nullptr;
    AVCodecContext*  codec_ctx_ = nullptr;
    SwsContext*      sws_ctx_   = nullptr;
    AVFrame*         av_frame_  = nullptr;
    AVFrame*         rgb_frame_ = nullptr;
    int              video_stream_idx_ = -1;
    int64_t          duration_ts_      = 0;
    int              src_w_ = 0, src_h_ = 0;   // нативные размеры видео
    int              dec_w_ = 0, dec_h_ = 0;   // размеры декодирования (с ограничением сверху)
    double           src_fps_ = 30.0;          // нативный fps (задает темп заливки на GPU)
    double           last_upload_time_ = 0.0;  // glfwGetTime() последней заливки на GPU
public:
    // Счетчик циклов: растет каждый раз, когда декодер прыгает обратно в начало.
    // VideoPool читает его, чтобы продвигать round-robin между источниками.
    int              loop_count() const { return loop_count_; }
    double           native_fps()  const { return src_fps_; }

    // Просит поток декодера прыгнуть на случайную позицию. Возвращается
    // сразу же (неблокирующий вызов). Пул использует это на cut-событиях,
    // чтобы каждый cut попадал в другую часть видео - визуально отклик
    // укладывается в 1 render-кадр, потому что get_random_frame до завершения
    // seek все еще отдает закэшированную текстуру.
    void             request_seek_random() { seek_request_.store(true); queue_cv_.notify_all(); }
private:
    std::atomic<int>  loop_count_{0};
    std::atomic<bool> seek_request_{false};

    // Пул GL-текстур - размеры хранятся отдельно на каждый слот, потому что
    // декодирование идет в нативном разрешении (оно может отличаться между
    // кадрами, если когда-нибудь начнем кэшировать кадры разного размера).
    GLuint tex_pool_[kTexPoolSize] = {};
    int    tex_w_[kTexPoolSize] = {};
    int    tex_h_[kTexPoolSize] = {};
    int    tex_next_        = 0;
    int    tex_ready_count_ = 0;
    int    seq_idx_         = 0;

    // Фоновый поток декодирования
    std::thread              decode_thread_;
    std::atomic<bool>        stop_thread_{false};
    std::mutex               queue_mutex_;
    std::condition_variable  queue_cv_;
    std::deque<DecodedFrame> ready_queue_;  // CPU-кадры, ждущие заливки на GPU
    int target_w_ = 1280, target_h_ = 720;
};
