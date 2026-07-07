#pragma once
#include "audio_stats.h"
#include <portaudio.h>
#include <fftw3.h>
#include <vector>
#include <string>
#include <atomic>

// Audio-callback buffer request. Kept small for low latency; the callback
// tolerates ANY frame count the driver actually delivers (WASAPI shared mode
// commonly hands over ~448-480 frames regardless of this request).
static constexpr int   kChunkSize   = 256;
static constexpr int   kSampleRateDefault = 48000;  // request; actual stored per-stream

// FFT is DECOUPLED from the callback buffer: incoming samples feed a sliding
// window of kFftSize and analysis runs every kHopSize new samples. This fixes
// two things vs. the old "FFT the raw 256-sample callback" design:
//   • bass resolution: 1024 @ 48k ⇒ ~47 Hz/bin (was ~187 Hz/bin - bass was 1 bin)
//   • no sample loss: stereo callbacks are fully downmixed, not truncated.
static constexpr int   kFftSize     = 1024;
static constexpr int   kHopSize     = 256;   // analyze every 256 new samples
static constexpr int   kCalibChunks = 256;   // ~1.4s of analysis frames
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
    // Number of audio callbacks since stream started - should grow rapidly
    // while the stream runs. If it stays at 0 the device is open but the
    // OS isn't delivering samples (typical for misconfigured WASAPI loopback).
    uint32_t callback_count() const { return callback_count_.load(); }

    // Read latest stats (lock-free, safe from render thread)
    AudioStats get_stats() const { return atomic_stats_.read(); }

    // Gate threshold - can be adjusted from GUI
    void  set_threshold_scale(float s) { threshold_scale_.store(s); }
    float get_gate()   const { return gate_.load(); }
    float get_rms_mean() const;

private:
    static int pa_callback(const void* input, void* output,
                           unsigned long frames,
                           const PaStreamCallbackTimeInfo* time_info,
                           PaStreamCallbackFlags flags,
                           void* user_data);

    // Feed arbitrary-length mono samples from the callback into the sliding
    // window; runs analyze_window() once per completed hop.
    void ingest(const float* mono, unsigned long n);
    void analyze_window();

    PaStream*             stream_    = nullptr;
    std::atomic<bool>     running_   = false;

    // FFTW (sized kFftSize)
    float*        fft_in_  = nullptr;
    fftwf_complex* fft_out_ = nullptr;
    fftwf_plan    fft_plan_ = nullptr;

    // Sliding analysis window (audio-thread only).
    float   win_ring_[kFftSize] = {};   // ring holding the last kFftSize samples
    int     ring_pos_           = 0;    // next write position in the ring
    int     samples_since_hop_  = 0;    // new samples since last analyze_window()
    bool    ring_primed_        = false;// true once kFftSize samples seen
    float   hann_[kFftSize]     = {};   // precomputed Hann window

    // Spectral-flux onset/beat detection state.
    float   prev_mag_[kFftSize/2 + 1] = {};
    float   flux_mean_ = 0.f;           // adaptive threshold baseline
    float   flux_std_  = 0.f;

    // Per-band AGC for normalized visualizer spectrum (0..1).
    float   bin_max_[kVizBins] = {};
    float   level_max_ = 1e-4f;

    // Audio stats (written in callback, read in render thread)
    mutable AtomicAudioStats atomic_stats_;

    // Internal state (only written in audio callback - no lock needed)
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
