import { useState } from "react";
import { DEFAULT_POLISH_PROMPT, DEFAULT_TRANSLATE_PROMPT } from "../constants";
import type { Params, Task } from "../types";

export default function TaskSettingsModal({
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
