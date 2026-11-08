#!/usr/bin/env python3
"""Tests for the coverage-derived rows of scripts/check_capability_matrix.py (#185)."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import check_capability_matrix as matrix  # noqa: E402

GOLDEN = {
    "models": {
        "a.purifier.1": {"category": "matched", "deviceType": "air-purifier", "service": "air-purifier"},
        "a.speaker.1": {"category": "matched", "deviceType": "speaker", "service": "speaker"},
        "a.washer.1": {"category": "droppedControllable", "deviceType": "washer"},
        "a.washer.2": {"category": "matched", "deviceType": "washer", "service": "washer"},
    },
    "windowsDivergences": {
        "a.speaker.1": {"category": "misclaimed", "deviceType": "speaker", "service": "air-conditioner"},
    },
}


def row(macos: str, windows: str, *types: str) -> dict:
    return {"id": "devices", "macos": macos, "windows": windows, "miotTypes": list(types)}


class CoverageStatusTests(unittest.TestCase):
    def test_every_spec_matched_is_supported(self):
        self.assertEqual(matrix.coverage_status(GOLDEN, ["air-purifier"], "macos"), "supported")

    def test_windows_reads_its_divergence_instead_of_the_macos_category(self):
        self.assertEqual(matrix.coverage_status(GOLDEN, ["speaker"], "macos"), "supported")
        self.assertEqual(matrix.coverage_status(GOLDEN, ["speaker"], "windows"), "unsupported")

    def test_some_specs_matched_is_partial(self):
        self.assertEqual(matrix.coverage_status(GOLDEN, ["air-purifier", "speaker"], "windows"), "partial")
        self.assertEqual(matrix.coverage_status(GOLDEN, ["washer"], "macos"), "partial")


class CoverageErrorTests(unittest.TestCase):
    def test_matching_claims_pass(self):
        self.assertEqual(matrix.coverage_errors(GOLDEN, [row("supported", "partial", "air-purifier", "speaker")]), [])

    def test_a_readme_claim_the_baseline_contradicts_fails(self):
        errors = matrix.coverage_errors(GOLDEN, [row("supported", "supported", "air-purifier", "speaker")])
        self.assertEqual(len(errors), 1)
        self.assertIn("windows", errors[0])
        self.assertIn("speaker", errors[0])

    def test_a_type_missing_from_the_baseline_fails(self):
        errors = matrix.coverage_errors(GOLDEN, [row("supported", "supported", "air-purifer")])
        self.assertEqual(len(errors), 1)
        self.assertIn("air-purifer", errors[0])

    def test_rows_without_miot_types_stay_documentation_only(self):
        self.assertEqual(matrix.coverage_errors(GOLDEN, [{"id": "voice", "macos": "supported", "windows": "partial"}]), [])


if __name__ == "__main__":
    unittest.main()
