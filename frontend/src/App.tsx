import { useEffect, useRef, useState } from "react";
import {
  api,
  phases,
  statuses,
  time,
  DEFAULT_POLISH_PROMPT,
  DEFAULT_TRANSLATE_PROMPT,
} from "./types";
import type { Params, Segment, Settings, System, Task } from "./types";

function SettingsPanel({
  close,
  notify,
}: {
  close: () => void;
  notify: (s: string) => void;
}) {
  const [settings, setSettings] = useState<Settings | null>(null),
    [key, setKey] = useState(""),
    [system, setSystem] = useState<System | null>(null),
    [working, setWorking] = useState(false),
    [modelToken, setModelToken] = useState(""),
    [importPath, setImportPath] = useState(""),
    [modelList, setModelList] = useState<string[]>([]),
    [fetchingModels, setFetchingModels] = useState(false);

  useEffect(() => {
    let active = true;
    api<Settings>("/settings")
      .then((v) => {
        if (!active) return;
        setSettings(v);
        setKey(v.key || "");
      })
      .catch((e) => notify(e.message));
    const update = () =>
      api<System>("/system")
        .then((v) => active && setSystem(v))
        .catch(() => {});
    update();
    const id = setInterval(update, 2500);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  async function save() {
    if (!settings) return;
    setWorking(true);
    try {
      const s = await api<Settings & { notice: string }>("/settings", "PUT", {
        ...settings,
        key,
      });
      setSettings(s);
      notify(s.notice || "设置与 API 密钥已保存到本地");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setWorking(false);
    }
  }

  async function fetchModels() {
    if (!settings?.base_url?.trim()) {
      notify("请先填写基础地址");
      return;
    }
    setFetchingModels(true);
    try {
      const res = await api<{ models: string[] }>("/settings/models", "POST", {
        base_url: settings.base_url,
        key,
      });
      setModelList(res.models);
      notify(`成功获取 ${res.models.length} 个模型`);
      if (res.models.length > 0 && !settings.model) {
        setSettings({ ...settings, model: res.models[0] });
      }
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setFetchingModels(false);
    }
  }

  return (
    <div className="scrim" onClick={close}>
      <section
        className="settings modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <div>
            <div className="eyebrow">全局偏好设置</div>
            <h2 id="settings-title">模型与 API 设置</h2>
          </div>
          <button onClick={close} aria-label="关闭设置">
            关闭
          </button>
        </header>
        <p className="muted">{system?.privacy}</p>
        {settings && (
          <>
            <h3>新任务默认设置</h3>
            <div className="default-switches">
              <label className="switch-toggle preference-toggle">
                <input
                  type="checkbox"
                  role="switch"
                  aria-label="新任务默认分人"
                  checked={settings.default_diarize}
                  onChange={(e) =>
                    setSettings({ ...settings, default_diarize: e.target.checked })
                  }
                />
                <span>
                  <strong>新任务默认分人</strong>
                  <small>转录完成后自动识别说话人</small>
                </span>
              </label>
              <label className="switch-toggle preference-toggle">
                <input
                  type="checkbox"
                  role="switch"
                  aria-label="新任务转录后自动润色并翻译"
                  checked={settings.default_auto_polish_translate}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      default_auto_polish_translate: e.target.checked,
                    })
                  }
                />
                <span>
                  <strong>新任务转录后自动润色并翻译</strong>
                  <small>会自动调用云端 API，可能产生费用</small>
                </span>
              </label>
            </div>
            <h3>云端 API</h3>
            <p className="muted">
              填写兼容 Chat Completions 的基础地址（通常含 /v1）。密钥保存在本地，以纯文本明文显示。
            </p>
            <label>
              基础地址
              <input
                aria-label="基础地址"
                value={settings.base_url}
                placeholder="https://api.example.com/v1"
                onChange={(e) =>
                  setSettings({ ...settings, base_url: e.target.value })
                }
              />
            </label>
            <label>
              API 密钥
              <input
                aria-label="API 密钥"
                type="text"
                autoComplete="off"
                value={key}
                placeholder="输入 API 密钥 (例如 sk-...)"
                onChange={(e) => setKey(e.target.value)}
              />
            </label>
            <div className="two">
              <label>
                模型 ID
                {modelList.length > 0 ? (
                  <div style={{ display: "flex", gap: "6px", marginTop: "5px" }}>
                    <select
                      aria-label="选择模型"
                      value={settings.model}
                      onChange={(e) =>
                        setSettings({ ...settings, model: e.target.value })
                      }
                      style={{ flex: 1 }}
                    >
                      <option value="" disabled>
                        -- 选择模型 --
                      </option>
                      {settings.model && !modelList.includes(settings.model) && (
                        <option value={settings.model}>
                          {settings.model} (当前)
                        </option>
                      )}
                      {modelList.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                    <input
                      aria-label="模型 ID"
                      style={{ flex: 1 }}
                      placeholder="或手动输入"
                      value={settings.model}
                      onChange={(e) =>
                        setSettings({ ...settings, model: e.target.value })
                      }
                    />
                  </div>
                ) : (
                  <input
                    aria-label="模型 ID"
                    placeholder="如: gpt-4o 或点击下方获取列表"
                    value={settings.model}
                    onChange={(e) =>
                      setSettings({ ...settings, model: e.target.value })
                    }
                  />
                )}
              </label>
            </div>
            <div style={{ marginTop: "-4px", marginBottom: "8px" }}>
              <button
                type="button"
                className="btn-link"
                disabled={fetchingModels}
                onClick={fetchModels}
              >
                {fetchingModels ? "正在获取模型列表…" : "获取可用模型列表 ↻"}
              </button>
            </div>
            <div className="actions">
              <button className="primary" onClick={save} disabled={working}>
                保存设置
              </button>
              <button
                onClick={async () => {
                  try {
                    const s = await api<Settings & { notice: string }>(
                      "/settings",
                      "PUT",
                      {
                        ...settings,
                        clear_key: true,
                      },
                    );
                    setSettings(s);
                    setKey("");
                    notify(s.notice || "密钥已清除");
                  } catch (e) {
                    notify((e as Error).message);
                  }
                }}
              >
                清除密钥
              </button>
            </div>
          </>
        )}
        <h3>本地模型</h3>
        <p className="muted">
          剩余空间 {system?.disk_free_gb ?? "—"} GB。支持 Whisper large-v3 与 community-1 说话人识别。
        </p>
        <div className="model-list">
          {system?.models.map((m) => (
            <div className="model" key={m.id}>
              <div>
                <strong>{m.id}</strong>
                <small>
                  {m.repo} · 约 {m.size_gb} GB
                </small>
              </div>
              {m.gated && !m.ready && (
                <input
                  type="password"
                  placeholder="HF Token（受限模型授权）"
                  style={{
                    width: "160px",
                    fontSize: "11px",
                    padding: "4px 8px",
                  }}
                  value={modelToken}
                  onChange={(e) => setModelToken(e.target.value)}
                />
              )}
              <button
                disabled={system.download.status === "running"}
                onClick={async () => {
                  try {
                    await api("/models/download", "POST", {
                      model: m.id,
                      token: modelToken,
                    });
                    notify("已开始下载 " + m.id);
                  } catch (e) {
                    notify((e as Error).message);
                  }
                }}
              >
                {m.ready ? "重新校验 / 下载" : "下载模型"}
              </button>
              <span className={"tag " + (m.ready ? "good" : "")}>
                {m.ready ? "已就绪" : "未下载"}
              </span>
            </div>
          ))}
        </div>
        <div style={{ marginTop: "12px", display: "flex", gap: "8px" }}>
          <input
            placeholder="导入已下载的本地模型绝对目录（包含 config.yaml 等）"
            value={importPath}
            onChange={(e) => setImportPath(e.target.value)}
          />
          <button
            disabled={!importPath.trim()}
            onClick={async () => {
              try {
                await api("/models/import", "POST", {
                  model: "community-1",
                  path: importPath.trim(),
                });
                notify("已成功导入 community-1 模型");
                setImportPath("");
              } catch (err) {
                notify((err as Error).message);
              }
            }}
          >
            从本地导入
          </button>
        </div>
        <p role="status">
          {system?.download.status === "running"
            ? "正在下载 " +
              system.download.model +
              "，可关闭此窗口继续使用页面"
            : system?.download.error}
        </p>
      </section>
    </div>
  );
}

