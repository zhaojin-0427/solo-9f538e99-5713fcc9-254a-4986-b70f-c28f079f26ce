"""两套观测站名录的“同版同格”对账。

匹配约束：
- 每个点先按指定版号的网格判定位置：只有落在同一网格**内部**的两点才可能配对；
- 两点欧氏距离不超过给定半径（比较平方距离，整数运算无浮点误差）；
- 一对一匹配，一个站点至多配一个对端。

最优性：
- 第一目标：配对数最多；
- 第二目标：所有配对平方距离之和最小。

本模块返回的是**全部最优方案的公共结论**，而不是任意抽一组最优方案：
- ``certain_pairs``：在所有最优方案中都出现的站点对；
- ``station_options``：每个可参与匹配的站点，在所有最优方案中出现过的对端集合，
  以及“存在某个最优方案让它未配对”的标记。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from shapely.geometry.base import BaseGeometry

from .geometry import classify_point


@dataclass(frozen=True)
class _Station:
    index: int
    side: str
    station_id: str
    x: int
    y: int
    grid_id: str


class _MinCostFlow:
    """小规模单位容量最小费用最大流（SPFA 最短路 + 逐单位增广）。

    边结构：[to, residual_capacity, cost, reverse_edge_index]
    """

    def __init__(self, node_count: int) -> None:
        self.graph: list[list[list[int]]] = [[] for _ in range(node_count)]

    def add_edge(self, src: int, dst: int, capacity: int, cost: int) -> None:
        self.graph[src].append([dst, capacity, cost, len(self.graph[dst])])
        self.graph[dst].append([src, 0, -cost, len(self.graph[src]) - 1])

    def min_cost_max_flow(self, src: int, dst: int, max_flow: int) -> tuple[int, int]:
        node_count = len(self.graph)
        flow = 0
        cost = 0
        infinity = 10**100
        while flow < max_flow:
            dist = [infinity] * node_count
            prev_node = [-1] * node_count
            prev_edge = [-1] * node_count
            in_queue = [False] * node_count
            dist[src] = 0
            queue: deque[int] = deque([src])
            in_queue[src] = True
            while queue:
                node = queue.popleft()
                in_queue[node] = False
                base = dist[node]
                for edge_index, edge in enumerate(self.graph[node]):
                    to, capacity, edge_cost, _ = edge
                    if capacity <= 0:
                        continue
                    candidate = base + edge_cost
                    if candidate < dist[to]:
                        dist[to] = candidate
                        prev_node[to] = node
                        prev_edge[to] = edge_index
                        if not in_queue[to]:
                            queue.append(to)
                            in_queue[to] = True
            if dist[dst] == infinity:
                break
            # 单位容量：沿最短增广路推 1 单位流
            node = dst
            while node != src:
                parent = prev_node[node]
                edge = self.graph[parent][prev_edge[node]]
                edge[1] -= 1
                self.graph[node][edge[3]][1] += 1
                node = parent
            flow += 1
            cost += dist[dst]
        return flow, cost


def _solve(
    rows: Iterable[int],
    cols: Iterable[int],
    cost: Sequence[Sequence[int | None]],
) -> tuple[int, int]:
    """在给定点集上求“最大配对数 + 该配对数下的最小平方距离和”。"""
    row_list = list(rows)
    col_list = list(cols)
    if not row_list or not col_list:
        return 0, 0

    row_count = len(row_list)
    source = 0
    sink = 1 + row_count + len(col_list)
    network = _MinCostFlow(sink + 1)

    for r in range(row_count):
        network.add_edge(source, 1 + r, 1, 0)
    for c in range(len(col_list)):
        network.add_edge(1 + row_count + c, sink, 1, 0)

    for r, left_idx in enumerate(row_list):
        for c, right_idx in enumerate(col_list):
            weight = cost[left_idx][right_idx]
            if weight is not None:
                network.add_edge(1 + r, 1 + row_count + c, 1, weight)

    return network.min_cost_max_flow(source, sink, min(row_count, len(col_list)))


def _analyze_matching(
    cost: Sequence[Sequence[int | None]],
) -> dict[str, Any]:
    """求最大配对数 / 最小平方距离和，以及全部最优方案的公共结论。

    ``cost[i][j]`` 为 None 表示该对不可配。返回：
    - ``best_pairs`` / ``best_cost``：两个目标的最优值；
    - ``possible_edges``：至少出现在一个最优方案中的 (i, j, 平方距离)；
    - ``left_can_be_unmatched`` / ``right_can_be_unmatched``：
      是否存在某个最优方案让该站点未配对。

    判定依据：在最优值处删去某条边 / 某个站点后重解，仍能达到同样的最优值，
    说明存在一个不包含它、但同样最优的方案。
    """
    left_count = len(cost)
    right_count = len(cost[0]) if cost else 0
    all_left = range(left_count)
    all_right = range(right_count)
    best_pairs, best_cost = _solve(all_left, all_right, cost)

    edges = [
        (i, j, cost[i][j])
        for i in all_left
        for j in all_right
        if cost[i][j] is not None
    ]

    possible_edges: list[tuple[int, int, int]] = []
    for i, j, weight in edges:
        remaining_pairs, remaining_cost = _solve(
            (r for r in all_left if r != i),
            (c for c in all_right if c != j),
            cost,
        )
        if remaining_pairs + 1 == best_pairs and remaining_cost + weight == best_cost:
            possible_edges.append((i, j, weight))

    left_can_be_unmatched: list[bool] = [False] * left_count
    for i in all_left:
        pairs, total = _solve((r for r in all_left if r != i), all_right, cost)
        left_can_be_unmatched[i] = pairs == best_pairs and total == best_cost

    right_can_be_unmatched: list[bool] = [False] * right_count
    for j in all_right:
        pairs, total = _solve(all_left, (c for c in all_right if c != j), cost)
        right_can_be_unmatched[j] = pairs == best_pairs and total == best_cost

    return {
        "best_pairs": best_pairs,
        "best_cost": best_cost,
        "possible_edges": possible_edges,
        "left_can_be_unmatched": left_can_be_unmatched,
        "right_can_be_unmatched": right_can_be_unmatched,
    }


def reconcile_stations(
    left: Sequence[tuple[str, int, int]],
    right: Sequence[tuple[str, int, int]],
    watershed: BaseGeometry,
    grids: list[tuple[str, BaseGeometry]],
    radius: int,
    point_eps: float,
) -> dict[str, Any]:
    """对两套站点名录做同版同格对账，详见模块说明。"""
    radius_sq = radius * radius
    pending: list[dict[str, Any]] = []
    left_inside: list[_Station] = []
    right_inside: list[_Station] = []

    for side, stations, bucket in (
        ("left", left, left_inside),
        ("right", right, right_inside),
    ):
        for station_id, x, y in stations:
            verdict = classify_point(x, y, watershed, grids, point_eps)
            if verdict["status"] == "inside":
                bucket.append(
                    _Station(
                        index=len(bucket),
                        side=side,
                        station_id=station_id,
                        x=x,
                        y=y,
                        grid_id=verdict["grid_id"],
                    )
                )
            else:
                # 边界点与域外点不参与任何配对，单列待核
                pending.append(
                    {
                        "side": side,
                        "station_id": station_id,
                        "x": x,
                        "y": y,
                        "status": verdict["status"],
                        "grid_ids": verdict["grid_ids"],
                    }
                )

    left_count = len(left_inside)
    right_count = len(right_inside)

    # 仅同网格内部、且平方距离不超过半径平方的两点之间连边
    cost: list[list[int | None]] = [
        [None] * right_count for _ in range(left_count)
    ]
    for i, a in enumerate(left_inside):
        for j, b in enumerate(right_inside):
            if a.grid_id != b.grid_id:
                continue
            distance_sq = (a.x - b.x) ** 2 + (a.y - b.y) ** 2
            if distance_sq <= radius_sq:
                cost[i][j] = distance_sq

    analysis = _analyze_matching(cost)
    best_pairs = analysis["best_pairs"]
    best_cost = analysis["best_cost"]
    possible_edges = analysis["possible_edges"]
    left_can_be_unmatched = analysis["left_can_be_unmatched"]
    right_can_be_unmatched = analysis["right_can_be_unmatched"]

    left_partners: list[list[int]] = [[] for _ in range(left_count)]
    right_partners: list[list[int]] = [[] for _ in range(right_count)]
    for i, j, _ in possible_edges:
        left_partners[i].append(j)
        right_partners[j].append(i)

    # 在所有最优方案中都成立的站点对：
    # 两端都必然被匹配，且各自在所有最优方案中只有对方这一个可能对端。
    certain_pairs: list[dict[str, Any]] = []
    for i, j, weight in possible_edges:
        if (
            not left_can_be_unmatched[i]
            and left_partners[i] == [j]
            and not right_can_be_unmatched[j]
            and right_partners[j] == [i]
        ):
            certain_pairs.append(
                {
                    "left_id": left_inside[i].station_id,
                    "right_id": right_inside[j].station_id,
                    "grid_id": left_inside[i].grid_id,
                    "distance_squared": weight,
                }
            )

    station_options: list[dict[str, Any]] = []
    for a in left_inside:
        station_options.append(
            {
                "side": "left",
                "station_id": a.station_id,
                "grid_id": a.grid_id,
                "possible_partners": [
                    right_inside[j].station_id for j in left_partners[a.index]
                ],
                "possible_unmatched": left_can_be_unmatched[a.index],
            }
        )
    for b in right_inside:
        station_options.append(
            {
                "side": "right",
                "station_id": b.station_id,
                "grid_id": b.grid_id,
                "possible_partners": [
                    left_inside[i].station_id for i in right_partners[b.index]
                ],
                "possible_unmatched": right_can_be_unmatched[b.index],
            }
        )

    return {
        "max_pairs": best_pairs,
        "min_total_squared_distance": best_cost,
        "certain_pairs": certain_pairs,
        "station_options": station_options,
        "pending": pending,
    }
