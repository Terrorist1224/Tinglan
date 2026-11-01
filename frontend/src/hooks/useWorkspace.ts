import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Params, Segment, Settings, Task } from "../types";

// App 只调用一次；子组件通过 props 共用这份状态，避免重复轮询和互相独立的草稿。
export function useWorkspace() {
  const [tasks, setTasks] = useState<Task[]>([]),
    [task, setTask] = useState<Task | null>(null),
    [draft, setDraft] = useState<Segment | null>(null),
    [dirty, setDirty] = useState(false),
    [saving, setSaving] = useState(false),
    [saveError, setSaveError] = useState(""),
    [current, setCurrent] = useState(0),
    [follow, setFollow] = useState(true),
    [displayMode, setDisplayMode] = useState<
      "bilingual" | "source" | "translation"
    >("bilingual"),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all"),
    [format, setFormat] = useState("srt"),
    [mode, setMode] = useState("bilingual"),
    [checked, setChecked] = useState<string[]>([]),
    [settingsOpen, setSettingsOpen] = useState(false),
    [taskSettingsOpen, setTaskSettingsOpen] = useState(false),
    [importOpen, setImportOpen] = useState(false),
    [importPath, setImportPath] = useState(""),
    [message, setMessage] = useState(""),
    [disconnected, setDisconnected] = useState(false),
    [taskMissing, setTaskMissing] = useState(false),
    [recovered, setRecovered] = useState(false),
    [uploading, setUploading] = useState(false),
    [importFailures, setImportFailures] = useState<string[]>([]),
    [singleLoading, setSingleLoading] = useState(false),
    [isCompact, setIsCompact] = useState(() =>
      typeof window !== "undefined"
        ? window.innerWidth < 1100 || window.innerHeight < 680
        : false,
    ),
    [activeTab, setActiveTab] = useState<"preview" | "edit" | "subtitles">("subtitles");

  useEffect(() => {
    function checkSize() {
      setIsCompact(window.innerWidth < 1100 || window.innerHeight < 680);
    }
    checkSize();
    window.addEventListener("resize", checkSize);
    return () => window.removeEventListener("resize", checkSize);
  }, []);

  // 异步回调从 ref 读取最新值；修改草稿时同步更新 state/ref，避免闭包读到旧编辑。
  const video = useRef<HTMLVideoElement>(null),
    draftRef = useRef<Segment | null>(null),
    dirtyRef = useRef(false),
    taskRef = useRef<Task | null>(null),
    savingRef = useRef(false),
    errorRef = useRef(""),
    toastTimer = useRef<number | null>(null),
    disconnectedRef = useRef(false),
    loadingTasksRef = useRef(false),
    loadingDetailRef = useRef(false),
    listFailedRef = useRef(false),
    detailFailedRef = useRef(false),
    selectionRef = useRef(0);

  function notify(text: string) {
    setMessage(text);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setMessage(""), 6000);
  }

  useEffect(() => {
    if (!dirty) return;
    const timer = setTimeout(() => {
      void save();
    }, 500);
    return () => clearTimeout(timer);
  }, [draft, dirty]);

  function updateDraft(s: Segment | null, isDirty = false) {
    setDraft(s);
    draftRef.current = s;
    setDirty(isDirty);
    dirtyRef.current = isDirty;
  }

  function change(fields: Partial<Segment>) {
    if (!draftRef.current) return;
    updateDraft({ ...draftRef.current, ...fields }, true);
  }

  async function save(retryFailed = false): Promise<boolean> {
    if (savingRef.current) return false;
    if (!draftRef.current || !taskRef.current) return true;
    if (!dirtyRef.current && !errorRef.current) return true;
    // 保存失败后保留草稿并等待显式重试，避免轮询恢复时重复提交旧修改。
    if (errorRef.current && !retryFailed) {
      setDirty(false);
      dirtyRef.current = false;
      return false;
    }
    if (retryFailed) {
      setSaveError("");
      errorRef.current = "";
    }
    const original = draftRef.current;
    const before = { ...original };
    savingRef.current = true;
    setSaving(true);
    try {
      const saved = await api<Segment>(
        `/tasks/${taskRef.current.id}/subtitles/${before.id}`,
        "PATCH",
        before,
      );
      // 请求期间可能继续输入；新草稿只接收服务器版本号，不能被旧响应覆盖。
      if (draftRef.current === original) updateDraft(saved, false);
      else if (draftRef.current?.id === saved.id) {
        updateDraft({ ...draftRef.current, version: saved.version }, true);
      }
      setSaveError("");
      errorRef.current = "";
      return true;
    } catch (e) {
      const text = (e as Error).message;
      setSaveError(text);
      errorRef.current = text;
      setDirty(false);
      dirtyRef.current = false;
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  async function refresh(): Promise<boolean> {
    const selected = taskRef.current;
    if (!selected || loadingDetailRef.current) return false;
    const token = selectionRef.current;
    loadingDetailRef.current = true;
    loadingTasksRef.current = true;
    try {
      const value = await api<Task>(
        `/tasks/${selected.id}`,
        "GET",
        undefined,
        AbortSignal.timeout(5000),
      );
      // 选择序号让切换前发出的慢响应失效，防止旧任务覆盖当前任务。
      if (token !== selectionRef.current || taskRef.current?.id !== selected.id) return true;
      detailFailedRef.current = false;
      setTaskMissing(false);
      setTask(value);
      taskRef.current = value;
      // 失败草稿即使 dirty 已复位也要保护，不能被后台轮询替换。
      if (!dirtyRef.current && !errorRef.current) {
        const active = draftRef.current;
        const row = active
          ? value.subtitles.find((item) => item.id === active.id) ?? active
          : value.subtitles[0] ?? null;
        updateDraft(row, false);
      }
      return true;
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status === 404) {
        detailFailedRef.current = false;
        setTaskMissing(true);
        return true;
      }
      if (status && status < 500) throw e;
      detailFailedRef.current = true;
      setDisconnected(true);
      disconnectedRef.current = true;
      return false;
    } finally {
      loadingDetailRef.current = false;
      loadingTasksRef.current = false;
    }
  }

  async function selectTask(id: string) {
    if (dirtyRef.current && !(await save())) return;
    const token = ++selectionRef.current;
    try {
      const value = await api<Task>(
        `/tasks/${id}`,
        "GET",
        undefined,
        AbortSignal.timeout(5000),
      );
      if (token !== selectionRef.current) return;
      taskRef.current = value;
      setTask(value);
      setTaskMissing(false);
      updateDraft(value.subtitles[0] ?? null, false);
      setChecked([]);
      setSaveError("");
      errorRef.current = "";
      if (video.current) video.current.currentTime = 0;
      setActiveTab("subtitles");
    } catch (e) {
      notify((e as Error).message);
    }
  }

  async function selectSegment(row: Segment, seek = true) {
    if (dirtyRef.current && !(await save())) return;
    updateDraft(row, false);
    setSaveError("");
    if (seek && video.current) video.current.currentTime = row.start / 1000;
    if (window.innerWidth < 1100 || window.innerHeight < 680) {
      setActiveTab("edit");
    }
  }

  async function importFiles(files: File[]) {
    if (!files.length) return { ok: 0, failed: [] };
    if (dirtyRef.current && !(await save())) return { ok: 0, failed: ["当前字幕尚未保存"] };
    setUploading(true);
    setImportFailures([]);
    const defaults = await api<Settings>("/settings");
    const created: Task[] = [];
    const failed: string[] = [];
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      form.append("default_diarize", String(defaults.default_diarize));
      form.append(
        "default_auto_polish_translate",
        String(defaults.default_auto_polish_translate),
      );
      try {
        created.push(await api<Task>("/tasks", "POST", form));
      } catch (e) {
        failed.push(`${file.name}：${(e as Error).message}`);
      }
    }
    setUploading(false);
    setImportFailures(failed);
    await loadTasks();
    if (created.length) {
      await selectTask(created[0].id);
      notify(`已导入 ${created.length} 个任务${failed.length ? `，${failed.length} 个失败` : ""}`);
    }
    return { ok: created.length, failed };
  }

  async function pickFiles() {
    try {
      const result = await api<{ path: string; paths?: string[] }>(
        "/tasks/pick-file",
        "POST",
        { multiple: true },
      );
      const paths = result.paths?.length ? result.paths : result.path ? [result.path] : [];
      if (!paths.length) return;
      setUploading(true);
      setImportFailures([]);
      const defaults = await api<Settings>("/settings");
      const created: Task[] = [];
      const failed: string[] = [];
      for (const path of paths) {
        try {
          const task = await api<Task>("/tasks/import-path", "POST", {
            path,
            default_diarize: defaults.default_diarize,
            default_auto_polish_translate: defaults.default_auto_polish_translate,
          });
          created.push(task);
        } catch (e) {
          failed.push(`${path}：${(e as Error).message}`);
        }
      }
      setUploading(false);
      setImportFailures(failed);
      await loadTasks();
      if (created.length) {
        await selectTask(created[0].id);
        notify(`已导入 ${created.length} 个任务${failed.length ? `，${failed.length} 个失败` : ""}`);
      }
    } catch (e) {
      setUploading(false);
      notify((e as Error).message);
    }
  }

  async function importPathFile() {
    const path = importPath.trim();
    if (!path) return;
    setUploading(true);
    try {
      const defaults = await api<Settings>("/settings");
      const created = await api<Task>(
        `/tasks/import-path?default_diarize=${defaults.default_diarize}&default_auto_polish_translate=${defaults.default_auto_polish_translate}`,
        "POST",
        { path },
      );
      setImportPath("");
      await loadTasks();
      await selectTask(created.id);
      setImportOpen(false);
      notify("导入完成");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setUploading(false);
    }
  }

  async function run(
    phase: string,
    ids?: string[],
    overwrite = false,
    resume = false,
  ) {
    if (!task) return;
    if (dirtyRef.current && !(await save())) return;
    try {
      await api(`/tasks/${task.id}/run`, "POST", {
        phase,
        ids,
        overwrite,
        resume,
      });
      await refresh();
      await loadTasks();
    } catch (e) {
      notify((e as Error).message);
    }
  }

  async function setParams(p: Partial<Params>) {
    if (!task) return;
    if (dirtyRef.current && !(await save())) return;
    const body: Params = { ...task.params, ...p };
    try {
      const updated = await api<Task>(`/tasks/${task.id}/params`, "PUT", body);
      taskRef.current = updated;
      setTask(updated);
    } catch (e) {
      notify((e as Error).message);
    }
  }

  async function deleteTask(id: string) {
    const t = tasks.find((x) => x.id === id);
    const targetName = t ? t.name : (task?.id === id ? task.name : "当前任务");
    const isTargetRunning = t ? t.status === "running" : (task?.id === id && task.status === "running");
    if (isTargetRunning) {
      notify("请先暂停或取消正在运行的任务，再执行删除");
      return;
    }
    if (
      !window.confirm(
        `确定要删除任务“${targetName}”吗？\n任务缓存与所有字幕记录将彻底删除且无法恢复；直接导入的外置源文件不会被删除。`,
      )
    ) {
      return;
    }
    try {
      await api(`/tasks/${id}`, "DELETE");
      notify("任务已删除");
      if (task?.id === id) {
        taskRef.current = null;
        setTask(null);
        updateDraft(null, false);
        setChecked([]);
      }
      await loadTasks();
    } catch (e) {
      notify("删除失败: " + (e as Error).message);
    }
  }

  async function translateCurrent() {
    if (!draft || !task) return;
    if (dirtyRef.current) await save();
    setSingleLoading(true);
    try {
      const updated = await api<Segment>(
        `/tasks/${task.id}/subtitles/${draft.id}/translate`,
        "POST",
      );
      updateDraft(updated, false);
      notify("翻译已完成");
      await refresh();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setSingleLoading(false);
    }
  }

  async function polishCurrent() {
    if (!draft || !task) return;
    if (dirtyRef.current) await save();
    setSingleLoading(true);
    try {
      const updated = await api<Segment>(
        `/tasks/${task.id}/subtitles/${draft.id}/polish`,
        "POST",
      );
      updateDraft(updated, false);
      notify("已完成润色原文");
      await refresh();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setSingleLoading(false);
    }
  }

  async function revertPolish() {
    if (!task) return;
    try {
      const res = await api<{ ok: boolean; reverted: number; skipped: number }>(
        `/tasks/${task.id}/revert-polish`,
        "POST",
      );
      notify(`已撤回 ${res.reverted} 句润色${res.skipped ? `，跳过 ${res.skipped} 句后续修改` : ""}`);
      await refresh();
    } catch (e) {
      notify((e as Error).message);
    }
  }

  async function polishAndTranslateCurrent() {
    if (!draft || !task) return;
    if (dirtyRef.current) await save();
    setSingleLoading(true);
    try {
      const updated = await api<Segment>(
        `/tasks/${task.id}/subtitles/${draft.id}/polish-and-translate`,
        "POST",
      );
      updateDraft(updated, false);
      notify("已完成润色并翻译");
      await refresh();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setSingleLoading(false);
    }
  }

  // 每轮先取列表再取当前详情；请求未结束时跳过下一轮，避免断线积压。
  async function loadTasks() {
    if (loadingTasksRef.current) return;
    loadingTasksRef.current = true;
    const token = selectionRef.current;
    try {
      const list = await api<Task[]>(
        "/tasks",
        "GET",
        undefined,
        AbortSignal.timeout(5000),
      );
      if (token === selectionRef.current) setTasks(list);
      listFailedRef.current = false;
      loadingTasksRef.current = false;
      if (taskRef.current) await refresh();
      if (!listFailedRef.current && !detailFailedRef.current) {
        if (disconnectedRef.current) {
          setRecovered(true);
          window.setTimeout(() => setRecovered(false), 3000);
        }
        disconnectedRef.current = false;
        setDisconnected(false);
      }
    } catch (e) {
      const status = (e as { status?: number }).status;
      const timeout = (e as { timeout?: boolean }).timeout;
      if (status === 404) {
        setTaskMissing(true);
      } else if (timeout || !status || status >= 500) {
        listFailedRef.current = true;
        setDisconnected(true);
        disconnectedRef.current = true;
      }
      loadingTasksRef.current = false;
    }
  }


  useEffect(() => {
    loadTasks();
    const timer = setInterval(() => {
      loadTasks();
    }, 1500);
    return () => clearInterval(timer);
  }, []);

  function timeUpdate() {
    if (!video.current) return;
    // 媒体元素使用秒，接口与字幕状态统一使用毫秒。
    const now = video.current.currentTime * 1000;
    setCurrent(now);
    if (follow) {
      const id = taskRef.current?.subtitles.find(
        (r) => r.start <= now && r.end > now,
      )?.id;
      if (id) {
        document
          .getElementById("row-" + id)
          ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  }

  async function exportFile() {
    if (!task) return;
    if (dirtyRef.current && !(await save())) return;
    const incomplete = (task.subtitles ?? []).filter(
      (r) =>
        (!r.translation.trim() || r.translated_version !== r.source_version) &&
        format !== "json",
    ).length;
    try {
      const response = await fetch(
        `/api/tasks/${task.id}/export?fmt=${format}&mode=${mode}`,
      );
      if (!response.ok) {
        const error = await response.json();
        if (error.subtitle_id && taskRef.current?.id === task.id) {
          await refresh();
          const row = taskRef.current?.subtitles.find(
            (r) => r.id === error.subtitle_id,
          );
          if (row) {
            setQuery("");
            setFilter(error.reason === "translation" ? "untranslated" : "all");
            await selectSegment(row);
            error.detail += "，已定位至该字幕。";
            requestAnimationFrame(() =>
              document
                .getElementById("row-" + row.id)
                ?.scrollIntoView({ block: "nearest" }),
            );
          }
        }
        throw new Error(error.detail);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = task.name.replace(/\.[^.]+$/, "") + "." + format;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      if (mode !== "source" && format !== "json" && incomplete > 0) {
        notify(`已导出，${incomplete} 句未完成，已用原文补位。`);
      }
    } catch (e) {
      notify((e as Error).message);
    }
  }

  const rows = task?.subtitles ?? [],
    visible = rows.filter((r) => {
      const matchQuery =
        !query ||
        (r.source + " " + r.translation).toLowerCase().includes(query.toLowerCase());
      if (!matchQuery) return false;

      const review = r.review || "";
      const isUntranslated = !r.translation.trim() || r.translated_version !== r.source_version;
      const isPolishRefused = review.includes("润色被模型拒绝") || review.includes("润色跳过");
      const isRestricted = review.includes("模型拦截") || review.includes("策略拒绝");
      const isTimeIssue = r.end <= r.start || (r.end - r.start) < 200;
      const isSpeakerReview = review.includes("跨说话人") || review.includes("未匹配到说话人");
      const isBoundaryReview = review.includes("分块交界") || review.includes("分块边界可能重复");
      const isLowConfidence = review.includes("需检查");
      const isManualEdited = r.manual_translation === 1 || r.alignment === "manual";

      switch (filter) {
        case "untranslated":
          return isUntranslated;
        case "polish_refused":
          return isPolishRefused;
        case "restricted":
          return isRestricted;
        case "time_issue":
          return isTimeIssue;
        case "speaker_review":
          return isSpeakerReview;
        case "boundary_review":
          return isBoundaryReview;
        case "low_confidence":
          return isLowConfidence;
        case "manual_edited":
          return isManualEdited;
        case "all":
        default:
          return true;
      }
    });

  const activeRows = rows
    .filter((r) => r.start <= current && r.end > current)
    .sort((a, b) => a.start - b.start || a.id.localeCompare(b.id));

  const running = task?.status === "running";
  const queued = task?.status === "queued";
  const busy = running || queued;
  const queueLabel = queued
    ? `${task.queue_channel === "cloud" ? "等待云端" : "等待转录"} · 前方 ${task.queue_ahead ?? 0} 个任务`
    : "";
  const runningLabel =
    task?.status === "running" &&
      (task.phase === "transcribe" || task.phase === "diarize")
      ? "正在转录"
      : "";
  const progressDetail = task?.params?.progress_detail;
  const showProgress =
    running &&
    progressDetail &&
    ["polish", "translate", "polish_translate", "transcribe"].includes(
      task.phase,
    );

  const progressText = showProgress ? [
    `进度 ${progressDetail.completed}/${progressDetail.total}`,
    [
      progressDetail.restricted !== undefined && task.phase !== "polish"
        ? `受限${progressDetail.restricted}句` : "",
      progressDetail.concurrency !== undefined && progressDetail.concurrency_ceiling !== undefined
        ? `并发${progressDetail.concurrency}/${progressDetail.concurrency_ceiling}` : "",
    ].filter(Boolean).join(" | "),
  ].filter(Boolean).join(" | ") : "";

  return {
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
  };
}

export type Workspace = ReturnType<typeof useWorkspace>;
