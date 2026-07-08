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

// Имена устройств от PortAudio MME/DirectSound на Windows приходят в текущей
// ANSI-кодовой странице. WASAPI и WDM-KS уже отдают UTF-8. Конвертируем через
// CP_ACP -> UTF-8, только если в строке есть байты, не валидные как UTF-8.
static std::string to_utf8(const char* raw) {
    if (!raw || !*raw) return "";
#if defined(_WIN32)
    // Проверяем, не UTF-8 ли строка уже. MB_ERR_INVALID_CHARS заставляет
    // MultiByteToWideChar вернуть 0 при любой невалидной последовательности,
    // так что положительный результат значит "это валидный UTF-8, оставляем как есть".
    int len = (int)std::strlen(raw);
    int wlen = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, raw, len, nullptr, 0);
    if (wlen > 0) return std::string(raw, len); // валидный UTF-8
    // Иначе декодируем как системную ANSI -> wide -> UTF-8
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

// Приоритет host API для дедупликации - при совпадении имён побеждает большее
// значение. Ниже задержка и стабильнее API - выше очки.
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
        // Пишем в лог вообще все устройства, которые видит PortAudio, включая
        // output-only - диагностика должна показывать полную картину.
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
    fft_in_  = fftwf_alloc_real(kFftSize);
    fft_out_ = fftwf_alloc_complex(kFftSize / 2 + 1);
    fft_plan_ = fftwf_plan_dft_r2c_1d(kFftSize, fft_in_, fft_out_, FFTW_MEASURE);

    // Окно Ханна считаем один раз - применяется к каждому кадру анализа, чтобы
    // подавить spectral leakage (прямоугольное окно сильно размазывает бас).
    for (int i = 0; i < kFftSize; ++i)
        hann_[i] = 0.5f * (1.f - std::cos(2.f * 3.14159265358979f * i / (kFftSize - 1)));
}

