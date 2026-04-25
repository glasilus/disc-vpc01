#include "audio_analyzer.h"
#include <cmath>
#include <cstring>
#include <algorithm>
#include <numeric>
#include <unordered_map>
#include <cctype>

#if defined(_WIN32)
#  define NOMINMAX
#  include <windows.h>
#endif

// Device names coming from PortAudio MME/DirectSound on Windows are in the
// current ANSI code page. WASAPI & WDM-KS are already UTF-8. Convert via
// CP_ACP → UTF-8 when the string contains high bytes that aren't valid UTF-8.
static std::string to_utf8(const char* raw) {
    if (!raw) return "";
#if defined(_WIN32)
    // Detect whether the string is already valid UTF-8; if so, keep as is.
    int len = (int)std::strlen(raw);
    BOOL invalid = FALSE;
    int wlen = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, raw, len, nullptr, 0);
    if (wlen > 0 && !invalid) return std::string(raw, len); // valid UTF-8
    // Otherwise decode as system ANSI → wide → UTF-8
    wlen = MultiByteToWideChar(CP_ACP, 0, raw, len, nullptr, 0);
    if (wlen <= 0) return std::string(raw, len);
    std::wstring w(wlen, L'\0');
    MultiByteToWideChar(CP_ACP, 0, raw, len, w.data(), wlen);
    int ulen = WideCharToMultiByte(CP_UTF8, 0, w.data(), wlen, nullptr, 0, nullptr, nullptr);
    std::string u(ulen, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), wlen, u.data(), ulen, nullptr, nullptr);
    return u;
#else
    return std::string(raw);
#endif
}

// Priority of host APIs for dedup — higher value wins when names collide.
// Lower latency + better stability → higher score.
static int host_api_priority(PaHostApiTypeId t) {
    switch (t) {
        case paWASAPI:          return 100;
        case paASIO:            return  95;
        case paCoreAudio:       return  90;
        case paJACK:            return  85;
        case paALSA:            return  80;
        case paWDMKS:           return  60;
        case paDirectSound:     return  40;
        case paMME:             return  20;
        default:                return   0;
    }
}

static std::string lower_ascii(const std::string& s) {
    std::string o; o.reserve(s.size());
    for (char c : s) o.push_back((char)std::tolower((unsigned char)c));
    return o;
}

AudioAnalyzer::AudioAnalyzer() {
    PaError e = Pa_Initialize();
    if (e != paNoError) {
        fprintf(stderr, "[audio] Pa_Initialize failed: %s\n", Pa_GetErrorText(e));
    } else {
        fprintf(stderr, "[audio] Pa_Initialize OK (%s)\n", Pa_GetVersionText());
        // Dump every device PortAudio sees, including non-input ones, so
        // diagnostic logs reveal exactly what the user has available.
        int total = Pa_GetDeviceCount();
        fprintf(stderr, "[audio] %d devices total:\n", total);
        for (int i = 0; i < total; ++i) {
            const PaDeviceInfo* info = Pa_GetDeviceInfo(i);
            if (!info) continue;
            const PaHostApiInfo* ha = Pa_GetHostApiInfo(info->hostApi);
            fprintf(stderr, "  #%-3d [%s] in=%d out=%d sr=%.0f \"%s\"\n",
                    i, ha ? ha->name : "?",
                    info->maxInputChannels, info->maxOutputChannels,
                    info->defaultSampleRate, info->name ? info->name : "?");
        }
    }
    fft_in_  = fftwf_alloc_real(kChunkSize);
    fft_out_ = fftwf_alloc_complex(kChunkSize / 2 + 1);
    fft_plan_ = fftwf_plan_dft_r2c_1d(kChunkSize, fft_in_, fft_out_, FFTW_MEASURE);
}

int AudioAnalyzer::default_input_device() {
    // Prefer WASAPI's default input device on Windows; otherwise fall back
    // to PortAudio's global default. Returns -1 if nothing is available.
    int count = Pa_GetHostApiCount();
    for (int h = 0; h < count; ++h) {
        const PaHostApiInfo* ha = Pa_GetHostApiInfo(h);
        if (!ha) continue;
        if (ha->type == paWASAPI || ha->type == paCoreAudio ||
            ha->type == paJACK   || ha->type == paALSA) {
            if (ha->defaultInputDevice >= 0) return ha->defaultInputDevice;
        }
    }
    int def = Pa_GetDefaultInputDevice();
    return (def == paNoDevice) ? -1 : def;
}

