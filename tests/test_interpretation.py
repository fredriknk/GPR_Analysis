"""Native tracking, review gates, shared calibration and drainage negative fixtures."""

import importlib.util
import unittest
from dataclasses import replace

HAS_EXTRAS = all(
    importlib.util.find_spec(k) for k in ("numpy", "scipy", "xarray", "shapely", "plotly", "pyproj")
)
if HAS_EXTRAS:
    import numpy as np
    import xarray as xr
    from shapely import Polygon

    from soil_sensing.export.interpretation import supported_surface_mesh
    from soil_sensing.interpretation.drainage import (
        DrainOptions,
        associate_detections,
        detect_diffraction,
        review_candidates,
    )
    from soil_sensing.interpretation.horizons import TrackingOptions, track_horizon
    from soil_sensing.interpretation.layers import (
        build_surfaces,
        finite_offset_depth,
        shared_thickness_uncertainty,
    )
    from soil_sensing.interpretation.picks import revision, validate_interpretation
    from soil_sensing.visualization.synthetic import synthetic_plots
    from soil_sensing.volumes.contracts import digest


def seed(profile, index, time, horizon="H1", status="accepted"):
    sample = int(np.argmin(abs(profile.twt_ns.values - time)))
    return dict(
        pick_id=digest([profile.attrs["source_id"], profile.attrs["profile_id"], index, horizon])[
            :24
        ],
        horizon_id=horizon,
        source_id=profile.attrs["source_id"],
        profile_id=profile.attrs["profile_id"],
        trace_id=str(profile.trace_id.values[index]),
        native_sample=sample,
        time_basis="recording",
        two_way_time_ns=float(profile.twt_ns.values[sample]),
        standard_uncertainty_ns=None,
        reviewer="synthetic analyst",
        evidence="synthetic boundary",
        status=status,
    )


