import { useEffect, useState } from "react";
import { api } from "../api";
import type { Settings, System } from "../types";

export default function SettingsPanel({
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
        .catch(() => { });
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
