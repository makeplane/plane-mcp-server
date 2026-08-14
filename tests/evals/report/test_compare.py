"""Offline eval tests for compare."""

from __future__ import annotations

import math

import pytest

from evals.report import (
    ab_compare,
    sign_test_pvalue,
)


def test_sign_test_behaviours():
    def test_sign_test_all_positive_hand_computed():
        deltas = [1.0, 2.0, 3.0, 0.5, 4.0]
        p = sign_test_pvalue(deltas)
        assert p == pytest.approx(2.0 * (1.0 / 32.0))
        assert p == pytest.approx(0.0625)

    def test_sign_test_four_of_five_hand_computed():
        deltas = [1.0, 1.0, 1.0, 1.0, -1.0]
        p = sign_test_pvalue(deltas)
        right = (math.comb(5, 4) + math.comb(5, 5)) / 32.0
        assert p == pytest.approx(2.0 * right)
        assert p == pytest.approx(0.375)

    def test_sign_test_drops_zeros_and_none_when_empty():
        assert sign_test_pvalue([0.0, 0.0]) is None
        assert sign_test_pvalue([]) is None
        # One positive, one zero → n=1, k=1 → p = 2*(1/2) = 1.0
        assert sign_test_pvalue([3.0, 0.0]) == pytest.approx(1.0)

    test_sign_test_all_positive_hand_computed()
    test_sign_test_four_of_five_hand_computed()
    test_sign_test_drops_zeros_and_none_when_empty()


def test_ab_compare_behaviours():
    def test_ab_compare_paired_deltas_and_sign_test():
        rows_a = [
            {"task_id": "R1", "rep": 0, "success": True, "num_calls": 5, "calls": []},
            {"task_id": "R2", "rep": 0, "success": True, "num_calls": 3, "calls": []},
            {"task_id": "R3", "rep": 0, "success": False, "num_calls": 9, "calls": []},  # not paired
        ]
        rows_b = [
            {"task_id": "R1", "rep": 0, "success": True, "num_calls": 2, "calls": []},  # delta -3
            {"task_id": "R2", "rep": 0, "success": True, "num_calls": 4, "calls": []},  # delta +1
            {"task_id": "R3", "rep": 0, "success": True, "num_calls": 1, "calls": []},  # A failed → not paired
        ]
        cmp = ab_compare(rows_a, rows_b)
        assert cmp["n_paired"] == 2
        deltas = {p["task_id"]: p["delta"] for p in cmp["paired_tasks"]}
        assert deltas["R1"] == -3.0
        assert deltas["R2"] == 1.0
        assert cmp["median_delta"] == pytest.approx(-1.0)  # median of [-3, 1]
        assert cmp["sign_test_p"] is not None
        assert cmp["success_a"]["k"] == 2 and cmp["success_a"]["n"] == 3
        assert cmp["success_b"]["k"] == 3 and cmp["success_b"]["n"] == 3

    def test_ab_compare_multi_rep_uses_median_successful_call_counts():
        rows_a = [
            {"task_id": "R1", "rep": 0, "success": True, "num_calls": 1, "calls": []},
            {"task_id": "R1", "rep": 1, "success": False, "num_calls": 9, "calls": []},
            {"task_id": "R1", "rep": 2, "success": True, "num_calls": 5, "calls": []},
        ]
        rows_b = [
            {"task_id": "R1", "rep": 0, "success": True, "num_calls": 2, "calls": []},
            {"task_id": "R1", "rep": 1, "success": True, "num_calls": 4, "calls": []},
            {"task_id": "R1", "rep": 2, "success": True, "num_calls": 6, "calls": []},
        ]

        cmp = ab_compare(rows_a, rows_b)

        assert cmp["multi_rep"] is True
        assert cmp["unstable_a"] == 1
        assert cmp["unstable_b"] == 0
        assert cmp["paired_tasks"] == [{"task_id": "R1", "calls_a": 3.0, "calls_b": 4.0, "delta": 1.0}]

    test_ab_compare_paired_deltas_and_sign_test()
    test_ab_compare_multi_rep_uses_median_successful_call_counts()