function ImportModal({
  close,
  importFiles,
  uploading,
  pickFiles,
  importPath,
  setImportPath,
  applyPath,
  failures,
}: {
  close: () => void;
  importFiles: (files: File[]) => Promise<{ ok: number; failed: string[] }>;
  uploading: boolean;
  pickFiles: () => Promise<void>;
  importPath: string;
  setImportPath: (value: string) => void;
  applyPath: () => Promise<void>;
  failures: string[];
}) {
  const choose = useRef<HTMLInputElement>(null);
  return (
    <div className="scrim" onClick={close}>
      <section
        className="import-modal modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-title"
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <div>
            <div className="eyebrow">导入媒体</div>
            <h2 id="import-title">选择本地文件</h2>
          </div>
          <button onClick={close}>关闭</button>
        </header>
        <div className="import-section">
          <div className="import-section-title">
            <strong>本地秒导</strong>
            <small>直接读取原文件，不复制、不占额外空间</small>
          </div>
          <button
            type="button"
            className="primary pick-file"
            disabled={uploading}
            onClick={() => void pickFiles()}
          >
            📂 浏览本地文件（可多选）…
          </button>
          <label>
            本地绝对路径
            <input
              value={importPath}
              placeholder={"例如 E:\\素材\\录音.mkv"}
              onChange={(e) => setImportPath(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && importPath.trim()) void applyPath();
              }}
            />
          </label>
          <button
            type="button"
            className="primary"
            disabled={uploading || !importPath.trim()}
            onClick={() => void applyPath()}
          >
            {uploading ? "正在读取媒体信息…" : "极速导入"}
          </button>
        </div>
        <div className="import-divider"><span>或</span></div>
        <div className="import-section">
          <div className="import-section-title">
            <strong>传统网页上传</strong>
            <small>可多选；导入后不会自动开始处理</small>
          </div>
          <button
            type="button"
            disabled={uploading}
            onClick={() => choose.current?.click()}
          >
            选择并上传文件（可多选）
          </button>
          <input
            ref={choose}
            name="media-upload"
            type="file"
            hidden
            multiple
            accept="video/*,audio/*,.mkv,.ts"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              if (files.length) void importFiles(files);
              e.target.value = "";
            }}
          />
          {failures.length > 0 && (
            <div className="import-failures" role="alert">
              <strong>以下文件导入失败</strong>
              <ul>
                {failures.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function TaskSettingsModal({
  task,
  close,
  setParams,
  notify,
  deleteTask,
}: {
  task: Task;
  close: () => void;
  setParams: (p: Partial<Params>) => Promise<void>;
  notify: (s: string) => void;
  deleteTask: (id: string) => Promise<void>;
}) {
  const [targetLang, setTargetLang] = useState(
    task.params.target_language ?? "简体中文",
  );
  const [polishPrompt, setPolishPrompt] = useState(
    task.params.polish_prompt ?? DEFAULT_POLISH_PROMPT,
  );
  const [translatePrompt, setTranslatePrompt] = useState(
    task.params.translate_prompt ?? DEFAULT_TRANSLATE_PROMPT,
  );
  const [audioTrack, setAudioTrack] = useState(task.params.audio_track);
  const [device, setDevice] = useState(task.params.device);
  const [diarize, setDiarize] = useState(task.params.diarize ?? true);
  const [autoPolishTranslate, setAutoPolishTranslate] = useState(
    task.params.auto_polish_translate ?? false,
  );
  const [numSpeakers, setNumSpeakers] = useState(
    task.params.num_speakers == null ? "" : String(task.params.num_speakers),
  );
  const [speakerNames, setSpeakerNames] = useState(
    task.params.speakers ?? {},
  );
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    setSaving(true);
    try {
      const count = numSpeakers ? Number(numSpeakers) : null;
      if (count !== null && (!Number.isInteger(count) || count < 1)) {
        notify("说话人数必须为正整数，留空表示自动检测");
        return;
      }
      const speakerEntries = Object.keys(speakerNames)
        .sort((a, b) => Number(a) - Number(b))
        .map((id) => [
          id,
          {
            name: speakerNames[id].name.trim() || `说话人 ${id}`,
            note: speakerNames[id].note.trim(),
          },
        ]);
      await setParams({
        target_language: targetLang,
        polish_prompt: polishPrompt,
        translate_prompt: translatePrompt,
        audio_track: audioTrack,
        device: device,
        diarize,
        auto_polish_translate: autoPolishTranslate,
        num_speakers: count,
        speakers: Object.fromEntries(speakerEntries) as Params["speakers"],
      });
      close();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="scrim" onClick={close}>
      <section
        className="settings modal"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <div>
            <div className="eyebrow">任务配置</div>
            <h2>任务设置与提示词</h2>
          </div>
          <button onClick={close}>关闭</button>
        </header>

        <label>
          目标语言（翻译输出语言，将注入翻译流程）
          <input
            value={targetLang}
            placeholder="例如: 简体中文、English、日本語"
            onChange={(e) => setTargetLang(e.target.value)}
          />
        </label>

        <label>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>润色提示词模板（润色原文）</span>
            <button
              type="button"
              className="btn-link"
              onClick={() => setPolishPrompt(DEFAULT_POLISH_PROMPT)}
            >
              恢复默认
            </button>
          </div>
          <textarea
            rows={3}
            value={polishPrompt}
            onChange={(e) => setPolishPrompt(e.target.value)}
          />
        </label>

        <label>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>翻译提示词模板（生成译文）</span>
            <button
              type="button"
              className="btn-link"
              onClick={() => setTranslatePrompt(DEFAULT_TRANSLATE_PROMPT)}
            >
              恢复默认
            </button>
          </div>
          <textarea
            rows={3}
            value={translatePrompt}
            onChange={(e) => setTranslatePrompt(e.target.value)}
          />
        </label>

        <div className="two">
          <label>
            音轨选择
            <select
              value={audioTrack}
              onChange={(e) => setAudioTrack(+e.target.value)}
            >
              {task.probe.audio.map((a) => (
                <option key={a.index} value={a.index}>
                  轨 {a.index} · {a.language} · {a.codec}
                  {a.default ? " · 默认" : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            运行设备
            <select value={device} onChange={(e) => setDevice(e.target.value)}>
              <option value="cuda">GPU · FP16</option>
              <option value="cpu">CPU · INT8</option>
            </select>
          </label>
        </div>

        <h3>自动处理</h3>
        <label className="switch-toggle preference-toggle">
          <input
            type="checkbox"
            role="switch"
            aria-label="转录后自动润色并翻译"
            checked={autoPolishTranslate}
            onChange={(e) => setAutoPolishTranslate(e.target.checked)}
          />
          <span>
            <strong>转录后自动润色并翻译</strong>
            <small>会调用云端 API，可能产生费用</small>
          </span>
        </label>

        <h3>自动分人与说话人</h3>
        <div className="two">
          <label className="switch-toggle" style={{ alignSelf: "end", marginBottom: "7px" }}>
            <input
              type="checkbox"
              role="switch"
              aria-label="转录后自动分人"
              checked={diarize}
              onChange={(e) => setDiarize(e.target.checked)}
            />
            <span>
              <strong>转录后自动分人</strong>
            </span>
          </label>
          <label>
            说话人数量（选填）
            <input
              aria-label="说话人数量"
              type="number"
              min="1"
              max="20"
              placeholder="留空表示自动估算"
              value={numSpeakers}
              disabled={!diarize}
              onChange={(e) => setNumSpeakers(e.target.value)}
            />
          </label>
        </div>
        {Object.keys(speakerNames).length > 0 ? (
          <div className="speaker-list">
            {Object.keys(speakerNames)
              .sort((a, b) => Number(a) - Number(b))
              .map((id) => (
                <div className="speaker-row" key={id}>
                  <strong>说话人 {id}</strong>
                  <input
                    aria-label={`说话人 ${id} 名称`}
                    value={speakerNames[id].name}
                    placeholder={`说话人 ${id}`}
                    onChange={(e) =>
                      setSpeakerNames({
                        ...speakerNames,
                        [id]: { ...speakerNames[id], name: e.target.value },
                      })
                    }
                  />
                  <input
                    aria-label={`说话人 ${id} 备注`}
                    value={speakerNames[id].note}
                    placeholder="备注（选填）"
                    onChange={(e) =>
                      setSpeakerNames({
                        ...speakerNames,
                        [id]: { ...speakerNames[id], note: e.target.value },
                      })
                    }
                  />
                </div>
              ))}
          </div>
        ) : (
          <p className="muted" style={{ marginBottom: "8px" }}>
            识别完成后会在此列出说话人，可设置显示名称与备注。
          </p>
        )}

        <div
          className="actions"
          style={{
            marginTop: "20px",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", gap: "8px" }}>
            <button className="primary" onClick={handleSave} disabled={saving}>
              保存任务设置
            </button>
            <button onClick={close}>取消</button>
          </div>
          <button
            type="button"
            className="task-delete-btn"
            onClick={() => {
              close();
              void deleteTask(task.id);
            }}
          >
            删除此任务 🗑
          </button>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]),
    [task, setTask] = useState<Task | null>(null),
    [draft, setDraft] = useState<Segment | null>(null),
    [dirty, setDirty] = useState(false),
    [saving, setSaving] = useState(false),
    [saveError, setSaveError] = useState(""),
    [current, setCurrent] = useState(0),
    [follow, setFollow] = useState(true),
    [preview, setPreview] = useState(false),
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
      if (token !== selectionRef.current || taskRef.current?.id !== selected.id) return true;
      detailFailedRef.current = false;
      setTaskMissing(false);
      setTask(value);
      taskRef.current = value;
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
      setPreview(false);
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
      const incomplete =
        !r.translation.trim() || r.translated_version !== r.source_version;
      return (
        (!query ||
          (r.source + " " + r.translation)
            .toLowerCase()
            .includes(query.toLowerCase())) &&
        (filter === "all" || (filter === "untranslated" && incomplete))
      );
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

  return (
    <div className="app">
      {recovered && <div className="connection-status" role="status">连接已恢复</div>}
      {(disconnected || taskMissing) && (
        <div className="connection-status" role="status" aria-live="polite">
          {disconnected ? "无法连接后端或获取最新状态，正在重试。当前显示的是最后一次获取的进度。" : "任务已不存在"}
        </div>
      )}
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
                    <div className="player-box">
                      <div className="section-heading">
                        <h2>媒体预览</h2>
                        <span>{time(Math.round(task.probe.duration * 1000))}</span>
                      </div>
                      <div className="player">
                        <video
                          key={task.id + preview}
                          ref={video}
                          controls
                          preload="metadata"
                          src={`/api/tasks/${task.id}/media${preview ? "?preview=true" : ""}`}
                          onTimeUpdate={timeUpdate}
                          onError={() =>
                            notify("当前格式无法直接播放，可点击“生成兼容预览”")
                          }
                        />
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
                        <button
                          onClick={() => void run("preview")}
                          disabled={running}
                        >
                          生成兼容预览
                        </button>
                      </div>
                    </div>
                  )}

                  {/* 片段编辑功能 */}
                  {(!isCompact || activeTab === "edit") && (
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
                        <option value="untranslated">未完成（未翻译或译文过期）</option>
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

                  {/* 列表下方批量处理与导出 */}
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

