"""Tests for analyzer.py — SegmentClassifier."""
import pytest
import numpy as np
from vpc.analyzer import Segment, SegmentType, SegmentClassifier


def test_segment_type_values():
    types = [SegmentType.IMPACT, SegmentType.NOISE, SegmentType.SUSTAIN,
             SegmentType.SILENCE, SegmentType.BUILD, SegmentType.DROP]
    assert len(types) == 6


def test_segment_dataclass():
    seg = Segment(
        t_start=0.0, t_end=0.5, duration=0.5,
        type=SegmentType.IMPACT, intensity=0.8,
        rms=0.3, flatness=0.1, rms_change=0.2
    )
    assert seg.duration == 0.5
    assert seg.type == SegmentType.IMPACT
    assert 0.0 <= seg.intensity <= 1.0


def test_classify_impact():
    clf = SegmentClassifier(rms_mean=0.1, flat_mean=0.1)
    seg = clf.classify(
        t_start=0.0, t_end=0.1, rms=0.25, flatness=0.08,
        rms_change=0.15, rms_history=[]
    )
    assert seg.type == SegmentType.IMPACT
    assert seg.duration == pytest.approx(0.1)


def test_classify_silence():
    clf = SegmentClassifier(rms_mean=0.2, flat_mean=0.1)
    seg = clf.classify(
        t_start=0.0, t_end=1.0, rms=0.05, flatness=0.05,
        rms_change=0.0, rms_history=[]
    )
    assert seg.type == SegmentType.SILENCE


def test_classify_noise():
    clf = SegmentClassifier(rms_mean=0.1, flat_mean=0.1)
    seg = clf.classify(
        t_start=0.0, t_end=0.5, rms=0.15, flatness=0.25,
        rms_change=0.01, rms_history=[]
    )
    assert seg.type == SegmentType.NOISE


def test_classify_build():
    clf = SegmentClassifier(rms_mean=0.1, flat_mean=0.1)
    history = [0.05, 0.07, 0.09, 0.11, 0.13]
    seg = clf.classify(
        t_start=0.0, t_end=0.5, rms=0.15, flatness=0.05,
        rms_change=0.02, rms_history=history
    )
    assert seg.type == SegmentType.BUILD


def test_classify_drop():
    clf = SegmentClassifier(rms_mean=0.1, flat_mean=0.1)
    history = [0.3, 0.25, 0.2, 0.15, 0.11]
    seg = clf.classify(
        t_start=0.0, t_end=0.5, rms=0.05, flatness=0.05,
        rms_change=-0.25, rms_history=history
    )
    assert seg.type == SegmentType.DROP


def test_classify_sustain():
    clf = SegmentClassifier(rms_mean=0.1, flat_mean=0.1)
    seg = clf.classify(
        t_start=0.0, t_end=0.8, rms=0.25, flatness=0.05,
        rms_change=0.01, rms_history=[]
    )
    assert seg.type == SegmentType.SUSTAIN


def test_intensity_normalized():
    clf = SegmentClassifier(rms_mean=0.1, flat_mean=0.1)
    seg = clf.classify(
        t_start=0.0, t_end=0.1, rms=0.5, flatness=0.05,
        rms_change=0.4, rms_history=[]
    )
    assert 0.0 <= seg.intensity <= 1.0
