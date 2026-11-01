import { time } from "../format";
import type { Workspace } from "../hooks/useWorkspace";

type Props = Pick<Workspace, "task" | "draft" | "filter" | "checked" | "setChecked" | "selectSegment" | "rows" | "visible" | "activeRows">;

export default function SubtitleList({ task, draft, filter, checked, setChecked, selectSegment, rows, visible, activeRows }: Props) {
  if (!task) return null;
  return (
    <div className="subtitle-list">
      {visible.map((r, i) => (
        <article
          id={"row-" + r.id}
          key={r.id}
          className={
            "subtitle-row " +
            (draft?.id === r.id ? "selected " : "") +
            (activeRows.some((ar) => ar.id === r.id) ? "playing" : "")
          }
        >
          <input
            aria-label={"选择第 " + (i + 1) + " 句"}
            type="checkbox"
            checked={checked.includes(r.id)}
            onChange={(e) =>
              setChecked(
                e.target.checked
                  ? [...checked, r.id]
                  : checked.filter((id) => id !== r.id),
              )
            }
          />
          <button
            className="subtitle-content"
            onClick={() => void selectSegment(r)}
          >
            <div className="line-meta">
              <span>
                {String(i + 1).padStart(3, "0")} · {time(r.start)} →{" "}
                {time(r.end)}
              </span>
              {r.speaker && (
                <span className="speaker-badge">
                  {task?.params.speakers?.[r.speaker]?.name ||
                    `说话人 ${r.speaker}`}
                </span>
              )}
              {r.review?.includes("润色被模型拒绝") && (
                <span className="tag-badge badge-warning" title={r.review}>润色被拒</span>
              )}
              {r.review?.includes("模型拦截") && (
                <span className="tag-badge badge-danger" title={r.review}>模型拦截</span>
              )}
              {(r.end <= r.start || (r.end - r.start) < 200) && (
                <span className="tag-badge badge-danger" title="起止时间异常">时间异常</span>
              )}
              {(r.review?.includes("跨说话人") || r.review?.includes("未匹配到说话人")) && (
                <span className="tag-badge badge-info" title={r.review}>待校说话人</span>
              )}
              {(r.review?.includes("分块交界") || r.review?.includes("分块边界可能重复")) && (
                <span className="tag-badge badge-muted" title={r.review}>边界重复</span>
              )}
              {r.review?.includes("需检查") && (
                <span className="tag-badge badge-warning" title={r.review}>需检查</span>
              )}
              {r.manual_translation === 1 && (
                <span className="tag-badge badge-success" title="人工编辑译文">人工修改</span>
              )}
            </div>
            <p className="source">{r.source}</p>
            <p
              className={
                "translation " +
                (!r.translation ? "placeholder" : "")
              }
            >
              {r.translation || "尚未翻译"}
              {r.translation && r.translation_provider === "azure" && (
                <span className="provider-badge">Azure 翻译（历史）</span>
              )}
              {r.translation && r.translation_provider === "google" && (
                <span className="provider-badge">谷歌翻译（历史）</span>
              )}
              {r.translation &&
                r.translated_version !== r.source_version && (
                  <span className="translation-stale">
                    译文已过期
                  </span>
                )}
            </p>
          </button>
        </article>
      ))}
      {!rows.length && (
        <p className="sidebar-empty">
          尚未识别出字幕。请点击上方“开始语音识别”。
        </p>
      )}
    </div>
  );
}
