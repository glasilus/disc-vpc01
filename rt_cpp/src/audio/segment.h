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
    float       intensity = 0.f;   // нормализованная энергия сегмента, 0..1
};

// Логика классификации идентична Python-версии make_segment_from_stats()
Segment classify_segment(const AudioStats& s, float gate_threshold) noexcept;

const char* segment_name(SegmentType t) noexcept;
