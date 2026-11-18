"""匹配求解的性质测试：对拍穷举所有一对一匹配的暴力实现。"""
from __future__ import annotations

import random

from app.reconcile import _analyze_matching


def _brute_force(cost):
    """枚举全部一对一匹配，返回两个目标的最优值与全部最优方案。"""
    rows = range(len(cost))
    cols = range(len(cost[0])) if cost else range(0)
    edges = [(i, j) for i in rows for j in cols if cost[i][j] is not None]

    optimal_sets: list[set[tuple[int, int]]] = []
    best_key = None

    def walk(k: int, used_left: set[int], used_right: set[int], chosen: list[tuple[int, int]]):
        nonlocal best_key, optimal_sets
        if k == len(edges):
            # 第一目标配对数最多（负数便于取最小），第二目标平方距离和最小
            key = (-len(chosen), sum(cost[i][j] for i, j in chosen))
            if best_key is None or key < best_key:
                best_key = key
                optimal_sets = [set(chosen)]
            elif key == best_key:
                optimal_sets.append(set(chosen))
            return
        i, j = edges[k]
        walk(k + 1, used_left, used_right, chosen)
        if i not in used_left and j not in used_right:
            walk(k + 1, used_left | {i}, used_right | {j}, chosen + [(i, j)])

    walk(0, set(), set(), [])
    assert best_key is not None
    return -best_key[0], best_key[1], optimal_sets


def _expected_conclusions(cost):
    pairs, total, optimal_sets = _brute_force(cost)
    possible = set().union(*optimal_sets) if optimal_sets else set()
    rows = range(len(cost))
    cols = range(len(cost[0])) if cost else range(0)
    certain = set.intersection(*optimal_sets) if optimal_sets else set()
    left_unmatched = {
        i for i in rows if any(i not in {a for a, _ in s} for s in optimal_sets)
    }
    right_unmatched = {
        j for j in cols if any(j not in {b for _, b in s} for s in optimal_sets)
    }
    return pairs, total, possible, certain, left_unmatched, right_unmatched


def test_analyze_matching_matches_brute_force_on_random_graphs():
    rng = random.Random(20261118)
    for _ in range(400):
        row_count = rng.randint(1, 4)
        col_count = rng.randint(1, 4)
        cost = [[None] * col_count for _ in range(row_count)]
        for i in range(row_count):
            for j in range(col_count):
                if rng.random() < 0.55:
                    cost[i][j] = rng.choice([0, 1, 1, 2, 2, 3, 4, 5, 5, 9])

        expected_pairs, expected_total, possible, certain, left_unmatched, right_unmatched = (
            _expected_conclusions(cost)
        )
        analysis = _analyze_matching(cost)

        assert analysis["best_pairs"] == expected_pairs
        assert analysis["best_cost"] == expected_total
        assert {(i, j) for i, j, _ in analysis["possible_edges"]} == possible
        assert [
            i for i, flag in enumerate(analysis["left_can_be_unmatched"]) if flag
        ] == sorted(left_unmatched)
        assert [
            j for j, flag in enumerate(analysis["right_can_be_unmatched"]) if flag
        ] == sorted(right_unmatched)

        # 站点对“必然成立”的推导：两端都必然被配对，且各自可能对端只有对方
        left_partners: dict[int, list[int]] = {}
        right_partners: dict[int, list[int]] = {}
        for i, j, _ in analysis["possible_edges"]:
            left_partners.setdefault(i, []).append(j)
            right_partners.setdefault(j, []).append(i)
        derived_certain = {
            (i, j)
            for i, j, _ in analysis["possible_edges"]
            if not analysis["left_can_be_unmatched"][i]
            and left_partners[i] == [j]
            and not analysis["right_can_be_unmatched"][j]
            and right_partners[j] == [i]
        }
        assert derived_certain == certain


def test_brute_force_self_check_known_example():
    """手算例子：A1/A2 与 B1/B2 的两组完美配对总平方距离相同。"""
    cost = [
        [1, 2],
        [0, 1],
    ]
    pairs, total, possible, certain, left_unmatched, right_unmatched = (
        _expected_conclusions(cost)
    )
    assert (pairs, total) == (2, 2)
    assert possible == {(0, 0), (0, 1), (1, 0), (1, 1)}
    assert certain == set()
    assert left_unmatched == set() and right_unmatched == set()
