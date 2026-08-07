import numpy as np
import pytest

from voice.voice_similarity import cosine_similarity


def test_cosine_similarity_of_identical_vectors_is_one():
    vector = np.array([0.1, 0.2, -0.3, 0.5], dtype=np.float32)
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)


def test_cosine_similarity_of_orthogonal_vectors_is_zero():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_scales_with_proportional_vectors():
    a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    b = a * 2.5
    assert cosine_similarity(a, b) == pytest.approx(1.0)


def test_cosine_similarity_zero_norm_returns_zero():
    zero = np.zeros(8, dtype=np.float32)
    vector = np.ones(8, dtype=np.float32)
    assert cosine_similarity(zero, vector) == 0.0
