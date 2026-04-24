#pragma once
#include <atomic>
#include <cstdint>

// Plain-old-data snapshot written by the audio thread, read lock-free by the render thread.
// All floats stored as uint32_t via bit_cast to allow std::atomic<uint32_t>.
struct AudioStats {
    float rms         = 0.f;
    float rms_mean    = 0.f;
    float bass        = 0.f;
    float mid         = 0.f;
    float treble      = 0.f;
    float flatness    = 0.f;   // spectral flatness [0..1]
    float trend_slope = 0.f;   // positive = build, negative = drop
    bool  beat        = false;
    bool  is_noisy    = false;
};

// Atomic wrapper: written fully by audio thread, read by render thread.
// We protect it with a seqlock pattern (simple version: spinlock-free with
// a generation counter so the reader can detect a torn write).
struct AtomicAudioStats {
    std::atomic<uint32_t> gen{0};  // odd while writing
    AudioStats data{};             // protected by gen

    void write(const AudioStats& s) noexcept {
        gen.fetch_add(1, std::memory_order_release); // mark dirty (odd)
        data = s;
        gen.fetch_add(1, std::memory_order_release); // mark clean (even)
    }

    AudioStats read() const noexcept {
        AudioStats out;
        uint32_t g1, g2;
        do {
            g1 = gen.load(std::memory_order_acquire);
            if (g1 & 1) continue;   // writer is mid-write
            out = data;
            g2 = gen.load(std::memory_order_acquire);
        } while (g1 != g2);
        return out;
    }
};