int AudioAnalyzer::default_input_device() {
    // На Windows предпочитаем дефолтное input-устройство WASAPI, иначе
    // берём глобальный дефолт PortAudio. Возвращает -1, если нет ничего.
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
    // 1) Собираем все устройства с input-каналами, имя в UTF-8 + инфо об API.
    std::vector<AudioDevice> all;
    int count = Pa_GetDeviceCount();
    for (int i = 0; i < count; ++i) {
        const PaDeviceInfo* info = Pa_GetDeviceInfo(i);
        if (!info) continue;
        // WASAPI loopback в PortAudio числится как output-устройство, но
        // репортит input-каналы, поэтому оставляем всё, где они есть.
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

    // 2) Дедуп: группируем по базовому имени (без суффикса "[Loopback]",
    //    в нижнем регистре), для каждой группы оставляем API с высшим приоритетом.
    struct Best { int idx; int prio; };
    std::unordered_map<std::string, Best> best;
    for (int i = 0; i < (int)all.size(); ++i) {
        const auto& d = all[i];
        std::string key = lower_ascii(d.name);
        // Помечаем loopback отдельным суффиксом ключа, чтобы микрофон "Foo"
        // и "Foo (loopback)" не схлопывались в одну запись - а вот
        // DirectSound "Foo" и MME "Foo" должны схлопнуться.
        if (d.is_loopback) key += "#lb";
        int prio = host_api_priority((PaHostApiTypeId)d.host_api_type);
        auto it = best.find(key);
        if (it == best.end() || prio > it->second.prio) {
            best[key] = {i, prio};
        }
    }

    // 3) Собираем финальный список, добавляя host API в имя, чтобы разные
    //    физические устройства с одинаковым именем оставались различимыми.
    std::vector<AudioDevice> result;
    result.reserve(best.size());
    for (auto& kv : best) {
        AudioDevice d = all[kv.second.idx];
        if (!d.host_api.empty())
            d.name = "[" + d.host_api + "] " + d.name;
        result.push_back(std::move(d));
    }

    // 4) Стабильный порядок: сначала обычные устройства, loopback - в конце, дальше по алфавиту.
    std::sort(result.begin(), result.end(), [](const AudioDevice& a, const AudioDevice& b){
        if (a.is_loopback != b.is_loopback) return !a.is_loopback;
        return a.name < b.name;
    });
    return result;
}

bool AudioAnalyzer::start(int device_index) {
    stop();

    // Если GUI ещё ничего не выбрал, подбираем разумный дефолт сами.
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

    // Сначала пробуем моно; если устройство отказывается, откатываемся на его
    // родное число каналов (даунмикс в моно делаем уже в callback'е).
    int channels = 1;
    if (dev->maxInputChannels < 1) {
        fprintf(stderr, "[audio] device has no input channels\n");
        return false;
    }
    if (dev->maxInputChannels > 1) channels = std::min(dev->maxInputChannels, 2);

    // Берём родной sample rate устройства, если он в разумных пределах.
    // WASAPI shared mode на современной Windows почти всегда работает на
    // 48 кГц и напрямую отклонит запрос 44.1 кГц.
    double requested_sr = (dev->defaultSampleRate >= 16000.0 &&
                           dev->defaultSampleRate <= 96000.0)
                        ? dev->defaultSampleRate : (double)kSampleRateDefault;

    // Перебираем пары (число каналов, frames_per_buffer, latency), пока
    // что-то не сработает. Pa_IsFormatSupported на WASAPI врёт (говорит
    // "supported" для того, что падает при OpenStream, и наоборот), поэтому
    // просто пробуем OpenStream напрямую. paFramesPerBufferUnspecified (= 0)
    // разрешает PortAudio самому подобрать размер буфера под драйвер - часть
    // WASAPI-драйверов это ТРЕБУЕТ и отклоняет любой фиксированный размер.
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

    // Сброс состояния анализатора.
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
    // Состояние скользящего окна / детектора.
    std::fill(std::begin(win_ring_),  std::end(win_ring_),  0.f);
    std::fill(std::begin(prev_mag_),  std::end(prev_mag_),  0.f);
    std::fill(std::begin(bin_max_),   std::end(bin_max_),   1e-4f);
    ring_pos_          = 0;
    samples_since_hop_ = 0;
    ring_primed_       = false;
    flux_mean_         = 0.f;
    flux_std_          = 0.f;
    level_max_         = 1e-4f;

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
        sample_rate_ = (int)si->sampleRate;  // берём фактический sample rate
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
    if (!input) return paContinue;   // в некоторых WASAPI-случаях кратковременно приходит null
    const float* src = static_cast<const float*>(input);

    // Даунмикс в моно блоками ограниченного размера, скармливаем скользящему
    // окну. Буфер больше НЕ обрезается до фиксированного размера - WASAPI
    // shared mode регулярно отдаёт 448-480 фреймов; старый код обрабатывал
    // только первые 256, теряя ~половину аудио и замедляя анализ в ~1.9 раза.
    const int ch = std::max(1, self->channel_count_);
    if (ch <= 1) {
        self->ingest(src, frames);
    } else {
        float mono[512];
        unsigned long done = 0;
        while (done < frames) {
            unsigned long n = std::min<unsigned long>(frames - done, 512);
            for (unsigned long i = 0; i < n; ++i) {
                float sum = 0.f;
                const float* f = src + (done + i) * ch;
                for (int c = 0; c < ch; ++c) sum += f[c];
                mono[i] = sum / (float)ch;
            }
            self->ingest(mono, n);
            done += n;
        }
    }
    return paContinue;
}

static float compute_rms(const float* buf, int n) {
    float sum = 0.f;
    for (int i = 0; i < n; ++i) sum += buf[i] * buf[i];
    return std::sqrt(sum / n);
}

static float linear_slope(const float* y, int n) {
    // Наклон методом наименьших квадратов по n равномерно расставленным точкам
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

void AudioAnalyzer::ingest(const float* mono, unsigned long n) {
    if (n == 0) return;
    elapsed_ms_ += (float)n / (float)sample_rate_ * 1000.f;

    for (unsigned long i = 0; i < n; ++i) {
        win_ring_[ring_pos_] = mono[i];
        if (++ring_pos_ >= kFftSize) { ring_pos_ = 0; ring_primed_ = true; }
        if (++samples_since_hop_ >= kHopSize && ring_primed_) {
            samples_since_hop_ = 0;
            analyze_window();
        }
    }
}

void AudioAnalyzer::analyze_window() {
    // Собираем окно по порядку (старые -> новые сэмплы) из кольца; самый
    // старый сэмпл лежит ровно в ring_pos_ (следующая позиция записи).
    // Применяем окно Ханна.
    for (int i = 0; i < kFftSize; ++i) {
        float s = win_ring_[(ring_pos_ + i) % kFftSize];
        fft_in_[i] = s * hann_[i];
    }

    // RMS по сырому (без окна) буферу для стабильной оценки громкости.
    float raw_rms = compute_rms(win_ring_, kFftSize);
    rms_smooth_ = 0.7f * rms_smooth_ + 0.3f * raw_rms;

    // ── Калибровка (уровень шума) ─────────────────────────────────────────────
    if (!calibrated_) {
        cal_buf_[cal_idx_++ % kCalibChunks] = raw_rms;
        calibration_count_++;
        if (calibration_count_ >= kCalibChunks) {
            float mean_cal = 0.f;
            for (float v : cal_buf_) mean_cal += v;
            mean_cal /= kCalibChunks;
            noise_floor_ = std::max(mean_cal * 4.f, 0.005f);
            calibrated_ = true;
        }
        gate_.store(0.005f * threshold_scale_.load());
    } else {
        gate_.store(noise_floor_ * threshold_scale_.load());
    }

    // ── FFT ───────────────────────────────────────────────────────────────────
    fftwf_execute(fft_plan_);
    const int max_bin = kFftSize / 2;
    const float bin_hz = (float)sample_rate_ / (float)kFftSize;

    // Спектр магнитуд (нужен и для spectral flux, и для полос).
    // fft_in_ больше не нужен; магнитуды считаем по требованию.
    auto mag_at = [&](int b) -> float {
        return std::sqrt(fft_out_[b][0]*fft_out_[b][0] + fft_out_[b][1]*fft_out_[b][1]);
    };

    auto band_energy = [&](float lo_hz, float hi_hz) {
        int lo = std::max(1, (int)(lo_hz / bin_hz));
        int hi = std::min(max_bin, (int)(hi_hz / bin_hz));
        float e = 0.f;
        for (int b = lo; b <= hi; ++b)
            e += fft_out_[b][0]*fft_out_[b][0] + fft_out_[b][1]*fft_out_[b][1];
        return std::sqrt(e);
    };
    float bass   = band_energy(20.f,   300.f);
    float mid    = band_energy(300.f,  3000.f);
    float treble = band_energy(3000.f, 16000.f);

    // ── Spectral flatness + spectral flux (onset) ─────────────────────────────
    float geo_sum = 0.f, arith_sum = 0.f;
    int   nbins = 0;
    float flux = 0.f;
    const int flux_hi = std::min(max_bin, (int)(4000.f / bin_hz)); // полоса kick/snare
    for (int b = 1; b <= max_bin; ++b) {
        float mag = mag_at(b);
        geo_sum   += std::log(mag + 1e-9f);
        arith_sum += mag + 1e-9f;
        nbins++;
        if (b <= flux_hi) {
            float d = mag - prev_mag_[b];
            if (d > 0.f) flux += d;
        }
        prev_mag_[b] = mag;
    }
    float flatness = 0.f;
    if (nbins > 0 && arith_sum > 1e-9f)
        flatness = std::exp(geo_sum / nbins) / (arith_sum / nbins);
    flat_mean_ = 0.9f * flat_mean_ + 0.1f * flatness;

    // ── Среднее RMS + тренд ──────────────────────────────────────────────────
    rms_history_[rms_hist_idx_] = rms_smooth_;
    rms_hist_idx_ = (rms_hist_idx_ + 1) % kTrendWindow;
    if (rms_hist_count_ < kTrendWindow) rms_hist_count_++;
    float ordered[kTrendWindow];
    int   start = (rms_hist_count_ < kTrendWindow) ? 0 : rms_hist_idx_;
    for (int i = 0; i < rms_hist_count_; ++i)
        ordered[i] = rms_history_[(start + i) % kTrendWindow];
    float slope = linear_slope(ordered, rms_hist_count_);
    if (rms_mean_ < 1e-9f) rms_mean_ = rms_smooth_;
    else                   rms_mean_ = 0.99f * rms_mean_ + 0.01f * rms_smooth_;

    // ── Детекция бита: адаптивный порог по spectral flux ──────────────────────
    // Устойчиво к затяжному басу (в отличие от старой проверки по RMS-ratio):
    // бит - это *скачок* спектральной энергии низов относительно недавней
    // статистики, а не абсолютный уровень.
    float flux_dev = flux - flux_mean_;
    flux_mean_ = 0.98f * flux_mean_ + 0.02f * flux;
    flux_std_  = 0.98f * flux_std_  + 0.02f * std::fabs(flux_dev);
    bool beat = false;
    float cooldown_elapsed = elapsed_ms_ - beat_last_time_ms_;
    bool above_gate = rms_smooth_ > gate_.load();
    if (cooldown_elapsed >= kBeatCooldownMs && above_gate &&
        flux > flux_mean_ + 1.6f * flux_std_ + 1e-6f) {
        beat = true;
        beat_last_time_ms_ = elapsed_ms_;
    }

    bool is_noisy = (flatness > flat_mean_ * 1.5f) && above_gate;

    // ── Нормализованный спектр для визуализатора (лог-полосы + AGC) ───────────
    AudioStats s;
    const float f_lo = 40.f, f_hi = 16000.f;
    for (int k = 0; k < kVizBins; ++k) {
        float t0 = (float)k       / kVizBins;
        float t1 = (float)(k + 1) / kVizBins;
        float lo_hz = f_lo * std::pow(f_hi / f_lo, t0);
        float hi_hz = f_lo * std::pow(f_hi / f_lo, t1);
        int lo = std::max(1, (int)(lo_hz / bin_hz));
        int hi = std::max(lo, std::min(max_bin, (int)(hi_hz / bin_hz)));
        float e = 0.f;
        for (int b = lo; b <= hi; ++b) e += mag_at(b);
        e /= (float)(hi - lo + 1);
        // Автогейн по полосе: следим за медленно затухающим пиком, нормализуем в 0..1.
        bin_max_[k] = std::max(e, bin_max_[k] * 0.999f);
        s.bins[k] = std::clamp(e / (bin_max_[k] + 1e-6f), 0.f, 1.f);
    }
    level_max_ = std::max(rms_smooth_, level_max_ * 0.999f);
    s.level = std::clamp(rms_smooth_ / (level_max_ + 1e-6f), 0.f, 1.f);

    // ── Публикация результата ───────────────────────────────────────────────
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
