#include "audio_analyzer.h"
#include <cmath>
#include <cstring>
#include <algorithm>
#include <numeric>

AudioAnalyzer::AudioAnalyzer() {
    Pa_Initialize();
    fft_in_  = fftwf_alloc_real(kChunkSize);
    fft_out_ = fftwf_alloc_complex(kChunkSize / 2 + 1);
    fft_plan_ = fftwf_plan_dft_r2c_1d(kChunkSize, fft_in_, fft_out_, FFTW_MEASURE);
}

AudioAnalyzer::~AudioAnalyzer() {
    stop();
    fftwf_destroy_plan(fft_plan_);
    fftwf_free(fft_in_);
    fftwf_free(fft_out_);
    Pa_Terminate();
}

std::vector<AudioDevice> AudioAnalyzer::enumerate_devices() {
    std::vector<AudioDevice> result;
    int count = Pa_GetDeviceCount();
    for (int i = 0; i < count; ++i) {
        const PaDeviceInfo* info = Pa_GetDeviceInfo(i);
        if (!info || info->maxInputChannels < 1) continue;
        AudioDevice d;
        d.index = i;
        d.name  = info->name ? info->name : "(unknown)";
        // WASAPI loopback devices have "loopback" in name or hostApiType == paWASAPI
        const PaHostApiInfo* ha = Pa_GetHostApiInfo(info->hostApi);
        if (ha && ha->type == paWASAPI) {
            d.is_loopback = (d.name.find("loopback") != std::string::npos ||
                             d.name.find("Loopback") != std::string::npos);
        }
        result.push_back(d);
    }
    return result;
}

bool AudioAnalyzer::start(int device_index) {
    stop();

    const PaDeviceInfo* dev = Pa_GetDeviceInfo(device_index);
    if (!dev) return false;

    PaStreamParameters params{};
    params.device                    = device_index;
    params.channelCount              = 1;
    params.sampleFormat              = paFloat32;
    params.suggestedLatency          = dev->defaultLowInputLatency;
    params.hostApiSpecificStreamInfo = nullptr;

    PaError err = Pa_OpenStream(
        &stream_,
        &params,
        nullptr,
        kSampleRate,
        kChunkSize,
        paClipOff,
        &AudioAnalyzer::pa_callback,
        this
    );
    if (err != paNoError) return false;

    // Reset state
    rms_smooth_        = 0.f;
    rms_mean_          = 0.f;
    flat_mean_         = 0.f;
    calibration_count_ = 0;
    calibrated_        = false;
    noise_floor_       = 0.005f;
    gate_.store(0.005f);
    beat_last_time_ms_ = 0.f;
    elapsed_ms_        = 0.f;
    rms_hist_idx_      = 0;
    rms_hist_count_    = 0;
    std::fill(std::begin(rms_history_), std::end(rms_history_), 0.f);

    err = Pa_StartStream(stream_);
    if (err != paNoError) { Pa_CloseStream(stream_); stream_ = nullptr; return false; }
    running_.store(true);
    return true;
}

void AudioAnalyzer::stop() {
    if (stream_) {
        Pa_StopStream(stream_);
        Pa_CloseStream(stream_);
        stream_ = nullptr;
    }
    running_.store(false);
}

int AudioAnalyzer::pa_callback(const void* input, void* /*output*/,
                               unsigned long frames,
                               const PaStreamCallbackTimeInfo*,
                               PaStreamCallbackFlags,
                               void* user_data) {
    auto* self = static_cast<AudioAnalyzer*>(user_data);
    self->process_chunk(static_cast<const float*>(input), frames);
    return paContinue;
}

static float compute_rms(const float* buf, int n) {
    float sum = 0.f;
    for (int i = 0; i < n; ++i) sum += buf[i] * buf[i];
    return std::sqrt(sum / n);
}

static float linear_slope(const float* y, int n) {
    // Simple least-squares slope over n evenly-spaced points
    if (n < 2) return 0.f;
    float mx = (n - 1) * 0.5f;
    float sx2 = 0.f, sxy = 0.f;
    for (int i = 0; i < n; ++i) {
        float dx = i - mx;
        sx2 += dx * dx;
        sxy += dx * y[i];
    }
    return (sx2 > 1e-12f) ? sxy / sx2 : 0.f;
}

