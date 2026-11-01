import ImportModal from "./components/ImportModal";
import MediaPreview from "./components/MediaPreview";
import SettingsPanel from "./components/SettingsPanel";
import SubtitleActions from "./components/SubtitleActions";
import SubtitleEditor from "./components/SubtitleEditor";
import SubtitleList from "./components/SubtitleList";
import TaskSettingsModal from "./components/TaskSettingsModal";
import TaskSidebar from "./components/TaskSidebar";
import TaskStatus from "./components/TaskStatus";
import { useWorkspace } from "./hooks/useWorkspace";

export default function App() {
  const {
    tasks,
    task,
    draft,
    dirty,
    saving,
    saveError,
    displayMode,
    setDisplayMode,
    query,
    setQuery,
    filter,
    setFilter,
    format,
    setFormat,
    mode,
    setMode,
    checked,
    setChecked,
    settingsOpen,
    setSettingsOpen,
    taskSettingsOpen,
    setTaskSettingsOpen,
    importOpen,
    setImportOpen,
    importPath,
    setImportPath,
    message,
    setMessage,
    disconnected,
    taskMissing,
    recovered,
    uploading,
    importFailures,
    singleLoading,
    isCompact,
    activeTab,
    setActiveTab,
    video,
    notify,
    change,
    save,
    selectTask,
    selectSegment,
    importFiles,
    pickFiles,
    importPathFile,
    run,
    setParams,
    deleteTask,
    translateCurrent,
    polishCurrent,
    revertPolish,
    polishAndTranslateCurrent,
    loadTasks,
    timeUpdate,
    exportFile,
    rows,
    visible,
    activeRows,
    running,
    queued,
    busy,
    queueLabel,
    runningLabel,
    progressDetail,
    showProgress,
    progressText,
  } = useWorkspace();
  return (
    <div className="app">
      {recovered && <div className="connection-status" role="status">连接已恢复</div>}
      {(disconnected || taskMissing) && (
        <div className="connection-status" role="status" aria-live="polite">
          {disconnected ? "无法连接后端或获取最新状态，正在重试。当前显示的是最后一次获取的进度。" : "任务已不存在"}
        </div>
      )}
      <TaskSidebar tasks={tasks} task={task} setImportOpen={setImportOpen} uploading={uploading} selectTask={selectTask} deleteTask={deleteTask} />

      <main
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const files = Array.from(e.dataTransfer.files);
          if (files.length) {
            setImportOpen(true);
            void importFiles(files);
          }
        }}
      >
        <header className="topbar">
          <div>
            <div className="eyebrow">听澜字幕工作流</div>
            <h1>{task ? task.name : "你的本地字幕工作台"}</h1>
          </div>
          <div className="top-actions">
            <button onClick={() => setSettingsOpen(true)}>全局 API 设置 ↗</button>
          </div>
        </header>

        {!task ? (
          <section className="welcome">
            <div className="wave-art" aria-hidden="true">
              {Array.from({ length: 33 }, (_, i) => (
                <i
                  key={i}
                  style={{
                    height: 20 + Math.abs(Math.sin(i * 0.7)) * 80 + "px",
                  }}
                />
              ))}
            </div>
            <div className="eyebrow">从一个媒体文件开始</div>
            <h2>听清原文，译好每一句。</h2>
            <p>
              Whisper 本地识别 → 检查与润色 → 注入提示词翻译 → 导出字幕。
            </p>
            <button className="primary" onClick={() => setImportOpen(true)}>
              选择视频或音频
            </button>
          </section>
        ) : (
          <>
            <TaskStatus task={task} setTaskSettingsOpen={setTaskSettingsOpen} message={message} notify={notify} run={run} deleteTask={deleteTask} loadTasks={loadTasks} running={running} queued={queued} busy={busy} queueLabel={queueLabel} runningLabel={runningLabel} showProgress={showProgress} progressText={progressText} />

            {task.status === "complete" && !!progressDetail?.restricted && (
              <div className="notice compact-notice" role="status">
                <div>已完成自动翻译</div>
                <div>有 {progressDetail.restricted} 句需要人工翻译</div>
              </div>
            )}
            {task.error && (
              <details
                className={
                  "notice compact-notice " + (task.status === "failed" ? "error" : "")
                }
              >
                <summary>
                  <span className="notice-icon">⚠</span>{" "}
                  {task.error.length > 70
                    ? task.error.slice(0, 70) + "… (点击展开详细说明)"
                    : task.error}
                </summary>
                <p>{task.error}</p>
              </details>
            )}

            {isCompact && (
              <nav className="mobile-tabs" aria-label="工作区视图切换">
                <button
                  type="button"
                  className={activeTab === "preview" ? "active" : ""}
                  onClick={() => setActiveTab("preview")}
                >
                  预览
                </button>
                <button
                  type="button"
                  className={activeTab === "edit" ? "active" : ""}
                  onClick={() => setActiveTab("edit")}
                >
                  编辑 {dirty ? "•" : ""}
                </button>
                <button
                  type="button"
                  className={activeTab === "subtitles" ? "active" : ""}
                  onClick={() => setActiveTab("subtitles")}
                >
                  字幕 ({rows.length})
                </button>
              </nav>
            )}

            <div className={`workspace ${isCompact ? "compact-tab-mode" : ""}`}>
              {/* 左侧栏：媒体预览 + 片段编辑 */}
              {(!isCompact || activeTab === "preview" || activeTab === "edit") && (
                <section
                  className={`media-column ${isCompact && activeTab !== "preview" && activeTab !== "edit" ? "compact-hidden" : ""}`}
                >
                  {(!isCompact || activeTab === "preview") && (
                    <MediaPreview task={task} displayMode={displayMode} setDisplayMode={setDisplayMode} video={video} notify={notify} timeUpdate={timeUpdate} activeRows={activeRows} />
                  )}

                  {/* 片段编辑功能 */}
                  {(!isCompact || activeTab === "edit") && (
                    <SubtitleEditor task={task} draft={draft} dirty={dirty} saving={saving} saveError={saveError} singleLoading={singleLoading} change={change} save={save} translateCurrent={translateCurrent} polishCurrent={polishCurrent} polishAndTranslateCurrent={polishAndTranslateCurrent} running={running} />
                  )}
                </section>
              )}

              {/* 右侧栏：字幕稿列表 + 批量处理 */}
              {(!isCompact || activeTab === "subtitles") && (
                <section className="subtitle-column">
                  <div className="section-heading subtitle-header">
                    <h2>字幕稿</h2>
                    <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                      <select
                        aria-label="筛选字幕"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        style={{ width: "auto", padding: "4px 8px", fontSize: "12px" }}
                      >
                        <option value="all">全部</option>
                        <option value="untranslated">未完成（未翻译 / 译文过期）</option>
                        <option value="polish_refused">润色被拒（原文直译）</option>
                        <option value="restricted">模型拦截（需人工翻译）</option>
                        <option value="time_issue">时间轴异常（起止相同）</option>
                        <option value="speaker_review">说话人待校对</option>
                        <option value="boundary_review">边界交界 / 疑似重复</option>
                        <option value="low_confidence">语音需检查（低置信度）</option>
                        <option value="manual_edited">人工已编辑</option>
                      </select>
                      <input
                        aria-label="搜索字幕与术语"
                        style={{ width: "150px", padding: "4px 8px", fontSize: "12px" }}
                        placeholder="搜索字幕与术语…"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                      <span>共 {rows.length} 句</span>
                    </div>
                  </div>

                  <SubtitleList task={task} draft={draft} filter={filter} checked={checked} setChecked={setChecked} selectSegment={selectSegment} rows={rows} visible={visible} activeRows={activeRows} />

                  {/* 列表下方批量处理与导出 */}
                  <SubtitleActions task={task} format={format} setFormat={setFormat} mode={mode} setMode={setMode} checked={checked} setChecked={setChecked} run={run} revertPolish={revertPolish} exportFile={exportFile} rows={rows} visible={visible} running={running} />
                </section>
              )}
            </div>
          </>
        )}

        {importOpen && (
          <ImportModal
            close={() => setImportOpen(false)}
            importFiles={importFiles}
            uploading={uploading}
            pickFiles={pickFiles}
            importPath={importPath}
            setImportPath={setImportPath}
            applyPath={importPathFile}
            failures={importFailures}
          />
        )}

        {/* 弹窗：全局偏好设置 */}
        {settingsOpen && (
          <SettingsPanel
            close={() => setSettingsOpen(false)}
            notify={notify}
          />
        )}

        {/* 弹窗：当前任务设置与提示词 */}
        {taskSettingsOpen && task && (
          <TaskSettingsModal
            task={task}
            close={() => setTaskSettingsOpen(false)}
            setParams={setParams}
            notify={notify}
            deleteTask={deleteTask}
          />
        )}



        {message && (
          <div className="toast" role="status">
            <span>{message}</span>
            <button onClick={() => setMessage("")} aria-label="关闭提示">
              ×
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