AudioAnalyzer::~AudioAnalyzer() {
    stop();
    fftwf_destroy_plan(fft_plan_);
    fftwf_free(fft_in_);
    fftwf_free(fft_out_);
    Pa_Terminate();
}

std::vector<AudioDevice> AudioAnalyzer::enumerate_devices() {
    // 1) Collect every input-capable device with UTF-8 name + API info.
    std::vector<AudioDevice> all;
    int count = Pa_GetDeviceCount();
    for (int i = 0; i < count; ++i) {
        const PaDeviceInfo* info = Pa_GetDeviceInfo(i);
        if (!info) continue;
        // WASAPI loopback counts as an output device in PortAudio but reports
        // input channels, so we keep any device with inputs available.
        if (info->maxInputChannels < 1) continue;

        AudioDevice d;
        d.index        = i;
        d.name         = to_utf8(info->name ? info->name : "(unknown)");
        const PaHostApiInfo* ha = Pa_GetHostApiInfo(info->hostApi);
        d.host_api     = ha ? to_utf8(ha->name) : "";
        d.host_api_type = ha ? (int)ha->type : 0;

        std::string lname = lower_ascii(d.name);
        d.is_loopback =
            (d.host_api_type == paWASAPI) &&
            (lname.find("loopback") != std::string::npos);

        all.push_back(std::move(d));
    }

    // 2) Dedup: group by base name (strip " [Loopback]" suffix, lowercase),
    //    keep the highest-priority host API for each group.
    struct Best { int idx; int prio; };
    std::unordered_map<std::string, Best> best;
    for (int i = 0; i < (int)all.size(); ++i) {
        const auto& d = all[i];
        std::string key = lower_ascii(d.name);
        // Strip the "(loopback)" / "[loopback]" annotations so that a mic
        // called "Foo" and "Foo (loopback)" aren't merged, but DirectSound
        // "Foo" and MME "Foo" are.
        if (d.is_loopback) key += "#lb";
        int prio = host_api_priority((PaHostApiTypeId)d.host_api_type);
        auto it = best.find(key);
        if (it == best.end() || prio > it->second.prio) {
            best[key] = {i, prio};
        }
    }

    // 3) Build final list, annotating the host API so duplicates across
    //    truly different physical devices remain distinguishable.
    std::vector<AudioDevice> result;
    result.reserve(best.size());
    for (auto& kv : best) {
        AudioDevice d = all[kv.second.idx];
        if (!d.host_api.empty())
            d.name = "[" + d.host_api + "] " + d.name;
        result.push_back(std::move(d));
    }

    // 4) Stable ordering: loopbacks last, then alphabetical.
    std::sort(result.begin(), result.end(), [](const AudioDevice& a, const AudioDevice& b){
        if (a.is_loopback != b.is_loopback) return !a.is_loopback;
        return a.name < b.name;
    });
    return result;
}

