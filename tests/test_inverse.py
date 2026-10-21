"""Synthetic inverse-adapter checks; CUDA/gprMax/private data are not required."""

import unittest

import numpy as np

from soil_sensing.inversion.gprmax import layered_input
from soil_sensing.inversion.observations import separated_training_mask


class InverseTests(unittest.TestCase):
    def test_spatial_buffer_removes_shared_support(self):
        mask = separated_training_mask([[0, 0], [0.1, 0], [1, 0]], [[0, 0]], 0.25)
        np.testing.assert_array_equal(mask, [False, False, True])
        with self.assertRaises(ValueError):
            separated_training_mask([[0, 0]], [], 0.25)
        with self.assertRaises(ValueError):
            separated_training_mask([[0, 0]], [[1, 1]], -1)

    def test_physical_input_is_explicit_and_background_separate(self):
        text = layered_input(9.0, 3.0)
        self.assertIn("#material: 9.0 0.005 1 0 upper", text)
        self.assertIn("4 3.0 0.01 lower", text)
        self.assertIn("#rx: 2.25", text)
        self.assertNotIn("0.01 lower", layered_input(9.0))
        self.assertIn("not field calibration", text)
        with self.assertRaises(ValueError):
            layered_input(0, 2)
        with self.assertRaises(ValueError):
            layered_input(9, 7)
