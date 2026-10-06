# 分段三次 Bézier 轨迹审计服务

机器人标定平台在关节程序下发前，对分段三次 Bézier 轨迹进行审计。服务对**整段连续曲线**
裁决速度与加速度峰值（而非只检查控制周期采样点），因此段内超速 / 加速度峰值不会漏过。

## 接口

### `POST /api/trajectories/audit`

请求：

- `joints`：1–8 个关节，每个含 `lower`、`upper`（闭合行程）、`maxVelocity`、`maxAcceleration`（须为正）。
- `segments`：1–200 段，每段含
  - `duration`：**正**的规范十进制时长；
  - `controlPoints`：按关节给出的 4 个控制位置 `[P0, P1, P2, P3]`。

所有数值既接受 JSON 数字，也接受十进制字符串（如 `"1.50"`、`"+0.0"`、`"100e-2"`），
均按规范十进制归一化：**同一数值的等值写法不影响峰值、越限排序或最终放行结论**。

响应（200）：

```json
{
  "approved": true,
  "peaks": [{"joint": 1, "maxVelocity": 1.5, "maxAcceleration": 6.0}],
  "violations": []
}
```

越限条目含 `segment`（段号，从 1 起）、`joint`（关节号，从 1 起）、`constraint`
（`velocity` / `acceleration`）、`value`、`limit`，并按
**段号 → 关节号 → 约束类型（速度在前）** 稳定排序。

**等于上限视为合格。**

422 用于格式错误、非正时长、控制点数量不符、超出闭合行程、相邻段端点位置 / 速度不连续等，
`detail[].loc` 给出可定位字段路径，例如
`["body", "segments", 0, "controlPoints", 0, 3]`。

### `GET ${HEALTH_PATH}`（默认 `/health`）

返回 `{"status": "ok"}`。

## 连续曲线裁决（非抽样）

对时长 `T`、控制点 `P0..P3` 的一段，令 `τ = t/T ∈ [0,1]`：

- 加速度是 `τ` 的**线性**函数，故加速度峰值必在段端点：
  `a(0)=6(P0−2P1+P2)/T²`，`a(1)=6(P1−2P2+P3)/T²`；
- 速度是 `τ` 的**二次**函数，除两个端点外，当抛物线顶点
  `τ* = −b/(2a)` 严格落在 `(0,1)` 内时，该内部极值也纳入裁决。

因此无需任何采样即可精确覆盖段内峰值（如端点速度为零的 smoothstep `[0,0,1,1]`，
采样端点会误以为速度恒为零，而真实峰值为 1.5）。

- 端点速度：`v₀ = 3(P1−P0)/T`，`v₁ = 3(P3−P2)/T`。
- 相邻段连续性在十进制下**精确**判定；速度连续性用交叉相乘
  `(P3−P2)·T_next == (Q1−Q0)·T_cur` 比较，避免除法舍入。
- 峰值计算使用高精度 `Decimal`（60 位），仅对远低于 1e-40 相对量级的舍入残差容错，
  以保证“等于上限合格”。

## 运行

```bash
cp .env.example .env      # 可编辑 PORT / HOST_PORT / HEALTH_PATH
docker compose up --build api
# 自定义宿主机端口与健康路径：
HOST_PORT=9000 HEALTH_PATH=/healthz docker compose up --build api
```

### 一次性 `verify` 服务

等待 `api` 健康后，依次执行：代码测试（pytest）、构建检查（字节编译 + 应用导入）、
以及**通过轨迹与越限轨迹**的 API 冒烟（含 422 与十进制等值不变性），并以退出码报告结果：

```bash
docker compose run --build verify   # 全部通过退出码为 0，否则非 0
```

## 本地开发（无需 Docker）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/
PORT=8000 HEALTH_PATH=/health \
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
API_BASE_URL=http://127.0.0.1:8000 sh scripts/verify.sh
```

## 目录

```
app/            FastAPI 应用、十进制输入、Bézier 数学、审计逻辑
tests/          pytest 测试（18 项）
scripts/        健康等待、API 冒烟、verify 编排
Dockerfile, docker-compose.yml, .env.example
```