bool AudioAnalyzer::start(int device_index) {
    stop();

    // Auto-pick a sensible default when the GUI hasn't selected anything yet.
    if (device_index < 0) {
        device_index = default_input_device();
        if (device_index < 0) {
            fprintf(stderr, "[audio] no input device available\n");
            return false;
        }
        fprintf(stderr, "[audio] auto-selected default input device #%d\n", device_index);
    }

    const PaDeviceInfo* dev = Pa_GetDeviceInfo(device_index);
    if (!dev) {
        fprintf(stderr, "[audio] invalid device index %d\n", device_index);
        return false;
    }
    const PaHostApiInfo* ha = Pa_GetHostApiInfo(dev->hostApi);
    fprintf(stderr, "[audio] starting device #%d \"%s\" via %s "
                    "(defaultSR=%.0f maxIn=%d)\n",
            device_index, dev->name ? dev->name : "?",
            ha ? ha->name : "?", dev->defaultSampleRate, dev->maxInputChannels);

    // Try mono first; if the device refuses, fall back to the device's native
    // channel count (we mix to mono in the callback).
    int channels = 1;
    if (dev->maxInputChannels < 1) {
        fprintf(stderr, "[audio] device has no input channels\n");
        return false;
    }
    if (dev->maxInputChannels > 1) channels = std::min(dev->maxInputChannels, 2);

    // Use the device's native sample rate when available. WASAPI shared mode
    // almost always runs at 48 kHz on modern Windows and will reject a 44.1
    // kHz request outright.
    double requested_sr = (dev->defaultSampleRate >= 16000.0 &&
                           dev->defaultSampleRate <= 96000.0)
                        ? dev->defaultSampleRate : (double)kSampleRateDefault;

    // Try pairs of (channel_count, frames_per_buffer, latency) until one
    // works. Pa_IsFormatSupported is unreliable on WASAPI (returns
    // \"supported\" for things that fail at OpenStream time and vice versa),
    // so we just try OpenStream directly. paFramesPerBufferUnspecified
    // (= 0) lets PortAudio pick a buffer size compatible with the driver,
    // which some WASAPI drivers REQUIRE — they reject any fixed buffer
    // size we'd choose ourselves.
    struct Attempt { int ch; unsigned long fpb; double lat; const char* tag; };
    const Attempt attempts[] = {
        {channels, kChunkSize,                       dev->defaultLowInputLatency,  "ch=N fpb=256 low"},
        {channels, paFramesPerBufferUnspecified,     dev->defaultLowInputLatency,  "ch=N fpb=auto low"},
        {channels, paFramesPerBufferUnspecified,     dev->defaultHighInputLatency, "ch=N fpb=auto high"},
        {1,        paFramesPerBufferUnspecified,     dev->defaultLowInputLatency,  "ch=1 fpb=auto low"},
        {1,        paFramesPerBufferUnspecified,     dev->defaultHighInputLatency, "ch=1 fpb=auto high"},
        {std::min(dev->maxInputChannels, 2), paFramesPerBufferUnspecified,
                                                     dev->defaultHighInputLatency, "ch=max fpb=auto high"},
    };

    PaError last_err = paNoError;
    bool opened = false;
    for (const auto& a : attempts) {
        PaStreamParameters params{};
        params.device                    = device_index;
        params.channelCount              = a.ch;
        params.sampleFormat              = paFloat32;
        params.suggestedLatency          = a.lat > 0 ? a.lat : 0.05;
        params.hostApiSpecificStreamInfo = nullptr;

        PaError err = Pa_OpenStream(&stream_, &params, nullptr,
                                    requested_sr, a.fpb, paClipOff,
                                    &AudioAnalyzer::pa_callback, this);
        if (err != paNoError) {
            fprintf(stderr, "[audio] attempt \"%s\" failed: %s\n",
                    a.tag, Pa_GetErrorText(err));
            last_err = err;
            stream_  = nullptr;
            continue;
        }
        channel_count_ = a.ch;
        sample_rate_   = (int)requested_sr;
        opened = true;
        fprintf(stderr, "[audio] opened with \"%s\" (sr=%d)\n", a.tag, sample_rate_);
        break;
    }
    if (!opened) {
        fprintf(stderr, "[audio] all Pa_OpenStream attempts failed; last error: %s\n",
                Pa_GetErrorText(last_err));
        stream_ = nullptr;
        return false;
    }

    // Reset analyzer state.
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
    callback_count_.store(0);

    PaError err = Pa_StartStream(stream_);
    if (err != paNoError) {
        fprintf(stderr, "[audio] Pa_StartStream failed: %s\n", Pa_GetErrorText(err));
        Pa_CloseStream(stream_);
        stream_ = nullptr;
        return false;
    }
    running_.store(true);
    const PaStreamInfo* si = Pa_GetStreamInfo(stream_);
    if (si) {
        fprintf(stderr, "[audio] stream running: actual sr=%.0f input_lat=%.3fs\n",
                si->sampleRate, si->inputLatency);
        sample_rate_ = (int)si->sampleRate;  // adopt actual rate
    } else {
        fprintf(stderr, "[audio] stream running (Pa_GetStreamInfo returned null)\n");
    }
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
    self->callback_count_.fetch_add(1, std::memory_order_relaxed);
    if (!input) return paContinue;   // some WASAPI edge cases pass null briefly
    const float* src = static_cast<const float*>(input);

    // If the stream is multi-channel, downmix to mono into a per-call scratch
    // buffer. Frame count is bounded by kChunkSize (PortAudio honors our
    // framesPerBuffer request) so a small stack buffer is fine.
    if (self->channel_count_ <= 1) {
        self->process_chunk(src, frames);
    } else {
        float mono[kChunkSize];
        unsigned long n = std::min<unsigned long>(frames, kChunkSize);
        int ch = self->channel_count_;
        for (unsigned long i = 0; i < n; ++i) {
            float sum = 0.f;
            for (int c = 0; c < ch; ++c) sum += src[i * ch + c];
            mono[i] = sum / (float)ch;
        }
        self->process_chunk(mono, n);
    }
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
    const float chunk_ms = (float)n / (float)sample_rate_ * 1000.f;
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
    float bin_hz = (float)sample_rate_ / kChunkSize;
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
