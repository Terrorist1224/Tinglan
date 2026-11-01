import { statuses } from "../constants";
import type { Workspace } from "../hooks/useWorkspace";

type Props = Pick<Workspace, "tasks" | "task" | "setImportOpen" | "uploading" | "selectTask" | "deleteTask">;

export default function TaskSidebar({ tasks, task, setImportOpen, uploading, selectTask, deleteTask }: Props) {
  return (
    <aside className="sidebar">
      <a className="brand" href="/" onClick={(e) => e.preventDefault()}>
        <span className="brand-mark" aria-hidden="true">
          ≋
        </span>
        <div>
          听澜字幕<small>TINGLAN · 智能字幕工作台</small>
        </div>
      </a>
      <button
        className="primary import"
        disabled={uploading}
        onClick={() => setImportOpen(true)}
      >
        ＋ {uploading ? "正在导入…" : "导入媒体"}
      </button>
      <div className="sidebar-label">
        任务记录 <span>{tasks.length}</span>
      </div>
      <nav aria-label="任务记录">
        {tasks.map((t) => {
          const isCloud =
            t.queue_channel === "cloud" ||
            ["polish", "translate", "polish_translate"].includes(t.phase);
          const badgeColor =
            t.status === "queued"
              ? "yellow"
              : isCloud
                ? "blue"
                : "green";
          const badgeTitle =
            t.status === "queued"
              ? `序号 ${t.queue_rank}（排队中）`
              : isCloud
                ? `序号 ${t.queue_rank}（云端处理中）`
                : `序号 ${t.queue_rank}（正在转录）`;
          return (
            <div key={t.id} className="task-item-row">
              <button
                className={"task-link " + (task?.id === t.id ? "chosen" : "")}
                onClick={() => void selectTask(t.id)}
              >
                {t.queue_rank ? (
                  <span
                    className={`queue-badge queue-badge-${badgeColor}`}
                    title={badgeTitle}
                    aria-label={`任务序号 ${t.queue_rank}`}
                  >
                    {t.queue_rank}
                  </span>
                ) : (
                  <span className="file-icon">▤</span>
                )}
                <span className="task-name">
                  {t.name}
                  <small>
                    {t.status === "queued"
                      ? `${t.queue_channel === "cloud" ? "等待云端" : "等待转录"} · 前方 ${t.queue_ahead ?? 0} 个`
                      : statuses[t.status] ?? t.status}
                    {t.status === "running"
                      ? " · " +
                      (["polish", "translate", "polish_translate", "transcribe"].includes(t.phase) && t.params?.progress_detail
                        ? `${t.params.progress_detail.completed}/${t.params.progress_detail.total} ${t.params.progress_detail.unit}` +
                        (t.params.progress_detail.active_requests !== undefined
                          ? ` · 请求 ${t.params.progress_detail.active_requests} · 并发 ${t.params.progress_detail.concurrency}/${t.params.progress_detail.concurrency_ceiling}`
                          : ` · ${Math.round(t.progress)}%`)
                        : `${Math.round(t.progress)}%`)
                      : ""}
                  </small>
                </span>
              </button>
              <button
                type="button"
                className="task-item-delete"
                title={`删除任务 ${t.name}`}
                aria-label={`删除任务 ${t.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  void deleteTask(t.id);
                }}
              >
                ×
              </button>
            </div>
          );
        })}
        {!tasks.length && (
          <p className="sidebar-empty">
            导入一个视频或音频，
            <br />
            开始整理与翻译字幕。
          </p>
        )}
      </nav>
    </aside>
  );
}
