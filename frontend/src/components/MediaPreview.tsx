import { useEffect, useState } from "react";
import { time } from "../format";
import type { Workspace } from "../hooks/useWorkspace";

type Props = Pick<
  Workspace,
  | "task"
  | "displayMode"
  | "setDisplayMode"
  | "video"
  | "notify"
  | "timeUpdate"
  | "activeRows"
>;

export default function MediaPreview({
  task,
  displayMode,
  setDisplayMode,
  video,
  notify,
  timeUpdate,
  activeRows,
}: Props) {
  const [mediaError, setMediaError] = useState(false);

  useEffect(() => {
    setMediaError(false);
  }, [task?.id]);

  if (!task) return null;
  return (
    <div className="player-box">
      <div className="section-heading">
        <h2>媒体预览</h2>
        <span>{time(Math.round(task.probe.duration * 1000))}</span>
      </div>
      <div className="player">
        {mediaError ? (
          <div className="media-unsupported-box">
            <div className="media-unsupported-icon">⚠️</div>
            <p className="media-unsupported-title">当前格式无法在浏览器中直接播放预览</p>
            <p className="media-unsupported-desc">
              该视频格式（如 AVI 容器或非网页原生编码）不影响语音识别、润色、翻译与字幕导出。完成导出后可使用系统播放器查看声画。
            </p>
          </div>
        ) : (
          <video
            key={task.id}
            ref={video}
            controls
            preload="metadata"
            src={`/api/tasks/${task.id}/media`}
            onTimeUpdate={timeUpdate}
            onError={() => setMediaError(true)}
            onLoadedData={() => setMediaError(false)}
          />
        )}
        {!mediaError && (
          <div className="subtitle-overlay" aria-live="off">
            {activeRows.map((r) => (
              <div key={r.id} className="subtitle-overlay-block">
                {displayMode === "bilingual" && (
                  <>
                    {r.translation && (
                      <div className="overlay-translation">
                        {r.translation}
                      </div>
                    )}
                    <div className="overlay-source">{r.source}</div>
                  </>
                )}
                {displayMode === "translation" && (
                  <div className="overlay-translation">
                    {r.translation || "（无译文）"}
                  </div>
                )}
                {displayMode === "source" && (
                  <div className="overlay-source">{r.source}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="play-tools">
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span style={{ fontSize: "11px", color: "var(--muted)" }}>字幕:</span>
          <select
            aria-label="字幕显示模式"
            value={displayMode}
            onChange={(e) =>
              setDisplayMode(
                e.target.value as "bilingual" | "source" | "translation",
              )
            }
            style={{ width: "auto" }}
          >
            <option value="bilingual">双语</option>
            <option value="source">仅原文</option>
            <option value="translation">仅译文</option>
          </select>
        </div>
      </div>
    </div>
  );
}
