# 流域防汛网格服务

基于 **FastAPI + Shapely + SQLite** 的防汛网格整版发布、观测点落格查询与观测站名录对账 API。

## 业务规则

### 发布（`POST /publish`）

- 提交一个流域多边形和 **2～20 个**带唯一编号的网格 GeoJSON（Polygon 或 MultiPolygon）。
- 网格之间**内部不得重叠**（共边、共点的相切允许）。
- 网格合并后须**恰好覆盖**流域：既不能有缺口（`gap`），也不能越界（`excess`）。
- 任何重叠 / 缺口 / 越界都会在响应 `issues` 中给出问题位置（包围盒、代表点、面积），**整版拒绝**，当前版不变。
- 请求携带提交方**所见版号** `expected_version`（首次发布传 `null` 或 `0`）。两个并发改版只有一个能按该版号发布，另一个收到 `409 VERSION_CONFLICT`。
- 发布成功后版本号递增；旧版网格整版保留，永不改写。

### 查询（`POST /query`）

- 一次至多 **100 个**观测点；一次查询固定使用**同一版本**（`version` 不传 = 当前版）。
- 判定结果：
  - `inside`：点在某网格内部，返回唯一 `grid_id`；
  - `boundary`：点落在网格 / 流域边界上（含角点、洞边），列出所有相接网格，`grid_id` 为 `null`，标记为**待判**；
  - `outside`：流域外，网格列表为空。
- 指定的版本不存在返回 `404 VERSION_NOT_FOUND`；尚无任何已发布版本时查当前版返回 `404 NO_PUBLISHED_VERSION`。
- 旧版数据持久化在 SQLite 中，**进程 / 容器重启后仍可查询**。

## 快速启动（Docker Compose）

```bash
docker compose up --build
```

- 服务地址：http://localhost:8000
- 交互式文档（可直接提交和查询）：http://localhost:8000/docs
- SQLite 数据保存在命名卷 `watershed-data`（容器内 `/data/watershed.db`），重建容器不丢数据。

## 本地运行（无 Docker）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
DATABASE_PATH=data/watershed.db .venv/bin/uvicorn app.main:app --reload
```

## 请求示例

发布第一版：

```bash
curl -X POST http://localhost:8000/publish \
  -H 'Content-Type: application/json' \
  -d @examples/publish_v1.json
```

携带所见版号发布第二版（并发时只有一个成功，另一个返回 409）：

```bash
curl -X POST http://localhost:8000/publish \
  -H 'Content-Type: application/json' \
  -d @examples/publish_v2.json
