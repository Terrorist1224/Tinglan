import type { Workspace } from "../hooks/useWorkspace";

type Props = Pick<Workspace, "task" | "format" | "setFormat" | "mode" | "setMode" | "checked" | "setChecked" | "run" | "revertPolish" | "exportFile" | "rows" | "visible" | "running">;

export default function SubtitleActions({
  task,
  format,
  setFormat,
  mode,
  setMode,
  checked,
  setChecked,
  run,
  revertPolish,
  exportFile,
  rows,
  visible,
  running,
}: Props) {
  if (!task) return null;
  return (
    <div className="subtitle-footer">
      <div className="translation-actions">
        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
          <label className="check" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={
                visible.length > 0 &&
                checked.length === visible.length
              }
              onChange={(e) =>
                setChecked(e.target.checked ? visible.map((r) => r.id) : [])
              }
            />
            全选 ({checked.length}/{visible.length})
          </label>
          {Boolean(task?.params?.last_polish?.changes?.length) && (
            <button
              type="button"
              className="btn-link"
              onClick={() => void revertPolish()}
            >
              撤回本次润色 ({task.params.last_polish?.changes?.length})
            </button>
          )}
        </div>
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
          <button
            disabled={running || !rows.length}
            onClick={() =>
              void run(
                "polish",
                checked.length ? checked : undefined,
              )
            }
          >
            AI 润色原文{checked.length ? `(${checked.length})` : "全部"}
          </button>
          <button
            disabled={running || !rows.length}
            onClick={() =>
              void run(
                "translate",
                checked.length ? checked : undefined,
              )
            }
          >
            翻译{checked.length ? `(${checked.length})` : "全部"}
          </button>
          <button
            className="primary"
            disabled={running || !rows.length}
            onClick={() =>
              void run(
                "polish_translate",
                checked.length ? checked : undefined,
              )
            }
          >
            润色并翻译{checked.length ? `(${checked.length})` : "全部"}
          </button>
        </div>
      </div>

      <div className="export-bar-row">
        <select
          aria-label="导出模式"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          style={{ width: "auto" }}
        >
          <option value="bilingual">双语导出</option>
          <option value="source">仅原文</option>
          <option value="translation">仅译文</option>
        </select>
        <select
          aria-label="导出格式"
          value={format}
          onChange={(e) => setFormat(e.target.value)}
          style={{ width: "auto" }}
        >
          <option value="srt">SRT</option>
          <option value="vtt">VTT</option>
          <option value="txt">TXT（时间轴文稿）</option>
          <option value="json">完整 JSON</option>
        </select>
        <button onClick={() => void exportFile()}>导出字幕 ↓</button>
      </div>
    </div>
  );
}
