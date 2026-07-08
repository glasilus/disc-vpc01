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

    // Вызывать из render-потока каждый кадр, чтобы прокачать загрузки на GPU
    void pump_uploads();

    GLuint get_random_frame(int w, int h, int* out_w = nullptr, int* out_h = nullptr);
    GLuint get_sequential_frame(int w, int h, int* out_w = nullptr, int* out_h = nullptr);
    GLuint get_cut_frame(bool trigger_cut, int w, int h, int* out_w = nullptr, int* out_h = nullptr);

    // Фокус на одном клипе в стиле VJ. Пока active_idx_ в диапазоне
    // [0, size()-1], каждый запрос кадра - случайный или последовательный,
    // в режиме cut или без него - обслуживается только этим источником.
    // -1 возвращает обычное поведение пула (round-robin / случайный выбор
    // среди всех источников). Клэмпить индекс должен вызывающий код;
    // выход за диапазон здесь просто сбрасывает фокус в -1.
    void set_active(int idx);
    int  active() const { return active_idx_; }

private:
    std::vector<std::unique_ptr<VideoSource>> sources_;
    std::vector<std::string>                  paths_;
    int                                       round_robin_ = 0;
    int                                       active_idx_  = -1;
    int                                       cut_source_idx_ = 0;
    // Снимок счетчика циклов текущего round-robin источника. Как только живой
    // счетчик его превысит, значит источник доиграл до конца, и пул
    // переключается на следующий - так без фокуса получаем "сначала полностью
    // A, потом B, потом C", а не смену источника на каждом render-кадре.
    int                                       rr_loop_baseline_ = 0;
};
