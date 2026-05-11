from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.hypotheses import framework
from analysis.hypotheses import build_reports


class TestHypothesisFramework(unittest.TestCase):
    def test_every_hypothesis_spec_validates(self):
        specs = framework.load_hypothesis_specs(PROJECT_ROOT)
        self.assertEqual([s["id"] for s in specs], [f"H{i}" for i in range(1, 5)])

    def test_registry_reports_workload_calibration_analyzable(self):
        registry = framework.build_registry(PROJECT_ROOT)
        by_id = {h["id"]: h for h in registry["hypotheses"]}
        self.assertEqual(by_id["H2"]["execution_status"], "analyzable")
        self.assertEqual(by_id["H2"]["declared_status"], "supported")
        self.assertEqual(by_id["H2"]["setup"]["experiments"][0]["name"],
                         "retransmission-deadline-fluc")
        self.assertEqual(by_id["H3"]["execution_status"], "analyzable")
        self.assertEqual(by_id["H3"]["declared_status"], "supported")
        self.assertEqual(by_id["H3"]["setup"]["experiments"][0]["name"],
                         "scream-vs-gcc-mahimahi-5g-cqi")
        self.assertEqual(by_id["H4"]["execution_status"], "analyzable")
        self.assertEqual(by_id["H4"]["declared_status"], "supported")
        self.assertEqual(by_id["H4"]["setup"]["experiments"][0]["name"],
                         "scream-vs-gcc-mahimahi-5g-cqi-x0p33-snow-384x216")

    def test_write_registry_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "index.json"
            registry = framework.write_registry(out, PROJECT_ROOT)
            loaded = json.loads(out.read_text())
            self.assertEqual(loaded["schema_version"], 1)
            self.assertEqual(len(loaded["hypotheses"]), len(registry["hypotheses"]))

    def test_workload_calibration_report_includes_figures_and_setup(self):
        reports = build_reports.write_reports()
        h3 = reports["H3"]
        self.assertEqual(h3["environment"]["camera_worker"], "aum")
        self.assertEqual(h3["environment"]["viewer_worker"], "veda")
        self.assertEqual(len(h3["experiments"]), 3)
        self.assertTrue((PROJECT_ROOT / "analysis/hypotheses/results/h3_capacity_headroom.svg").is_file())
        self.assertTrue((PROJECT_ROOT / "analysis/hypotheses/results/h3_ho_headroom_timeseries.svg").is_file())
        h3_svg = (PROJECT_ROOT / "analysis/hypotheses/results/h3_capacity_headroom.svg").read_text()
        self.assertIn('width="241.2pt" height="123.84pt"', h3_svg)
        self.assertIn("Matplotlib", h3_svg)
        ho_rows = [r for r in h3["tables"]["headroom"] if r["network"] == "mahimahi-5g-ho-100ms"]
        self.assertGreater(min(r["median_headroom"] for r in ho_rows), 10.0)

    def test_realistic_trace_scream_advantage_report_includes_comparison(self):
        reports = build_reports.write_reports()
        h4 = reports["H4"]
        self.assertTrue((PROJECT_ROOT / "docs/FIGURE_STYLE_PRINCIPLES.md").is_file())
        self.assertEqual(h4["environment"]["camera_worker"], "aum")
        self.assertEqual(h4["environment"]["viewer_worker"], "veda")
        self.assertEqual(len(h4["experiments"]), 3)
        self.assertIn("post-payload RTP pacing", h4["claim_structure"]["conclusion"])
        self.assertEqual(len(h4["claim_structure"]["subclaims"]), 5)
        self.assertIn("GCC's target falls", h4["claim_structure"]["subclaims"][-1])
        h3_page = (PROJECT_ROOT / "analysis/hypotheses/h3.html").read_text()
        h4_page = (PROJECT_ROOT / "analysis/hypotheses/h4.html").read_text()
        self.assertIn("Findings and Limitations", h3_page)
        self.assertIn("Findings and Limitations", h4_page)
        self.assertIn("Main claim", h4_page)
        self.assertIn("Supporting evidence", h4_page)
        self.assertNotIn("Conclusion Boundaries", h3_page)
        self.assertNotIn("Conclusion Boundaries", h4_page)
        for experiment in h4["experiments"]:
            self.assertEqual(experiment["varies"], ["congestion_control.algorithm"])
            self.assertEqual(experiment["arms"][0]["network"], experiment["arms"][1]["network"])
        self.assertTrue((PROJECT_ROOT / "analysis/hypotheses/results/h4_frame_delivery_by_trace.svg").is_file())
        self.assertTrue((PROJECT_ROOT / "analysis/hypotheses/results/h4_camera_egress_by_trace.svg").is_file())
        self.assertTrue((PROJECT_ROOT / "analysis/hypotheses/results/h4_cqi_timeseries.svg").is_file())
        self.assertTrue((PROJECT_ROOT / "analysis/hypotheses/results/h4_ho_timeseries.svg").is_file())
        self.assertTrue((PROJECT_ROOT / "analysis/hypotheses/results/h4_rb_timeseries.svg").is_file())
        captions = [figure["caption"] for figure in h4["figures"]]
        self.assertIn("Figure H4-1", captions[0])
        self.assertIn("S/G labels", captions[0])
        self.assertIn("Left:", captions[2])
        frame_svg = (PROJECT_ROOT / "analysis/hypotheses/results/h4_frame_delivery_by_trace.svg").read_text()
        self.assertIn('width="241.2pt" height="123.84pt"', frame_svg)
        self.assertIn("Matplotlib", frame_svg)
        self.assertIn("S/G=2.74x", frame_svg)
        ho_svg = (PROJECT_ROOT / "analysis/hypotheses/results/h4_ho_timeseries.svg").read_text()
        self.assertIn('width="374.4pt" height="133.2pt"', ho_svg)
        self.assertIn("Matplotlib", ho_svg)
        self.assertIn("(a) Camera egress vs. trace capacity", ho_svg)
        self.assertIn("(b) Cumulative delivered frames", ho_svg)
        self.assertIn("SCReAM/GCC frames = 2.74x", ho_svg)
        comparison = {row["trace"]: row for row in h4["tables"]["h4_trace_comparison"]}
        self.assertAlmostEqual(comparison["CQI"]["viewer_frame_ratio_scream_over_gcc"], 1.0, delta=0.02)
        self.assertGreater(comparison["HO"]["viewer_frame_ratio_scream_over_gcc"], 2.0)
        self.assertGreater(comparison["RB"]["viewer_frame_ratio_scream_over_gcc"], 1.25)
        self.assertLess(comparison["HO"]["camera_wire_ratio_scream_over_gcc"], 0.85)
        self.assertLess(comparison["RB"]["camera_wire_ratio_scream_over_gcc"], 0.85)
        self.assertLess(comparison["HO"]["gcc_encoder_target_mean_kbps"], 500.0)
        self.assertGreater(comparison["HO"]["gcc_camera_wire_mean_kbps"], 2000.0)


if __name__ == "__main__":
    unittest.main()
