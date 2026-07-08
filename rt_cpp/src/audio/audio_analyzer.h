#pragma once
#include "audio_stats.h"
#include <portaudio.h>
#include <fftw3.h>
#include <vector>
#include <string>
#include <atomic>

// Запрашиваемый размер буфера в audio-callback. Маленький ради низкой задержки;
// сам callback спокойно принимает ЛЮБОЕ число фреймов, которое реально отдаёт
// драйвер (WASAPI shared mode обычно шлёт ~448-480 фреймов независимо от запроса).
static constexpr int   kChunkSize   = 256;
static constexpr int   kSampleRateDefault = 48000;  // запрос; реальный sample rate хранится отдельно на поток

// FFT НЕ привязан к размеру буфера callback'а: входящие сэмплы копятся в
// скользящее окно kFftSize, анализ запускается каждые kHopSize новых сэмплов.
// Так решены две проблемы старого варианта ("FFT прямо по 256-сэмпловому callback'у"):
//   - разрешение по басам: 1024 @ 48k => ~47 Гц/bin (было ~187 Гц/bin, бас укладывался в 1 bin)
//   - нет потери сэмплов: стерео-callback полностью даунмиксится, а не обрезается.
static constexpr int   kFftSize     = 1024;
static constexpr int   kHopSize     = 256;   // анализ каждые 256 новых сэмплов
static constexpr int   kCalibChunks = 256;   // ~1.4с кадров анализа для калибровки
static constexpr int   kTrendWindow = 10;
static constexpr float kBeatCooldownMs = 80.f;

struct AudioDevice {
    int         index = -1;
    std::string name;          // UTF-8, может содержать префикс "[API]"
    std::string host_api;      // "WASAPI" / "MME" / "CoreAudio" / "ALSA" ...
    int         host_api_type = 0;  // значение PaHostApiTypeId
    bool        is_loopback = false;
};

class AudioAnalyzer {
public:
    AudioAnalyzer();
    ~AudioAnalyzer();

    std::vector<AudioDevice> enumerate_devices();
    bool   start(int device_index);
    void   stop();
    bool   is_running() const { return running_.load(); }

    // Индекс дефолтного input-устройства WASAPI (или подходящего для платформы), либо -1.
    int    default_input_device();
    int    sample_rate() const { return sample_rate_; }
    // Счётчик audio-callback'ов с момента старта потока - должен быстро расти,
    // пока поток жив. Если стоит на 0, устройство открыто, но ОС не отдаёт
    // сэмплы (типичный симптом неправильно настроенного WASAPI loopback).
    uint32_t callback_count() const { return callback_count_.load(); }

    // Чтение последней статистики (lock-free, безопасно из render-потока)
    AudioStats get_stats() const { return atomic_stats_.read(); }

    // Порог гейта - можно менять из GUI
    void  set_threshold_scale(float s) { threshold_scale_.store(s); }
    float get_gate()   const { return gate_.load(); }
    float get_rms_mean() const;

private:
    static int pa_callback(const void* input, void* output,
                           unsigned long frames,
                           const PaStreamCallbackTimeInfo* time_info,
                           PaStreamCallbackFlags flags,
                           void* user_data);

    // Принимает моно-сэмплы произвольной длины из callback'а и кладёт их в
    // скользящее окно; запускает analyze_window() на каждый завершённый hop.
    void ingest(const float* mono, unsigned long n);
    void analyze_window();

    PaStream*             stream_    = nullptr;
    std::atomic<bool>     running_   = false;

    // FFTW (размер kFftSize)
    float*        fft_in_  = nullptr;
    fftwf_complex* fft_out_ = nullptr;
    fftwf_plan    fft_plan_ = nullptr;

    // Скользящее окно анализа (используется только в audio-потоке).
    float   win_ring_[kFftSize] = {};   // кольцевой буфер последних kFftSize сэмплов
    int     ring_pos_           = 0;    // следующая позиция записи в кольце
    int     samples_since_hop_  = 0;    // новых сэмплов с последнего analyze_window()
    bool    ring_primed_        = false;// true после того, как накопилось kFftSize сэмплов
    float   hann_[kFftSize]     = {};   // предвычисленное окно Ханна

    // Состояние детектора битов по spectral flux.
    float   prev_mag_[kFftSize/2 + 1] = {};
    float   flux_mean_ = 0.f;           // база для адаптивного порога
    float   flux_std_  = 0.f;

    // Поканальный AGC для нормализованного спектра визуализатора (0..1).
    float   bin_max_[kVizBins] = {};
    float   level_max_ = 1e-4f;

    // Статистика аудио (пишется в callback, читается в render-потоке)
    mutable AtomicAudioStats atomic_stats_;

    // Внутреннее состояние (пишется только в audio callback - лок не нужен)
    float rms_smooth_   = 0.f;
    float rms_mean_     = 0.f;
    float flat_mean_    = 0.f;
    int   calibration_count_ = 0;
    bool  calibrated_   = false;
    float noise_floor_  = 0.005f;
    std::atomic<float> gate_{0.005f};
    std::atomic<float> threshold_scale_{1.0f};

    // Детекция битов
    float   beat_last_time_ms_ = 0.f;
    float   elapsed_ms_        = 0.f;

    // Тренд (наклон)
    float   rms_history_[kTrendWindow] = {};
    int     rms_hist_idx_              = 0;
    int     rms_hist_count_            = 0;

    // Реальный sample rate, выданный PortAudio (может отличаться от запрошенного).
    int     sample_rate_   = kSampleRateDefault;
    int     channel_count_ = 1;
    std::atomic<uint32_t> callback_count_{0};

    // Буфер калибровки
    float   cal_buf_[kCalibChunks] = {};
    int     cal_idx_               = 0;
};
