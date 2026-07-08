import pytest
import numpy as np

@pytest.fixture
def blank_frame():
    """Кадр 480x270 RGB, полностью чёрный."""
    return np.zeros((270, 480, 3), dtype=np.uint8)

@pytest.fixture
def noise_frame():
    """Кадр 480x270 RGB, случайный шум."""
    rng = np.random.default_rng(42)
    return rng.integers(0, 256, (270, 480, 3), dtype=np.uint8)

@pytest.fixture
def white_frame():
    """Кадр 480x270 RGB, полностью белый."""
    return np.full((270, 480, 3), 255, dtype=np.uint8)
