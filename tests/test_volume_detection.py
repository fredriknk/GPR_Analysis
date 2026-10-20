"""Synthetic coherence and missing-support regressions; no private data."""

import unittest

import numpy as np

from soil_sensing.volumes.detection import detect_anomalies


class VolumeDetectionTests(unittest.TestCase):
    def fixture(self):
        data = np.ones((12, 16, 16)) * 10
        data[3:8, 4:9, 4:9] = 30
        return data, np.ones((15, 15), dtype=bool)

    def test_coherent_event_and_gain_invariance(self):
        data, cells = self.fixture()
        labels, report = detect_anomalies(data, cells)
        self.assertEqual(len(report["candidates"]), 1)
        self.assertGreater(labels[5, 6, 6], 0)
        self.assertEqual(labels[0, 0, 0], 0)
        scaled, _ = detect_anomalies(data * 7, cells)
        np.testing.assert_array_equal(labels, scaled)
        self.assertEqual(report["candidates"][0]["status"], "proposed")

    def test_missing_plane_and_domain_hole_split_events(self):
        data, cells = self.fixture()
        data[5] = np.nan
        labels, report = detect_anomalies(data, cells, minimum_cells=1)
        self.assertEqual(len(report["candidates"]), 2)
        self.assertFalse(labels[4:6].any())
        cells[5:7, 5:7] = False
        labels, _ = detect_anomalies(data, cells)
        self.assertFalse(labels[:, 5:7, 5:7].any())

    def test_flat_missing_and_sparse_slices_have_no_candidates(self):
        for value in (0.0, 10.0, np.nan):
            labels, report = detect_anomalies(np.full((4, 8, 8), value), np.ones((7, 7)))
            self.assertFalse(labels.any())
            self.assertEqual(report["candidates"], [])
        data, cells = self.fixture()
        labels, _ = detect_anomalies(data, cells, minimum_slice=1000)
        self.assertFalse(labels.any())
        labels, _ = detect_anomalies(data, cells, minimum_cells=100000)
        self.assertFalse(labels.any())

    def test_invalid_controls_and_negative_input_rejected(self):
        data, cells = self.fixture()
        for options in ({"threshold": float("nan")}, {"minimum_cells": 0}):
            with self.assertRaises(ValueError):
                detect_anomalies(data, cells, **options)
        with self.assertRaises(ValueError):
            detect_anomalies(-data, cells)
        with self.assertRaises(ValueError):
            detect_anomalies(data, cells[:-1])
