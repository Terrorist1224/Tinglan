import { api } from "../api";
import { phases, statuses } from "../constants";
import type { Workspace } from "../hooks/useWorkspace";

type Props = Pick<Workspace, "task" | "setTaskSettingsOpen" | "message" | "notify" | "run" | "deleteTask" | "loadTasks" | "running" | "queued" | "busy" | "queueLabel" | "runningLabel" | "showProgress" | "progressText">;

export default function TaskStatus({
  task,
  setTaskSettingsOpen,
  message,
  notify,
  run,
  deleteTask,
  loadTasks,
  running,
  queued,
  busy,
  queueLabel,
  runningLabel,
  showProgress,
  progressText,
}: Props) {
  if (!task) return null;
  return (
    <section className="task-strip">
      <div>
        <span className={"status-dot " + (busy ? "busy" : "")} />
        <strong>
          {queueLabel || runningLabel || statuses[task.status] || task.status}
        </strong>
        {!queueLabel && !runningLabel && (
          <span>{phases[task.phase] ?? "准备就绪"}</span>
        )}
        {running && <span>{Math.round(task.progress)}%</span>}
      </div>
      {showProgress && (
        <div
          className="progress-detail"
          role="status"
          aria-label="任务处理详情"
          tabIndex={0}
          title={progressText}
        >
          <span>{progressText}</span>
        </div>
      )}
      <div className="actions">
        {busy ? (
          <>
            <button
              onClick={() =>
                api(`/tasks/${task.id}/control/pause`, "POST")
                  .then(() => notify(queued ? "已退出队列" : "暂停中…"))
                  .catch((e) => notify(e.message))
                  .then(() => loadTasks())
              }
            >
              {queued ? "退出队列" : "暂停"}
            </button>
            <button
              onClick={() =>
                api(`/tasks/${task.id}/control/cancel`, "POST")
                  .then(() => notify(queued ? "已取消排队" : "取消中…"))
                  .catch((e) => notify(e.message))
                  .then(() => loadTasks())
              }
            >
              取消
            </button>
          </>
        ) : (
          <>
            {["paused", "interrupted", "failed", "cancelled"].includes(
              task.status,
            ) ? (
              <>
                <button
                  className="primary"
                  onClick={() =>
                    void run(
                      task.phase || "transcribe",
                      undefined,
                      false,
                      true,
                    )
                  }
                >
                  恢复处理
                </button>
                <button
                  className="task-restart-btn"
                  onClick={() => {
                    if (
                      window.confirm(
                        "确定要重新从0开始语音识别吗？\n当前已生成的字幕和检查点将被清空。",
                      )
                    ) {
                      void run("transcribe", undefined, true, false);
                    }
                  }}
                >
                  重新转录
                </button>
              </>
            ) : task.status === "complete" ? (
              <button
                className="task-restart-btn"
                onClick={() => {
                  if (
                    window.confirm(
                      "确定要重新从0开始语音识别吗？\n当前已生成的字幕和检查点将被清空。",
                    )
                  ) {
                    void run("transcribe", undefined, true, false);
                  }
                }}
              >
                重新转录
              </button>
            ) : (
              <button
                className="primary"
                onClick={() =>
                  void run("transcribe", undefined, false, false)
                }
              >
                开始语音识别
              </button>
            )}
            <button
              className="task-settings-btn"
              onClick={() => setTaskSettingsOpen(true)}
            >
              任务设置与提示词 ⚙
            </button>
            <button
              className="task-delete-btn"
              onClick={() => void deleteTask(task.id)}
              title="删除当前任务及关联字幕；不删除外置源文件"
            >
              删除任务
            </button>
          </>
        )}
      </div>
      {busy && (
        <div className="progress">
          <i style={{ width: task.progress + "%" }} />
        </div>
      )}
    </section>
  );
}
