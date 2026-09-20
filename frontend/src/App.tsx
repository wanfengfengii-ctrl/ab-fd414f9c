import { useEffect, useMemo, useState } from "react";
import { fetchHealth, solveInstance } from "./api";
import type {
  AssignmentRow,
  CutStitchRow,
  FieldError,
  MaskId,
  SolveResponse,
  WitnessPayload,
} from "./types";
import "./App.css";

const MASK_LABEL: Record<MaskId, string> = {
  mask1: "掩模一",
  mask2: "掩模二",
  mask3: "掩模三",
};

const SAMPLE = {
  fragments: ["1", "2", "3", "4", "5", "6", "7", "8"].join("\n"),
  conflicts: [
    "1 2",
    "2 3",
    "3 4",
    "4 5",
    "5 6",
    "6 7",
    "7 8",
    "1 8",
    "2 8",
  ].join("\n"),
  stitches: ["1 3 5", "3 5 8", "5 7 4", "2 4 2", "4 6 6"].join("\n"),
};

type FieldKey = "fragments" | "conflicts" | "stitches";

const FIELDS: { key: FieldKey; title: string; hint: string }[] = [
  {
    key: "fragments",
    title: "片段编号",
    hint: "每行一个正整数编号（也可空格分隔多个），模型含 4–48 个唯一片段",
  },
  {
    key: "conflicts",
    title: "冲突边（必须异色）",
    hint: "每行 “端点1 端点2”，支持 1-2、1, 2 等写法",
  },
  {
    key: "stitches",
    title: "缝合边（异色则切开，计正整数权重）",
    hint: "每行 “端点1 端点2 权重”，例如 1-2: 5",
  },
];

function ErrorList({ errors }: { errors: FieldError[] }) {
  if (errors.length === 0) return null;
  return (
    <ul className="error-list">
      {errors.map((e, i) => (
        <li key={i}>
          {e.line != null && <span className="error-line">第 {e.line} 行：</span>}
          {e.message}
        </li>
      ))}
    </ul>
  );
}

function AssignmentByMask({
  rows,
  title,
  weight,
}: {
  rows: AssignmentRow[];
  title: string;
  weight?: number;
}) {
  const byMask: Record<MaskId, AssignmentRow[]> = {
    mask1: [],
    mask2: [],
    mask3: [],
  };
  for (const r of rows) byMask[r.mask].push(r);
  return (
    <div className="assignment-block">
      <h3>{title}</h3>
      <div className="mask-grid">
        {(Object.keys(byMask) as MaskId[]).map((m) => (
          <div key={m} className={`mask-card mask-${m}`}>
            <div className="mask-card-head">
              <span className="swatch" />
              {MASK_LABEL[m]}
              <span className="mask-count">{byMask[m].length} 片</span>
            </div>
            <div className="mask-fragments">
              {byMask[m].map((r) => (
                <span key={r.fragment} className="fragment-chip">
                  {r.fragment}
                </span>
              ))}
              {byMask[m].length === 0 && (
                <span className="muted">无片段</span>
              )}
            </div>
          </div>
        ))}
      </div>
      {weight != null && (
        <p className="cut-weight-hint">
          切开缝合边权重合计：<strong>{weight}</strong>
        </p>
      )}
    </div>
  );
}

