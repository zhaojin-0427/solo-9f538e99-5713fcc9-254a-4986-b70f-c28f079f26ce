"""观测站对账端点的端到端测试。"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    import app.config as config
    import app.db as dbmod
    import app.main as main
    importlib.reload(config)
    importlib.reload(dbmod)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c, main


def poly(coords):
    return {"type": "Polygon", "coordinates": [coords]}


# 6×6 流域；v1 左右切分（内部 x=1,2 与 x=4,5），v2 上下切分（内部 y=1,2 与 y=4,5）
WATERSHED = poly([(0, 0), (6, 0), (6, 6), (0, 6), (0, 0)])
WEST = poly([(0, 0), (3, 0), (3, 6), (0, 6), (0, 0)])
EAST = poly([(3, 0), (6, 0), (6, 6), (3, 6), (3, 0)])
BOTTOM = poly([(0, 0), (6, 0), (6, 3), (0, 3), (0, 0)])
TOP = poly([(0, 3), (6, 3), (6, 6), (0, 6), (0, 3)])


def publish(c, expected, grids):
    return c.post(
        "/publish",
        json={"expected_version": expected, "watershed": WATERSHED, "grids": grids},
    )


def publish_v1(c):
    r = publish(c, None, [
        {"grid_id": "W", "geometry": WEST},
        {"grid_id": "E", "geometry": EAST},
    ])
    assert r.status_code == 200, r.text


def publish_v2(c):
    r = publish(c, 1, [
        {"grid_id": "B", "geometry": BOTTOM},
        {"grid_id": "T", "geometry": TOP},
    ])
    assert r.status_code == 200, r.text


def stations(*items):
    return [{"station_id": sid, "x": x, "y": y} for sid, x, y in items]


def by_side(body, side):
    return {
        s["station_id"]: s for s in body["station_options"] if s["side"] == side
    }


def by_pair(body):
    return {(p["left_id"], p["right_id"]): p for p in body["certain_pairs"]}


# ---------- 基础：唯一最优解 ----------

def test_unique_optimal_pair_is_certain(client):
    c, _ = client
    publish_v1(c)
    r = c.post("/reconcile", json={
        "radius": 3,
        "left": stations(("A1", 1, 1), ("A2", 2, 1)),
        "right": stations(("B1", 2, 2)),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1 and body["is_current"] is True
    assert body["max_pairs"] == 1
    assert body["min_total_squared_distance"] == 1
    assert by_pair(body) == {("A2", "B1"): {
        "left_id": "A2",
        "right_id": "B1",
        "grid_id": "W",
        "distance_squared": 1,
    }}
    left = by_side(body, "left")
    # A1 虽与 B1 在半径内，但存在让它不配对的更优方案，不能视为确定
    assert left["A1"]["possible_partners"] == []
    assert left["A1"]["possible_unmatched"] is True
    assert left["A2"]["possible_unmatched"] is False
    assert by_side(body, "right")["B1"]["possible_unmatched"] is False
    assert body["pending"] == []


# ---------- 多组最优方案：不得任取一组冒充确定结论 ----------

def test_tie_yields_no_certain_pair_and_all_options(client):
    c, _ = client
    publish_v1(c)
    # A1-B1 与 A2-B1 距离都是 1，配对数同为 1；无法确定谁是 B1 的对端
    r = c.post("/reconcile", json={
        "radius": 2,
        "left": stations(("A1", 1, 1), ("A2", 1, 3)),
        "right": stations(("B1", 1, 2)),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_pairs"] == 1
    assert body["min_total_squared_distance"] == 1
    assert body["certain_pairs"] == []
    left = by_side(body, "left")
    assert left["A1"]["possible_partners"] == ["B1"]
    assert left["A2"]["possible_partners"] == ["B1"]
    assert left["A1"]["possible_unmatched"] is True
    assert left["A2"]["possible_unmatched"] is True
    assert by_side(body, "right")["B1"]["possible_unmatched"] is False


def test_secondary_objective_tie_keeps_both_solutions(client):
    c, _ = client
    publish_v1(c)
    # 两种完整配对（A1-B1+A2-B2 / A1-B2+A2-B1）总平方距离都是 2，均属最优
    r = c.post("/reconcile", json={
        "radius": 2,
        "left": stations(("A1", 1, 1), ("A2", 2, 2)),
        "right": stations(("B1", 1, 2), ("B2", 2, 1)),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_pairs"] == 2
    assert body["min_total_squared_distance"] == 2
    assert body["certain_pairs"] == []
    left = by_side(body, "left")
    assert set(left["A1"]["possible_partners"]) == {"B1", "B2"}
    assert set(left["A2"]["possible_partners"]) == {"B1", "B2"}
    for station in body["station_options"]:
        assert station["possible_unmatched"] is False


def test_secondary_objective_picks_lower_squared_distance(client):
    c, _ = client
    publish_v1(c)
    # A1 可配 B1(平方距离 1) 或 B2(平方距离 2)，最大配对数 1，应选 B1
    r = c.post("/reconcile", json={
        "radius": 3,
        "left": stations(("A1", 1, 1)),
        "right": stations(("B1", 1, 2), ("B2", 2, 2)),
    })
    assert r.status_code == 200
    body = r.json()
    assert body["max_pairs"] == 1
    assert body["min_total_squared_distance"] == 1
    assert by_pair(body) == {("A1", "B1"): {
        "left_id": "A1", "right_id": "B1",
        "grid_id": "W", "distance_squared": 1,
    }}


def test_max_pairs_precedes_distance(client):
    c, _ = client
    publish_v1(c)
    # 最短边 A1-B1 会挤掉 A2；最优方案先保证 2 对，再让总平方距离最小
    r = c.post("/reconcile", json={
        "radius": 2,
        "left": stations(("A1", 1, 1), ("A2", 1, 3)),
        "right": stations(("B1", 1, 2), ("B2", 2, 1)),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_pairs"] == 2
    assert body["min_total_squared_distance"] == 2
    assert by_pair(body) == {
        ("A1", "B2"): {"left_id": "A1", "right_id": "B2", "grid_id": "W", "distance_squared": 1},
        ("A2", "B1"): {"left_id": "A2", "right_id": "B1", "grid_id": "W", "distance_squared": 1},
    }


# ---------- 网格约束 ----------

def test_same_grid_inside_required(client):
    c, _ = client
    publish_v1(c)
    # 都在半径 5 内，但分属 W / E 两个网格，不得配对
    r = c.post("/reconcile", json={
        "radius": 5,
        "left": stations(("A1", 1, 2)),
        "right": stations(("B1", 4, 2)),
    })
    assert r.status_code == 200
    body = r.json()
    assert body["max_pairs"] == 0
    assert body["min_total_squared_distance"] == 0
    assert body["certain_pairs"] == []
    assert by_side(body, "left")["A1"]["possible_partners"] == []
    assert by_side(body, "right")["B1"]["possible_partners"] == []
    assert by_side(body, "left")["A1"]["possible_unmatched"] is True


def test_boundary_and_outside_stations_are_pending(client):
    c, _ = client
    publish_v1(c)
    r = c.post("/reconcile", json={
        "radius": 1,
        "left": stations(("A1", 3, 2), ("A2", 9, 9), ("A3", 1, 1)),
        "right": stations(("B1", 0, 6), ("B2", 1, 1)),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    pending = {(p["side"], p["station_id"]): p for p in body["pending"]}
    assert set(pending) == {("left", "A1"), ("left", "A2"), ("right", "B1")}
    assert pending[("left", "A1")]["status"] == "boundary"
    assert sorted(pending[("left", "A1")]["grid_ids"]) == ["E", "W"]
    assert pending[("left", "A2")]["status"] == "outside"
    assert pending[("right", "B1")]["status"] == "boundary"
    # 待核点不出现在可配对站点选项中
    option_ids = {(s["side"], s["station_id"]) for s in body["station_options"]}
    assert option_ids == {("left", "A3"), ("right", "B2")}
    assert by_pair(body) == {("A3", "B2"): {
        "left_id": "A3", "right_id": "B2",
        "grid_id": "W", "distance_squared": 0,
    }}


def test_radius_is_inclusive(client):
    c, _ = client
    publish_v1(c)
    r = c.post("/reconcile", json={
        "radius": 2,
        "left": stations(("A1", 1, 1)),
        "right": stations(("B1", 1, 3)),
    })
    assert r.json()["max_pairs"] == 1
    r = c.post("/reconcile", json={
        "radius": 1,
        "left": stations(("A1", 1, 1)),
        "right": stations(("B1", 1, 3)),
    })
    assert r.json()["max_pairs"] == 0


# ---------- 版号：固定同版、旧版可重现 ----------

def test_reconcile_reproducible_across_grid_revision(client):
    c, _ = client
    publish_v1(c)
    request = {
        "radius": 5,
        "left": stations(("A1", 1, 1)),
        "right": stations(("B1", 4, 1)),
    }
    v1_body = c.post("/reconcile", json=request).json()
    assert v1_body["version"] == 1 and v1_body["is_current"] is True
    assert v1_body["max_pairs"] == 0  # v1 中分属 W / E

    publish_v2(c)

    current_body = c.post("/reconcile", json=request).json()
    assert current_body["version"] == 2 and current_body["is_current"] is True
    assert current_body["max_pairs"] == 1  # v2 中同属 B
    assert by_pair(current_body) == {("A1", "B1"): {
        "left_id": "A1", "right_id": "B1",
        "grid_id": "B", "distance_squared": 9,
    }}

    pinned = c.post("/reconcile", json={**request, "version": 1}).json()
    assert pinned["version"] == 1 and pinned["is_current"] is False
    v1_without_flag = {k: v for k, v in v1_body.items() if k != "is_current"}
    pinned_without_flag = {k: v for k, v in pinned.items() if k != "is_current"}
    assert pinned_without_flag == v1_without_flag  # 旧版结果逐字段可重现


def test_reconcile_version_errors(client):
    c, _ = client
    request = {"radius": 1, "left": stations(("A1", 1, 1)), "right": stations(("B1", 1, 1))}
    r = c.post("/reconcile", json=request)
    assert r.status_code == 404
    assert r.json()["error"] == "NO_PUBLISHED_VERSION"

    publish_v1(c)
    r = c.post("/reconcile", json={**request, "version": 9})
    assert r.status_code == 404
    assert r.json()["error"] == "VERSION_NOT_FOUND"


def test_reconcile_uses_one_version_only(client, monkeypatch):
    """受理后、加载网格前另一请求发布了新版，本次对账仍只用受理时的单一版号。"""
    c, main = client
    publish_v1(c)
    original = main.db.load_snapshot
    raced = []

    def racing_load(version):
        if not raced:
            raced.append(version)
            from shapely.geometry import shape
            # 模拟并发发布第 2 版（B/T 上下切分）
            main.db.publish(
                1,
                shape(WATERSHED),
                [("B", shape(BOTTOM)), ("T", shape(TOP))],
            )
        return original(version)

    monkeypatch.setattr(main.db, "load_snapshot", racing_load)
    body = c.post("/reconcile", json={
        "radius": 5,
        "left": stations(("A1", 1, 1)),
        "right": stations(("B1", 4, 1)),
    }).json()

    assert raced == [1]
    assert main.db.current_version() == 2
    # 仍按受理时的第 1 版判定：两点分属 W / E，不得配对；不会一半用 v1 一半用 v2
    assert body["version"] == 1
    assert body["max_pairs"] == 0
    assert by_side(body, "left")["A1"]["grid_id"] == "W"
    assert by_side(body, "right")["B1"]["grid_id"] == "E"


# ---------- 输入校验 ----------

def test_reconcile_request_validation(client):
    c, _ = client
    publish_v1(c)
    base = {"radius": 1, "left": stations(("A1", 1, 1)), "right": stations(("B1", 1, 1))}

    assert c.post("/reconcile", json={**base, "radius": 0}).status_code == 422
    assert c.post("/reconcile", json={**base, "radius": -1}).status_code == 422
    assert c.post("/reconcile", json={**base, "radius": 1.5}).status_code == 422
    assert c.post("/reconcile", json={**base, "left": []}).status_code == 422
    assert c.post("/reconcile", json={**base, "right": []}).status_code == 422
    too_many = stations(*[(f"A{i}", i % 3, i % 3) for i in range(11)])
    assert c.post("/reconcile", json={**base, "left": too_many}).status_code == 422
    ten = stations(*[(f"A{i}", 1 + i % 2, 1 + i % 2) for i in range(10)])
    assert c.post("/reconcile", json={**base, "left": ten}).status_code == 200

    # 平面坐标必须为整数，且不接受字符串/浮点
    assert c.post("/reconcile", json={
        **base, "left": [{"station_id": "A1", "x": 1.5, "y": 1}],
    }).status_code == 422
    assert c.post("/reconcile", json={
        **base, "left": [{"station_id": "A1", "x": "1", "y": 1}],
    }).status_code == 422
    # 超出双精度可表示范围的整数坐标按无效几何拒绝，而不是 500
    huge = c.post("/reconcile", json={
        **base, "left": [{"station_id": "A1", "x": 10**400, "y": 1}],
    })
    assert huge.status_code == 422
    assert huge.json()["error"] == "INVALID_GEOMETRY"


def test_duplicate_station_id_rejected(client):
    c, _ = client
    publish_v1(c)
    r = c.post("/reconcile", json={
        "radius": 1,
        "left": stations(("A1", 1, 1), ("A1", 1, 2)),
        "right": stations(("B1", 1, 1)),
    })
    assert r.status_code == 422
    assert r.json()["error"] == "DUPLICATE_STATION_ID"

    # 同一编号出现在左右两侧是允许的（两套名录各自唯一）
    r = c.post("/reconcile", json={
        "radius": 1,
        "left": stations(("S1", 1, 1)),
        "right": stations(("S1", 1, 1)),
    })
    assert r.status_code == 200
    assert r.json()["max_pairs"] == 1


def test_reconcile_ten_by_ten_is_optimal(client):
    c, _ = client
    publish_v1(c)
    left = stations(*[(f"L{i}", 1, 1) for i in range(10)])
    right = stations(*[(f"R{i}", 1, 1) for i in range(10)])
    r = c.post("/reconcile", json={"radius": 1, "left": left, "right": right})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_pairs"] == 10
    assert body["min_total_squared_distance"] == 0
    # 零距离同坐标：任意完美配对都是最优，不能断定具体哪一对
    assert body["certain_pairs"] == []
    for station in body["station_options"]:
        assert station["possible_unmatched"] is False
        assert len(station["possible_partners"]) == 10


# ---------- 原有发布 / 查询行为未变 ----------

def test_publish_and_query_still_work_with_reconcile_present(client):
    c, _ = client
    publish_v1(c)
    q = c.post("/query", json={"points": [
        {"point_id": "in", "lon": 1, "lat": 2},
        {"point_id": "edge", "lon": 3, "lat": 2},
        {"point_id": "out", "lon": 9, "lat": 9},
    ]})
    assert q.status_code == 200
    by_id = {x["point_id"]: x for x in q.json()["results"]}
    assert by_id["in"]["grid_id"] == "W"
    assert by_id["edge"]["status"] == "boundary"
    assert by_id["out"]["status"] == "outside"
    assert c.get("/docs").status_code == 200
    assert c.get("/health").json()["current_version"] == 1
