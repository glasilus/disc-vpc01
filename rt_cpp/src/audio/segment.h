#pragma once
#include "audio_stats.h"

enum class SegmentType {
    SILENCE,
    BUILD,
    DROP,
    NOISE,
    IMPACT,
    SUSTAIN
};

struct Segment {
    SegmentType type     = SegmentType::SILENCE;
    float       intensity = 0.f;   // 0..1 normalised energy for this segment
};

// Identical classification logic to Python's make_segment_from_stats()
Segment classify_segment(const AudioStats& s, float gate_threshold) noexcept;

const char* segment_name(SegmentType t) noexcept;