void AudioAnalyzer::process_chunk(const float* samples, unsigned long n) {
    if (n == 0) return;
    const float chunk_ms = (float)n / kSampleRate * 1000.f;
    elapsed_ms_ += chunk_ms;

    // ── RMS ──────────────────────────────────────────────────────────────────
    float raw_rms = compute_rms(samples, (int)n);
    rms_smooth_ = 0.7f * rms_smooth_ + 0.3f * raw_rms;

    // ── Calibration (noise floor) ─────────────────────────────────────────────
    if (!calibrated_) {
        cal_buf_[cal_idx_++ % kCalibChunks] = raw_rms;
        calibration_count_++;
        if (calibration_count_ >= kCalibChunks) {
            float mean_cal = 0.f;
            for (float v : cal_buf_) mean_cal += v;
            mean_cal /= kCalibChunks;
            noise_floor_ = std::max(mean_cal * 4.f, 0.005f);
            gate_.store(noise_floor_ * threshold_scale_.load());
            calibrated_ = true;
        }
        // Update gate while calibrating
        gate_.store(0.005f * threshold_scale_.load());
    } else {
        gate_.store(noise_floor_ * threshold_scale_.load());
    }

    // ── FFT ───────────────────────────────────────────────────────────────────
    std::memcpy(fft_in_, samples, std::min((unsigned long)kChunkSize, n) * sizeof(float));
    if (n < kChunkSize)
        std::fill(fft_in_ + n, fft_in_ + kChunkSize, 0.f);
    fftwf_execute(fft_plan_);

    // Bin resolution: sr / N Hz/bin
    float bin_hz = (float)kSampleRate / kChunkSize;
    int bass_lo  = (int)(20.f  / bin_hz), bass_hi  = (int)(300.f  / bin_hz);
    int mid_lo   = (int)(300.f / bin_hz), mid_hi   = (int)(3000.f / bin_hz);
    int treb_lo  = (int)(3000.f/ bin_hz), treb_hi  = (int)(16000.f/ bin_hz);
    int max_bin  = kChunkSize / 2;

    auto band_energy = [&](int lo, int hi) {
        float e = 0.f;
        for (int b = std::max(lo, 0); b <= std::min(hi, max_bin); ++b)
            e += fft_out_[b][0]*fft_out_[b][0] + fft_out_[b][1]*fft_out_[b][1];
        return std::sqrt(e);
    };

    float bass   = band_energy(bass_lo, bass_hi);
    float mid    = band_energy(mid_lo,  mid_hi);
    float treble = band_energy(treb_lo, treb_hi);

    // ── Spectral flatness ─────────────────────────────────────────────────────
    float geo_sum = 0.f, arith_sum = 0.f;
    int   nbins = 0;
    for (int b = 1; b <= max_bin; ++b) {
        float mag = std::sqrt(fft_out_[b][0]*fft_out_[b][0] + fft_out_[b][1]*fft_out_[b][1]) + 1e-9f;
        geo_sum   += std::log(mag);
        arith_sum += mag;
        nbins++;
    }
    float flatness = 0.f;
    if (nbins > 0 && arith_sum > 1e-9f) {
        float geo  = std::exp(geo_sum / nbins);
        float arith = arith_sum / nbins;
        flatness = geo / arith;
    }
    flat_mean_ = 0.9f * flat_mean_ + 0.1f * flatness;

    // ── RMS mean + trend ──────────────────────────────────────────────────────
    rms_history_[rms_hist_idx_] = rms_smooth_;
    rms_hist_idx_ = (rms_hist_idx_ + 1) % kTrendWindow;
    if (rms_hist_count_ < kTrendWindow) rms_hist_count_++;

    // Re-order history into chronological order
    float ordered[kTrendWindow];
    int   start = (rms_hist_count_ < kTrendWindow) ? 0 : rms_hist_idx_;
    for (int i = 0; i < rms_hist_count_; ++i)
        ordered[i] = rms_history_[(start + i) % kTrendWindow];
    float slope = linear_slope(ordered, rms_hist_count_);

    if (rms_mean_ < 1e-9f) rms_mean_ = rms_smooth_;
    else                   rms_mean_ = 0.99f * rms_mean_ + 0.01f * rms_smooth_;

    // ── Beat detection ────────────────────────────────────────────────────────
    float ref = (rms_mean_ > 1e-9f) ? rms_mean_ : 1e-9f;
    bool beat = false;
    float cooldown_elapsed = elapsed_ms_ - beat_last_time_ms_;
    if (cooldown_elapsed >= kBeatCooldownMs) {
        if (rms_smooth_ / ref > 1.3f) {
            beat = true;
            beat_last_time_ms_ = elapsed_ms_;
        }
    }

    // ── Noisy flag ───────────────────────────────────────────────────────────
    bool is_noisy = (flatness > flat_mean_ * 1.5f) &&
                    (rms_smooth_ > gate_.load());

    // ── Publish ───────────────────────────────────────────────────────────────
    AudioStats s;
    s.rms         = rms_smooth_;
    s.rms_mean    = rms_mean_;
    s.bass        = bass;
    s.mid         = mid;
    s.treble      = treble;
    s.flatness    = flatness;
    s.trend_slope = slope;
    s.beat        = beat;
    s.is_noisy    = is_noisy;
    atomic_stats_.write(s);
}

float AudioAnalyzer::get_rms_mean() const {
    return atomic_stats_.read().rms_mean;
}