function CutTable({ rows }: { rows: CutStitchRow[] }) {
  return (
    <div className="cut-table-wrap">
      <h4>
        被切开的缝合边（{rows.length} 条）
      </h4>
      {rows.length === 0 ? (
        <p className="muted">没有缝合边被切开。</p>
      ) : (
        <table className="cut-table">
          <thead>
            <tr>
              <th>端点 A</th>
              <th>端点 B</th>
              <th>权重</th>
              <th>A 所在掩模</th>
              <th>B 所在掩模</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.u}-${r.v}-${i}`}>
                <td>{r.u}</td>
                <td>{r.v}</td>
                <td className="num">{r.weight}</td>
                <td>
                  <span className={`dot dot-${r.mask_u}`} />
                  {MASK_LABEL[r.mask_u]}
                </td>
                <td>
                  <span className={`dot dot-${r.mask_v}`} />
                  {MASK_LABEL[r.mask_v]}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function OptimalPanel({
  data,
  witness,
}: {
  data: Extract<SolveResponse, { status: "optimal" }>;
  witness: WitnessPayload | null;
}) {
  return (
    <div className="panel result-panel">
      <div className="result-head">
        <span className="badge badge-optimal">已求得最优解</span>
        <span className={`badge ${data.unique ? "badge-unique" : "badge-multi"}`}>
          {data.unique
            ? "规范化后的最优解唯一"
            : "规范化后存在多个最优解"}
        </span>
      </div>
      <div className="summary-row">
        <div className="stat">
          <div className="stat-label">最小切开权重和</div>
          <div className="stat-value">{data.optimal_weight}</div>
        </div>
        <div className="stat">
          <div className="stat-label">缝合边总权重</div>
          <div className="stat-value">{data.total_stitch_weight}</div>
        </div>
        <div className="stat">
          <div className="stat-label">被切开边数</div>
          <div className="stat-value">{data.cut_stitches.length}</div>
        </div>
        <div className="stat">
          <div className="stat-label">求解耗时</div>
          <div className="stat-value">{data.stats.elapsed_ms} ms</div>
        </div>
      </div>

      <AssignmentByMask
        rows={data.assignment}
        title="掩模分配（字典序最小的规范化最优方案）"
        weight={data.optimal_weight}
      />
      <CutTable rows={data.cut_stitches} />

      {witness && (
        <div className="witness-block">
          <h3>另一份不同的最优见证（权重同为 {data.optimal_weight}）</h3>
          <AssignmentByMask
            rows={witness.assignment}
            title="见证分配"
            weight={data.optimal_weight}
          />
          <CutTable rows={witness.cut_stitches} />
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [fragments, setFragments] = useState(SAMPLE.fragments);
  const [conflicts, setConflicts] = useState(SAMPLE.conflicts);
  const [stitches, setStitches] = useState(SAMPLE.stitches);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [result, setResult] = useState<SolveResponse | null>(null);
  const [showWitness, setShowWitness] = useState(false);
  const [loading, setLoading] = useState(false);
  const [networkError, setNetworkError] = useState<string | null>(null);
  const [apiAlive, setApiAlive] = useState<boolean | null>(null);

  useEffect(() => {
    let stop = false;
    fetchHealth().then((ok) => {
      if (!stop) setApiAlive(ok);
    });
    return () => {
      stop = true;
    };
  }, []);

  const errorsByField = useMemo(() => {
    const grouped: Record<FieldKey, FieldError[]> = {
      fragments: [],
      conflicts: [],
      stitches: [],
    };
    for (const e of errors) grouped[e.field].push(e);
    return grouped;
  }, [errors]);

  const setters: Record<FieldKey, (v: string) => void> = {
    fragments: setFragments,
    conflicts: setConflicts,
    stitches: setStitches,
  };
  const values: Record<FieldKey, string> = {
    fragments,
    conflicts,
    stitches,
  };

  async function handleSolve() {
    setLoading(true);
    setNetworkError(null);
    // A new solve always supersedes the previous result; on invalid input
    // the old result is cleared as required.
    setResult(null);
    setErrors([]);
    setShowWitness(false);
    try {
      const data = await solveInstance({ fragments, conflicts, stitches });
      if (data.status === "invalid") {
        if ("errors" in data) {
          setErrors(data.errors);
          setResult(null);
        }
      } else {
        setResult(data);
        setErrors([]);
      }
    } catch (err) {
      setNetworkError(
        err instanceof Error ? err.message : "网络错误，无法连接求解服务",
      );
    } finally {
      setLoading(false);
    }
  }

  function handleClear() {
    setFragments("");
    setConflicts("");
    setStitches("");
    setErrors([]);
    setResult(null);
    setNetworkError(null);
  }

  function handleSample() {
    setFragments(SAMPLE.fragments);
    setConflicts(SAMPLE.conflicts);
    setStitches(SAMPLE.stitches);
    setErrors([]);
    setResult(null);
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>三掩模分配工作台</h1>
        <p className="subtitle">
          冲突边强制异色 · 精确最小化切开缝合边权重 · 结果按编号升序规范化
        </p>
        <span className={`health health-${apiAlive ? "ok" : apiAlive === null ? "pending" : "bad"}`}>
          {apiAlive ? "后端在线" : apiAlive === null ? "检测后端…" : "后端不可达"}
        </span>
      </header>

      <main className="layout">
        <section className="panel input-panel">
          <div className="toolbar">
            <button className="btn primary" onClick={handleSolve} disabled={loading}>
              {loading ? "求解中…" : "精确求解"}
            </button>
            <button className="btn" onClick={handleSample} disabled={loading}>
              载入样例
            </button>
            <button className="btn ghost" onClick={handleClear} disabled={loading}>
              清空
            </button>
          </div>

          {FIELDS.map((f) => (
            <div
              key={f.key}
              className={`field ${errorsByField[f.key].length ? "field-error" : ""}`}
            >
              <label>
                {f.title}
                {errorsByField[f.key].length > 0 && (
                  <span className="error-count">
                    {errorsByField[f.key].length} 项错误
                  </span>
                )}
              </label>
              <p className="hint">{f.hint}</p>
              <textarea
                value={values[f.key]}
                spellCheck={false}
                onChange={(e) => setters[f.key](e.target.value)}
                rows={f.key === "fragments" ? 6 : 8}
              />
              <ErrorList errors={errorsByField[f.key]} />
            </div>
          ))}
        </section>

        <section className="panel output-panel">
          {networkError && (
            <div className="banner banner-error">网络错误：{networkError}</div>
          )}
          {!result && !loading && !networkError && (
            <div className="empty-state">
              <p>在左侧粘贴或编辑片段、冲突边与缝合边，然后点击“精确求解”。</p>
              <p className="muted">
                后端会校验全部输入（自环、重复点对、两类边重叠、端点未声明、
                权重非正等），并给出精确最优方案、唯一性判定及切开边明细。
              </p>
            </div>
          )}
          {loading && <div className="empty-state">正在精确求解…</div>}
          {result?.status === "optimal" && (
            <>
              {!result.unique && result.witness && (
                <div className="witness-toggle">
                  <label>
                    <input
                      type="checkbox"
                      checked={showWitness}
                      onChange={(e) => setShowWitness(e.target.checked)}
                    />
                    显示另一份不同的最优见证以便复核
                  </label>
                </div>
              )}
              <OptimalPanel
                data={result}
                witness={showWitness ? result.witness ?? null : null}
              />
            </>
          )}
          {result?.status === "infeasible" && (
            <div className="panel result-panel">
              <span className="badge badge-infeasible">无解</span>
              <p className="status-message">{result.message}</p>
              <p className="muted">
                冲突图无法用三种颜色合法着色（至少存在一个不可三色化的子结构，
                如四色团）。这与输入非法不同：所有输入均通过校验。
              </p>
              <p className="muted">搜索节点数 {result.stats.nodes}。</p>
            </div>
          )}
          {result?.status === "inconclusive" && (
            <div className="panel result-panel">
              <span className="badge badge-inconclusive">未能在预算内判定</span>
              <p className="status-message">{result.message}</p>
              <p className="muted">
                这既不是输入错误，也没有证明无解；可调整实例规模后重试。
              </p>
            </div>
          )}
        </section>
      </main>

      <footer className="app-footer">
        React + FastAPI · 冲突约束异色 · 缝合边正整数权重 · 精确最优
      </footer>
    </div>
  );
}
