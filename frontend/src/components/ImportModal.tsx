import { useRef } from "react";

export default function ImportModal({
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
