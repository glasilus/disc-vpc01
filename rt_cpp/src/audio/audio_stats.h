#pragma once
#include <atomic>
#include <cstdint>

// POD-снимок, который пишет audio-поток, а render-поток читает lock-free.
static constexpr int kVizBins = 16;   // нормализованные полосы спектра для визуализаторов

struct AudioStats {
    float rms         = 0.f;
    float rms_mean    = 0.f;
    float bass        = 0.f;
    float mid         = 0.f;
    float treble      = 0.f;
    float flatness    = 0.f;   // spectral flatness [0..1]
    float trend_slope = 0.f;   // положительный = нарастание, отрицательный = спад
    bool  beat        = false;
    bool  is_noisy    = false;

    // ── Нормализованные (с AGC) значения для визуалов ──────────────────────
    // Автогейн приводит их к стабильному диапазону 0..1, чтобы шейдеры и
    // визуализаторы выглядели одинаково на тихом и громком материале,
    // в отличие от сырых (неограниченных) bass/mid/treble выше, на которые
    // завязаны старые эффекты.
    float level                 = 0.f;   // общая громкость, 0..1
    float bins[kVizBins]        = {};     // лог-спектр, 0..1, от низких к высоким
};

// Atomic-обёртка: пишет целиком audio-поток, читает render-поток.
// Защита - seqlock (упрощённый вариант: без спинлока, со счётчиком поколений,
// по которому читатель ловит "разорванную" запись).
struct AtomicAudioStats {
    std::atomic<uint32_t> gen{0};  // нечётный во время записи
    AudioStats data{};             // защищено полем gen

    void write(const AudioStats& s) noexcept {
        gen.fetch_add(1, std::memory_order_release); // помечаем "грязным" (нечётный)
        data = s;
        gen.fetch_add(1, std::memory_order_release); // помечаем "чистым" (чётный)
    }

    AudioStats read() const noexcept {
        AudioStats out;
        uint32_t g1, g2;
        do {
            g1 = gen.load(std::memory_order_acquire);
            if (g1 & 1) continue;   // писатель ещё не закончил
            out = data;
            g2 = gen.load(std::memory_order_acquire);
        } while (g1 != g2);
        return out;
    }
};