@unittest.skipUnless(HAS_EXTRAS, "Requires scientific extras")
class InterpretationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profiles, _, _ = synthetic_plots()

    def profile(self):
        return self.profiles[0].copy(deep=True)

    def test_nullable_uncertainty_namespaced_picks_and_revision_hash(self):
        p, q = self.profiles[0], self.profiles[1]
        picks = [seed(p, 3, 18), seed(q, 3, 19)]
        document = revision(picks, "selection")
        validate_interpretation(document, [p, q], "selection")
        child = revision([dict(p) for p in picks], "selection", parent=document)
        self.assertEqual(child["revision"], 2)
        self.assertEqual(child["parent_hash"], document["interpretation_hash"])
        child["picks"][0]["status"] = "rejected"
        with self.assertRaises(ValueError):
            validate_interpretation(child, [p, q], "selection")
        for key, value in (
            ("standard_uncertainty_ns", -1),
            ("native_sample", 500),
            ("trace_id", "unrelated"),
            ("profile_id", "other"),
        ):
            bad = {**picks[0], key: value}
            with self.assertRaises(ValueError):
                validate_interpretation(revision([bad], "selection"), [p], "selection")

    def test_seeded_tracking_dipping_event_and_gap_split(self):
        p = self.profile()
        t = p.twt_ns.values[None, :]
        centre = 20 + 0.04 * p.distance_m.values[:, None]
        pulse = (t - centre) / 2
        p.amplitude.values[:] = 250 * np.exp(-(pulse**2)) * np.cos(pulse)
        p.amplitude.values[20:25] = 0
        p.quality_flags.values[20:25] |= np.uint32(2)
        options = TrackingOptions(search_ns=5, minimum_strength=30)
        picks = track_horizon(p, [seed(p, 2, 20)], "H1", options)
        trace_ids = list(map(str, p.trace_id.values))
        indices = [trace_ids.index(s["trace_id"]) for s in picks]
        self.assertTrue(indices)
        self.assertLess(max(indices), 20)
        self.assertTrue(
            all(s["status"] == "proposed" for s in picks if s["trace_id"] != p.trace_id.values[2])
        )
        np.testing.assert_allclose(
            [s["two_way_time_ns"] for s in picks],
            20 + 0.04 * p.distance_m.values[indices],
            atol=1.0,
        )
        self.assertEqual(track_horizon(p, [], "H1", options), [])

    def test_tracker_rejects_hard_seed_slope_conflict_without_jumping(self):
        p = self.profile()
        p.amplitude.values[:] = 0
        p.amplitude.values[:, 20] = 300
        p.amplitude.values[:, 60] = 1000
        picks = track_horizon(
            p, [seed(p, 2, 20)], "H1", TrackingOptions(search_ns=4, minimum_strength=40)
        )
        self.assertTrue(picks)
        self.assertTrue(all(abs(pick["two_way_time_ns"] - 20) < 5 for pick in picks))

    def test_finite_offset_and_shared_errors_do_not_double_count_time_zero(self):
        model = dict(
            method="homogeneous_horizontal_reflector_finite_offset",
            antenna_separation_m=0.0,
            calibration=dict(
                calibration_id="synthetic",
                evidence="simulated homogeneous model",
                velocity_m_per_ns=0.1,
                standard_uncertainty_m_per_ns=0.002,
                time_zero_standard_uncertainty_ns=5.0,
            ),
        )
        result = shared_thickness_uncertainty(
            np.array([20.0]), np.array([40.0]), np.array([0.0]), np.array([0.0]), model
        )
        self.assertAlmostEqual(float(result[0]), 0.02)
        finite = {**model, "antenna_separation_m": 0.4}
        self.assertTrue(np.isnan(finite_offset_depth([1.0], finite))[0])
        expected = np.sqrt((0.1 * 40 / 2) ** 2 - 0.2**2) - np.sqrt((0.1 * 20 / 2) ** 2 - 0.2**2)
        actual = np.diff(finite_offset_depth([20.0, 40.0], finite))[0]
        self.assertAlmostEqual(float(actual), expected)
        self.assertNotAlmostEqual(float(actual), 0.1 * 20 / 2)
        with self.assertRaises(ValueError):
            finite_offset_depth([20.0], {**model, "method": "layered"})

    def surfaces(
        self,
        top=18.0,
        bottom=55.0,
        status="accepted",
        preview=False,
        calibrated=False,
        uncertainty=None,
    ):
        profiles = self.profiles[:3]
        rows, picks = [], []
        for p in profiles:
            for i, trace in enumerate(p.trace_id.values):
                rows.append(
                    dict(
                        source_id=p.attrs["source_id"],
                        profile_id=p.attrs["profile_id"],
                        trace_id=str(trace),
                        line_id=p.attrs["profile_id"],
                        xy=[float(p.x_m.values[i] - 500000), float(p.y_m.values[i] - 6600000)],
                        located=p.position_status.values[i] in ("measured", "interpolated"),
                        position_status=str(p.position_status.values[i]),
                    )
                )
                if rows[-1]["located"]:
                    picks.extend([seed(p, i, top, "H1", status), seed(p, i, bottom, "H2", status)])
        cube = xr.Dataset(
            coords=dict(x=np.arange(1.0, 100.0, 2), y=np.arange(11.0, 30.0, 2)),
            attrs=dict(
                grid_options=dict(
                    spacing_m=2.0,
                    bin_m=1.0,
                    max_edge_m=20.0,
                    max_distance_m=20.0,
                    method="linear",
                    max_cells=100000,
                ),
                domain_geojson=Polygon([(0, 0), (100, 0), (100, 40), (0, 40)]).__geo_interface__,
                minimum_lines=2,
                calibration=None,
                time_zero_evidence=None,
            ),
        )
        if calibrated:
            cube.attrs.update(
                calibration=dict(
                    method="homogeneous_horizontal_reflector_finite_offset",
                    antenna_separation_m=0.4,
                    calibration=dict(
                        calibration_id="fixture",
                        evidence="simulated model",
                        velocity_m_per_ns=0.1,
                        standard_uncertainty_m_per_ns=0.002,
                        time_zero_standard_uncertainty_ns=0.2,
                    ),
                ),
                time_zero_evidence="synthetic reference",
                reviewed_time_zero_ns=0.0,
            )
        if uncertainty is not None:
            for pick in picks:
                pick["standard_uncertainty_ns"] = 0.2
        return build_surfaces(
            cube,
            revision(picks, "selection"),
            rows,
            [dict(layer_id="upper", top="H1", bottom="H2")],
            preview_proposed=preview,
            uncertainty=uncertainty,
        )

    def test_calibrated_layer_thickness_and_evidenced_partial_uncertainty(self):
        uncertainty = dict(
            pick_error_model="independent_across_traces",
            between_boundary_correlation=0.0,
            evidence="explicit synthetic independent pick errors",
        )
        surfaces = self.surfaces(calibrated=True, uncertainty=uncertainty)
        expected = np.sqrt((0.1 * 55 / 2) ** 2 - 0.2**2) - np.sqrt((0.1 * 18 / 2) ** 2 - 0.2**2)
        thickness = surfaces.layer_thickness_m.values
        np.testing.assert_allclose(thickness[np.isfinite(thickness)], expected)
        self.assertTrue(np.isfinite(surfaces.layer_partial_standard_uncertainty_m.values).any())
        self.assertTrue(np.isnan(surfaces.horizon_standard_uncertainty_ns.values).all())
        with self.assertRaises(ValueError):
            self.surfaces(calibrated=True, uncertainty={**uncertainty, "evidence": ""})

    def test_ordering_rejected_proposed_picks_and_uncalibrated_intervals(self):
        surfaces = self.surfaces()
        self.assertNotIn("layer_thickness_m", surfaces)
        self.assertTrue(np.isfinite(surfaces.layer_interval_twt_ns.values).any())
        self.assertTrue(np.isnan(surfaces.horizon_standard_uncertainty_ns.values).all())
        for status in ("proposed", "rejected"):
            empty = self.surfaces(status=status)
            self.assertTrue(np.isnan(empty.horizon_time_ns.values).all())
        preview = self.surfaces(status="proposed", preview=True)
        self.assertEqual(preview.attrs["review_status"], "proposed_preview")
        for top, bottom in ((55.0, 18.0), (18.0, 18.0)):
            crossing = self.surfaces(top, bottom)
            self.assertTrue(np.isnan(crossing.layer_interval_twt_ns.values).all())

    def test_mesh_all_corners_and_open_unknown_holes(self):
        values = np.ones((4, 4))
        values[1, 1] = np.nan
        mesh = supported_surface_mesh(np.arange(4.0), np.arange(4.0), values, values + 3)
        self.assertTrue(mesh["faces"])
        for row, col in mesh["parent_cells"]:
            self.assertTrue(np.isfinite(values[row : row + 2, col : col + 2]).all())
        self.assertTrue(mesh["display_cap_faces"])
        empty = supported_surface_mesh(
            np.arange(2.0), np.arange(2.0), np.array([[1.0, 1.0], [1.0, np.nan]])
        )
        self.assertEqual(empty["faces"], [])
        domain = Polygon(
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            holes=[[(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)]],
        )
        hidden_hole = supported_surface_mesh([0.0, 1.0], [0.0, 1.0], np.ones((2, 2)), domain=domain)
        self.assertEqual(hidden_hole["faces"], [])

    def diffraction(self, kind):
        p = self.profile()
        x = p.distance_m.values
        t = p.twt_ns.values
        apex = 24.0
        centre = np.sqrt(30**2 + (10 * (x - apex)) ** 2)
        if kind == "flat":
            centre[:] = 30
        elif kind == "ringing":
            p.amplitude.values[:] = 100 * np.cos(t[None, :] / 2)
        if kind != "ringing":
            # Gaussian-modulated cosine is independent of the Ricker template.
            pulse = (t[None, :] - centre[:, None]) / 1.5
            p.amplitude.values[:] = 250 * np.cos(1.7 * pulse) * np.exp(-(pulse**2))
        rows = [dict(xy=[float(a), 10.0], located=True, direction_group="east_west") for a in x]
        seed_ = dict(
            source_id=p.attrs["source_id"],
            profile_id=p.attrs["profile_id"],
            trace_id=str(p.trace_id.values[12]),
            two_way_time_ns=30.0,
            reviewer="fixture",
            evidence="analyst apex",
        )
        return p, rows, seed_

    def test_diffraction_detection_and_flat_ringing_truncation_negatives(self):
        domain = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
        for kind, expected in (("curved", True), ("flat", False), ("ringing", False)):
            p, rows, apex = self.diffraction(kind)
            detection = detect_diffraction(p, apex, rows, domain)
            self.assertEqual(detection["accepted_detection"], expected, detection)
            self.assertIsNone(detection["depth_m"])
            self.assertEqual(detection["status"], "proposed")
        p, rows, apex = self.diffraction("curved")
        rows[10]["located"] = False
        self.assertEqual(
            detect_diffraction(p, apex, rows, domain)["rejection_reason"],
            "unsupported_native_track",
        )
        apex["trace_id"] = str(p.trace_id.values[0])
        self.assertEqual(
            detect_diffraction(p, apex, rows, domain)["rejection_reason"],
            "truncated_or_insufficient_aperture",
        )

    def test_cross_line_graph_preserves_alternatives_and_never_bridges_holes(self):
        detections = [
            dict(
                detection_id=f"d{i}",
                source_id="fixture",
                profile_id=f"line{i}",
                trace_id="same-repeated-id",
                xy=[2.0, float(i * 10 + 10)],
                direction_group="east_west",
                time_basis="recording",
                two_way_time_ns=30.0,
                accepted_detection=True,
                score=0.6,
            )
            for i in range(4)
        ]
        domain = Polygon([(0, 0), (20, 0), (20, 60), (0, 60)])
        result = associate_detections(detections, domain, "one", "selection")
        self.assertEqual(result["candidates"][0]["kind"], "linked_drainage_candidate")
        self.assertIsNone(result["candidates"][0]["diameter_m"])
        self.assertFalse(result["candidates"][0]["score_is_probability"])
        gap = domain.difference(Polygon([(0, 24), (20, 24), (20, 26), (0, 26)]))
        result = associate_detections(detections, gap, "one", "selection")
        self.assertTrue(all(c["kind"] == "point_anomaly" for c in result["candidates"]))

        orthogonal = [dict(d) for d in detections]
        orthogonal[1]["direction_group"] = "north_south"
        result = associate_detections(
            orthogonal, domain, "one", "selection", replace(DrainOptions(), minimum_crossings=4)
        )
        self.assertTrue(all(c["kind"] == "point_anomaly" for c in result["candidates"]))

    def test_drain_review_revision_preserves_evidence_and_rejects_stale_parent(self):
        document = dict(
            selection_hash="fixture",
            candidates=[dict(candidate_id="one", status="proposed", evidence_score=0.7)],
            detector_signature="v1",
        )
        parent = review_candidates(document)
        decisions = dict(
            schema_version="1.0",
            kind="drainage_review",
            selection_hash="fixture",
            parent_hash=parent["interpretation_hash"],
            decisions=[
                dict(
                    candidate_id="one",
                    status="accepted",
                    reviewer="analyst",
                    evidence="synthetic control",
                )
            ],
        )
        child = review_candidates(
            dict(
                selection_hash="fixture",
                candidates=[dict(candidate_id="one", status="proposed", evidence_score=0.7)],
                detector_signature="v1",
            ),
            parent=parent,
            decisions=decisions,
        )
        self.assertEqual(child["revision"], 2)
        self.assertEqual(child["candidates"][0]["status"], "accepted")
        self.assertEqual(child["candidates"][0]["evidence_score"], 0.7)
        with self.assertRaises(ValueError):
            review_candidates(
                dict(selection_hash="fixture", candidates=[], detector_signature="other"),
                parent=parent,
            )
