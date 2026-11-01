import type { Workspace } from "../hooks/useWorkspace";

type Props = Pick<Workspace, "task" | "draft" | "dirty" | "saving" | "saveError" | "singleLoading" | "change" | "save" | "translateCurrent" | "polishCurrent" | "polishAndTranslateCurrent" | "running">;

export default function SubtitleEditor({
  task,
  draft,
  dirty,
  saving,
  saveError,
  singleLoading,
  change,
  save,
  translateCurrent,
  polishCurrent,
  polishAndTranslateCurrent,
  running,
}: Props) {
  if (!task) return null;
  return (
    <div className="edit-container">
      <div className="section-heading edit-heading">
        <h2>片段内容编辑</h2>
        <span
          className={
            saveError ? "danger" : dirty ? "unsaved" : "saved"
          }
        >
          {saveError
            ? "保存失败 · 未保存"
            : saving
              ? "保存中…"
              : dirty
                ? "待保存"
                : "已保存"}
        </span>
      </div>

      {draft ? (
        <div className="editor">
          <div className="two time-row">
            <label>
              开始时间（秒）
              <input
                type="number"
                step="0.001"
                min="0"
                value={draft.start / 1000}
                onChange={(e) =>
                  change({
                    start: Math.round(+e.target.value * 1000),
                  })
                }
              />
            </label>
            <label>
              结束时间（秒）
              <input
                type="number"
                step="0.001"
                min="0"
                value={draft.end / 1000}
                onChange={(e) =>
                  change({ end: Math.round(+e.target.value * 1000) })
                }
              />
            </label>
          </div>

          <label className="speaker-select">
            当前句说话人
            <select
              aria-label="当前句说话人"
              value={draft.speaker}
              onChange={(e) => change({ speaker: e.target.value })}
            >
              <option value="">未指定</option>
              {Object.keys(task?.params.speakers ?? {})
                .sort((a, b) => Number(a) - Number(b))
                .map((id) => (
                  <option key={id} value={id}>
                    {task?.params.speakers?.[id]?.name ||
                      `说话人 ${id}`}
                  </option>
                ))}
            </select>
          </label>

          <div className="editor-textareas">
            <label className="textarea-field">
              <span>原文内容</span>
              <textarea
                aria-label="原文"
                value={draft.source}
                onChange={(e) => change({ source: e.target.value })}
                placeholder="片段原文文字"
              />
            </label>

            <label className="textarea-field">
              <span>
                译文内容
                {draft.translation &&
                  draft.translation_provider === "azure" && (
                    <span className="provider-badge">Azure 翻译（历史）</span>
                  )}
                {draft.translation &&
                  draft.translation_provider === "google" && (
                    <span className="provider-badge">谷歌翻译（历史）</span>
                  )}
              </span>
              <textarea
                aria-label="译文"
                value={draft.translation}
                placeholder="点击下方“翻译”生成译文，也可直接在此编辑"
                onChange={(e) => change({ translation: e.target.value })}
              />
            </label>
          </div>

          <div className="editor-actions">
            <button
              disabled={singleLoading || running}
              onClick={() => void polishCurrent()}
            >
              {singleLoading ? "处理中…" : "润色原文"}
            </button>
            <button
              className="primary"
              disabled={singleLoading || running}
              onClick={() => void translateCurrent()}
            >
              {singleLoading ? "处理中…" : "翻译当前句"}
            </button>
            <button
              disabled={singleLoading || running}
              onClick={() => void polishAndTranslateCurrent()}
            >
              {singleLoading ? "处理中…" : "润色并翻译当前句"}
            </button>
            <button
              disabled={saving}
              onClick={() => void save(true)}
            >
              保存修改
            </button>
          </div>
        </div>
      ) : (
        <p className="muted" style={{ padding: "20px 0" }}>
          在右侧字幕稿列表中点击任意片段进行编辑与单独翻译。
        </p>
      )}
    </div>
  );
}
