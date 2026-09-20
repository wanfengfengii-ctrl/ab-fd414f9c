# 三掩模分配工作台（Tri-Mask Workbench）

先进制程版图场景：将相互冲突的版图片段精确分配到三张掩模。冲突边两端必须异色；
缝合边在两端异色时被「切开」，计入其正整数权重。系统精确最小化切开缝合边的
权重和，并判断规范化最优解是否唯一。

- **前端**：React 19 + TypeScript + Vite，浏览器中粘贴/编辑片段、冲突边、带权缝合边
- **后端**：FastAPI，真实 REST API 完成逐项校验、精确求解与结果组装
- **精确求解器**：并查集连通分量分解 + 桶消元变量消除（min-fill 序，树宽 ≤ 11）
  + DSATUR 分支定界回退（前向检查、下界剪枝、颜色对称破除）
- **规范化**：颜色按片段编号升序的首次出现次序归一化（首片固定为颜色 0）
- **唯一性**：逐位构造字典序最小最优解，并构造严格更大的第二份最优见证；
  无第二份解即唯一
- **交付**：多阶段 `Dockerfile`、可配置宿主机端口、带健康检查的
  `docker compose`、根目录可执行 `verify` 验收服务

## 模型约束

- 唯一片段数量：4–48
- 冲突边两端必须异色（冲突图需可三色着色）
- 缝合边权重为正整数，异色即切开并计入权重
- 同一无向点对：不得重复、不得同时属于两类边、不允许自环
- 所有边的端点必须在片段列表中声明

输入每行一条，支持常见写法：`1 2`、`1-2`、`1, 2`、`1-2: 5`、`# 注释`。

## 快速开始（Docker）

```bash
docker compose up --build
# 浏览器打开 http://localhost:8080
```

自定义宿主机端口：

```bash
HOST_PORT=9090 docker compose up --build
```

可调环境变量（见 `.env.example`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HOST_PORT` | `8080` | 宿主机暴露端口（容器内固定 8000） |
| `SOLVER_TIME_LIMIT` | `20` | 单次精确求解的墙钟预算（秒） |

## 验收服务 verify

根目录下的 `verify` 是可执行入口：自动构建镜像、启动应用栈（等待健康检查通过），
然后对**真实运行的 HTTP API** 跑端到端验收套件（31 项检查），结束后自动清理：

```bash
./verify
# 保留运行中的栈：
KEEP_STACK=1 ./verify
```

也可以直接调用 compose 的 verify profile：

```bash
docker compose up -d --build app
docker compose --profile verify run --rm verify
```

验收覆盖：健康检查与静态页面、逐项输入校验、无解（infeasible）与非法输入
（invalid）的区分、最优性、切边权重核对、首次出现规范化、唯一性判定与见证、
以及 90+ 随机生成实例的端到端自洽性。

## 本地开发

后端：

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

前端（Vite 开发服务器，已配置 `/api`、`/health` 代理到 8000）：

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

生产模式下 FastAPI 直接托管 `npm run build` 的静态产物（镜像内
`/app/static`，可用 `STATIC_DIR` 覆盖）。

## API

`POST /api/solve`

```json
{
  "fragments": "1 2 3 4",
  "conflicts": "1-2\n2 3",
  "stitches": "1 3 5"
}
```

响应状态：

- `status: "optimal"`：含 `optimal_weight`、按编号升序的 `assignment`、
  `cut_stitches` 明细、`unique` 与可选的 `witness`
- `status: "infeasible"`：冲突图不存在合法三色着色（区别于输入非法）
- `status: "invalid"`（HTTP 422）：逐项 `errors`，每条含字段、行号与中文说明
- `status: "inconclusive"`：时间预算内未能判定（既不伪报无解也不给出非最优方案）

## 目录结构

```
backend/        FastAPI 应用、输入校验、精确求解器、求解器单测
frontend/       React + Vite 工作台
acceptance/     对真实 HTTP API 的端到端验收脚本
Dockerfile      前端构建 + 后端运行时多阶段镜像
docker-compose.yml   app 服务（端口/健康检查）+ verify 验收服务
verify          可执行验收入口
```
