#include "segment.h"
#include <algorithm>
#include <cmath>

Segment classify_segment(const AudioStats& s, float gate_threshold) noexcept {
    Segment seg;

    if (s.rms < gate_threshold) {
        seg.type = SegmentType::SILENCE;
        seg.intensity = 0.f;
        return seg;
    }

    float ref = (s.rms_mean > 1e-9f) ? s.rms_mean : 1e-9f;

    if (s.trend_slope > ref * 0.07f) {
        seg.type = SegmentType::BUILD;
        seg.intensity = std::clamp(s.trend_slope / (ref * 0.3f), 0.f, 1.f);
    } else if (s.trend_slope < -ref * 0.07f && s.rms > ref) {
        seg.type = SegmentType::DROP;
        seg.intensity = std::clamp(-s.trend_slope / (ref * 0.3f), 0.f, 1.f);
    } else if (s.is_noisy) {
        seg.type = SegmentType::NOISE;
        seg.intensity = std::clamp(s.rms / ref, 0.f, 1.f);
    } else if (s.beat && s.bass > s.mid && s.bass > s.treble) {
        seg.type = SegmentType::IMPACT;
        seg.intensity = std::clamp(s.bass / (ref + 1e-9f), 0.f, 1.f);
    } else if (s.rms > ref * 1.2f) {
        seg.type = SegmentType::SUSTAIN;
        seg.intensity = std::clamp((s.rms - ref) / ref, 0.f, 1.f);
    } else {
        seg.type = SegmentType::SILENCE;
        seg.intensity = 0.f;
    }
    return seg;
}

const char* segment_name(SegmentType t) noexcept {
    switch (t) {
        case SegmentType::SILENCE: return "SILENCE";
        case SegmentType::BUILD:   return "BUILD";
        case SegmentType::DROP:    return "DROP";
        case SegmentType::NOISE:   return "NOISE";
        case SegmentType::IMPACT:  return "IMPACT";
        case SegmentType::SUSTAIN: return "SUSTAIN";
    }
    return "?";
}
