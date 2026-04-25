#pragma once
#include "audio_stats.h"
#include <portaudio.h>
#include <fftw3.h>
#include <vector>
#include <string>
#include <atomic>

static constexpr int   kChunkSize   = 256;
static constexpr int   kSampleRateDefault = 48000;  // request; actual stored per-stream
static constexpr int   kCalibChunks = 256;   // ~1.5s calibration
static constexpr int   kTrendWindow = 10;
static constexpr float kBeatCooldownMs = 80.f;

struct AudioDevice {
    int         index = -1;
    std::string name;          // UTF-8, may include "[API]" prefix
    std::string host_api;      // "WASAPI" / "MME" / "CoreAudio" / "ALSA" ...
    int         host_api_type = 0;  // PaHostApiTypeId value
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

    // Default WASAPI (or platform-appropriate) input device index, or -1.
    int    default_input_device();
    int    sample_rate() const { return sample_rate_; }
    // Number of audio callbacks since stream started — should grow rapidly
    // while the stream runs. If it stays at 0 the device is open but the
    // OS isn't delivering samples (typical for misconfigured WASAPI loopback).
    uint32_t callback_count() const { return callback_count_.load(); }

    // Read latest stats (lock-free, safe from render thread)
    AudioStats get_stats() const { return atomic_stats_.read(); }

    // Gate threshold — can be adjusted from GUI
    void  set_threshold_scale(float s) { threshold_scale_.store(s); }
    float get_gate()   const { return gate_.load(); }
    float get_rms_mean() const;

private:
    static int pa_callback(const void* input, void* output,
                           unsigned long frames,
                           const PaStreamCallbackTimeInfo* time_info,
                           PaStreamCallbackFlags flags,
                           void* user_data);

    void process_chunk(const float* samples, unsigned long n);
    void calibrate(float rms);

    PaStream*             stream_    = nullptr;
    std::atomic<bool>     running_   = false;

    // FFTW
    float*        fft_in_  = nullptr;
    fftwf_complex* fft_out_ = nullptr;
    fftwf_plan    fft_plan_ = nullptr;

    // Audio stats (written in callback, read in render thread)
    mutable AtomicAudioStats atomic_stats_;

    // Internal state (only written in audio callback — no lock needed)
    float rms_smooth_   = 0.f;
    float rms_mean_     = 0.f;
    float flat_mean_    = 0.f;
    int   calibration_count_ = 0;
    bool  calibrated_   = false;
    float noise_floor_  = 0.005f;
    std::atomic<float> gate_{0.005f};
    std::atomic<float> threshold_scale_{1.0f};

    // Beat detection
    float   beat_last_time_ms_ = 0.f;
    float   elapsed_ms_        = 0.f;

    // Trend slope
    float   rms_history_[kTrendWindow] = {};
    int     rms_hist_idx_              = 0;
    int     rms_hist_count_            = 0;

    // Actual sample rate granted by PortAudio (may differ from requested).
    int     sample_rate_   = kSampleRateDefault;
    int     channel_count_ = 1;
    std::atomic<uint32_t> callback_count_{0};

    // Calibration buffer
    float   cal_buf_[kCalibChunks] = {};
    int     cal_idx_               = 0;
};