```

批量查询（可传 `"version": 1` 固定查旧版，不传则查当前版）：

```bash
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d @examples/query.json
```

错误响应（重叠 / 缺口，HTTP 422）示例：

```json
{
  "error": "COVERAGE_INVALID",
  "detail": "网格存在重叠或未恰好覆盖流域，整版拒绝（问题位置见 issues）",
  "issues": [
    {
      "type": "overlap",
      "location": {"minx": 1.0, "miny": 0.0, "maxx": 2.0, "maxy": 4.0},
      "representative_point": [1.5, 2.0],
      "area": 4.0,
      "message": "网格 L 与 X 内部重叠，重叠面积 4"
    }
  ]
}
```

## 其他端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查与当前版号 |
| GET | `/versions` | 全部已发布版本清单 |
| GET | `/docs` | Swagger UI，可在线提交 / 查询 / 对账 |

## 观测站对账（`POST /reconcile`）

台网数据员合并两套观测站名录前，用它找出**可以自动认定**的同站记录。

### 请求

```json
{
  "version": 1,
  "radius": 2,
  "left":  [{"station_id": "L-001", "x": 1, "y": 1}, {"station_id": "L-002", "x": 1, "y": 3}],
  "right": [{"station_id": "R-101", "x": 1, "y": 2}, {"station_id": "R-102", "x": 3, "y": 1}]
}
```

- `left` / `right` 各 **1～10** 条站点，`station_id` 在各自名录内唯一（同一编号同时出现在两侧是允许的）。
- `x` / `y` 必须是**整数**平面坐标；`radius` 必须是**正整数**，距离不超过半径（含等于）才可配对。
- `version` 可选：指定则固定用该已发布版号；省略 / `null` 则固定用**受理时刻**的当前版，一次请求内不会中途换版。

### 配对规则

1. 每个点按**同一个版号**的网格判定位置：只有落在**同一网格内部**的两点才可能配对；
2. 两点欧氏距离 ≤ `radius`；
3. 一对一匹配，一个站点至多配一个对端。

### 判定顺序

1. 先使**配对数最多**；
2. 再使**所有配对的平方距离总和最小**。

注意这里不是“先找最近点再贪心”：为了多配一对，可以用总距离更长的方案。平方距离用整数运算，不存在浮点误差。

### 返回：全部最优方案的公共结论

响应不抽取某一种最优方案冒充确定结论，而是给出在所有最优方案中都成立的信息：

| 字段 | 含义 |
| --- | --- |
| `max_pairs` | 最大配对数 |
| `min_total_squared_distance` | 最大配对数前提下的最小平方距离总和 |
| `certain_pairs` | 在**全部**最优方案中都必然成立的站点对（含共同所在 `grid_id`、平方距离） |
| `station_options` | 每个可参与配对的站点：`possible_partners` = 所有最优方案中出现过的对端；`possible_unmatched` = 是否存在某个最优方案让它未配对 |
| `pending` | 落在网格 / 流域边界（`boundary`，含相接网格）或流域外（`outside`）的点，**不参与配对**，单列待核 |

判定口径：

- 某条边只有在“去掉它两端后仍能达到 `max_pairs - 1` 对且剩余代价恰为 `min_total_squared_distance - 该边平方距离`”时，才可能出现在最优方案中；
- 某站点只有在“去掉它后仍能达到同样的配对数与最小代价”时，才可能未配对；
- 只有**两端都必然被配对、且各自全部可能对端只有对方**的站点对，才进入 `certain_pairs`。

因此：

- 唯一最优方案时，`certain_pairs` 即该方案的配对；
- 存在并列最优方案时，`certain_pairs` 只保留各方案共同拥有的对（可能为空），其余写在 `station_options` 中，由人工复核；
- 任何站点只要存在“不配对”的最优方案，`possible_unmatched` 就为 `true`，不会被写成已配对。

### 版本与可重现性

- 一次对账只解析一次版号，所有点位固定使用该版网格，**不会混用版号**；
- 指定 `version` 时按该旧版重算，发布新网格不影响旧版对账结果；
- 响应返回实际使用的 `version` 与 `is_current`；版号不存在 → `404 VERSION_NOT_FOUND`，尚无任何发布 → `404 NO_PUBLISHED_VERSION`；
- 站点编号重复 → `422 DUPLICATE_STATION_ID`。

示例：

```bash
curl -X POST http://localhost:8000/reconcile \
  -H 'Content-Type: application/json' \
  -d @examples/reconcile.json
```

## 设计说明

- **乐观锁**：`meta.current_version` 记录当前版。发布在单条 `BEGIN IMMEDIATE` 事务内比对 `expected_version` 与当前版，不一致即回滚并返回 409；应用层另用写锁串行化同进程并发，SQLite `busy_timeout` 处理跨进程锁等待。
- **持久化**：几何以 WKB 存入 `versions` / `grids` 表，WAL 模式；每个版本独立行组，发布新版本不修改旧版任何行。
- **对账求解**：站点规模 ≤ 10×10，按同格 / 半径建二分图，用单位容量最小费用最大流先求最大匹配，再在每个候选边上重解一次子问题，得到“可能出现的边”与“可能未配对”的精确集合；平方距离为整数，费用无浮点误差。已用穷举所有匹配的暴力实现交叉验证。
- **容差**：面积比较使用 `AREA_TOL_REL × 流域面积`（默认 `1e-9`）吸收浮点噪音；点查询对距边界极近（`POINT_EPS_REL`，默认 `1e-9` × 外接矩形对角线）的点兜底判为待判，避免浮点抖动把边界点误判为域外。
- 坐标系按平面坐标处理（经纬度直接作为 x/y），面积单位与输入坐标单位一致；如需米制面积请在入库前投影。

## 测试

```bash
.venv/bin/python -m pytest tests/ -v
```

覆盖：首版发布、版号乐观锁冲突、并发发布仅一个成功、网格数上下限、编号重复、重叠 / 缺口 / 越界定位、无效几何拒绝、失败发布不改当前版、内部 / 边界 / 角点 / 洞边 / 域外判定、100 点上限、旧版查询、版本不存在、重启后旧版仍可查；对账最大配对数优先、平方距离次优、并列最优不冒充确定结论、同格约束、边界 / 域外单列、半径含等号、指定旧版可重现、10×10 规模与输入校验。
