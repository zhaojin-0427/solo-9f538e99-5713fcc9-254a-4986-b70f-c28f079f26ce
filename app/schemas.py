"""请求 / 响应 Pydantic 模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# 状态码：inside 内部点 / boundary 边界待判 / outside 流域外
PointStatus = Literal["inside", "boundary", "outside"]


class GridIn(BaseModel):
    grid_id: str = Field(..., min_length=1, max_length=128, description="网格唯一编号")
    geometry: dict[str, Any] = Field(..., description="Polygon 或 MultiPolygon 的 GeoJSON geometry")


class PublishRequest(BaseModel):
    expected_version: int | None = Field(
        None,
        ge=0,
        description="提交方所见的当前版号；首次发布传 null 或 0，之后传当前版号",
    )
    watershed: dict[str, Any] = Field(..., description="流域多边形 GeoJSON geometry")
    grids: list[GridIn] = Field(..., min_length=2, max_length=20, description="2~20 个带唯一编号的网格")


class ValidationIssue(BaseModel):
    type: Literal["overlap", "gap", "excess"]
    location: dict[str, float]
    representative_point: list[float]
    area: float
    message: str


class PublishResponse(BaseModel):
    version: int
    expected_version: int | None
    published_at: str
    grid_count: int
    status: Literal["published"] = "published"


class ErrorResponse(BaseModel):
    error: str
    detail: str
    issues: list[ValidationIssue] | None = None


class QueryPoint(BaseModel):
    point_id: str | None = Field(None, max_length=128, description="观测点编号，可选，便于结果对应")
    lon: float
    lat: float


class QueryRequest(BaseModel):
    version: int | None = Field(
        None, ge=1, description="固定查询版号；不传或传 null 表示当前版，一次查询内所有点同版"
    )
    points: list[QueryPoint] = Field(..., min_length=1, max_length=100, description="至多 100 个观测点")


class PointResult(BaseModel):
    point_id: str | None
    lon: float
    lat: float
    status: PointStatus
    grid_id: str | None = Field(None, description="内部点返回唯一网格编号；其余为 null")
    grid_ids: list[str] = Field(
        default_factory=list, description="边界点列出所有相接网格（待判）；域外为空"
    )


class QueryResponse(BaseModel):
    version: int = Field(..., description="本次查询实际使用的版号")
    is_current: bool
    results: list[PointResult]


class VersionInfo(BaseModel):
    version: int
    expected_version: int | None
    published_at: str
    grid_count: int


# ---------- 观测站对账 ----------

Side = Literal["left", "right"]


class StationIn(BaseModel):
    station_id: str = Field(..., min_length=1, max_length=128, description="名录内唯一站点编号")
    x: int = Field(..., strict=True, description="平面坐标 x（整数）")
    y: int = Field(..., strict=True, description="平面坐标 y（整数）")


class ReconcileRequest(BaseModel):
    version: int | None = Field(
        None, ge=1, description="固定使用的网格版号；不传或 null 表示受理时的当前版"
    )
    radius: int = Field(..., ge=1, strict=True, description="正整数匹配半径；距离不超过半径才可配对")
    left: list[StationIn] = Field(..., min_length=1, max_length=10, description="左侧名录，1～10 个站点")
    right: list[StationIn] = Field(..., min_length=1, max_length=10, description="右侧名录，1～10 个站点")


class CertainPair(BaseModel):
    left_id: str
    right_id: str
    grid_id: str = Field(..., description="两点共同所在的网格（同版）")
    distance_squared: int


class StationOptions(BaseModel):
    side: Side
    station_id: str
    grid_id: str
    possible_partners: list[str] = Field(
        default_factory=list, description="全部最优方案中出现过的对端（可能为空）"
    )
    possible_unmatched: bool = Field(
        ..., description="是否存在某个最优方案使该站点未配对"
    )


class PendingStation(BaseModel):
    side: Side
    station_id: str
    x: int
    y: int
    status: Literal["boundary", "outside"] = Field(
        ..., description="边界（待判）或流域外；这类点不参与配对"
    )
    grid_ids: list[str] = Field(default_factory=list, description="边界点相接的全部网格")


class ReconcileResponse(BaseModel):
    version: int = Field(..., description="本次对账实际使用的版号，所有点均按该版判定")
    is_current: bool
    radius: int
    max_pairs: int = Field(..., description="一对一配对数的最大值")
    min_total_squared_distance: int = Field(
        ..., description="在最大配对数前提下，平方距离总和的最小值"
    )
    certain_pairs: list[CertainPair] = Field(
        default_factory=list, description="在全部最优方案中都必然成立的站点对"
    )
    station_options: list[StationOptions] = Field(
        default_factory=list, description="每个可参与配对的站点在各最优方案中的可能结论"
    )
    pending: list[PendingStation] = Field(
        default_factory=list, description="边界 / 域外点，单列待核，不参与配对"
    )
