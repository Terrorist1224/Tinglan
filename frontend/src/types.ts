// 字幕条目数据结构
// 关键字段与版本控制语义：
// - start, end: 字幕起止时间戳，单位毫秒 (ms)；导出为 SRT/VTT 时转换为标准时分秒毫秒
// - source: 当前有效原文，可被人工编辑或 AI 润色更新
// - original: ASR 初始语音识别结果，永久留存作为比对基准
// - translation: 当前译文内容
// - translation_provider: 译文来源（"" | "google" | "azure" | "llm" | "manual"，历史保留 google/azure 标签）
// - version: 字幕记录全局乐观锁版本号，每次编辑（原文、译文、时间轴等）递增 1；写回时比对以防脏写覆盖
// - source_version: 原文变更版本号，每次 source 被修改（编辑或润色）时递增 1
// - translated_version: 译文生成所依据的 source_version。若 source_version !== translated_version，表示原文已改动而译文过期（stale 状态）
// - manual_translation: 1 表示人工编辑保存的译文，0 表示机器/AI 生成
// - words: 词级时间戳，用于分块交界去重与说话人边界拆分
export type Segment = {
  id: string;
  task_id: string;
  start: number;
  end: number;
  source: string;
  original: string;
  translation: string;
  translation_provider: "" | "google" | "azure" | "llm" | "manual";
  speaker: string;
  language: string;
  version: number;
  source_version: number;
  translated_version: number;
  manual_translation: number;
  confirmed: number;
  review: string;
  model: string;
  alignment: string;
  words?: { word: string; start: number; end: number; prob: number }[];
};

// 任务参数及运行快照
// - progress_detail: 任务执行中的细粒度进度（包含并发数、退避秒数、已完成/受限行数等），用于状态栏和断点恢复展示
// - last_polish: 记录最近一次 AI 润色变更列表，用于支持“撤回本次润色”
export type Params = {
  audio_track: number;
  subtitle_track: number | null;
  language: string;
  device: string;
  glossary: string;
  topic?: string;
  target_language?: string;
  polish_prompt?: string;
  translate_prompt?: string;
  context_mode?: "batch" | "independent";
  diarize?: boolean;
  auto_polish_translate?: boolean;
  num_speakers?: number | null;
  speakers?: Record<string, { name: string; note: string }>;
  progress_detail?: {
    completed: number;
    translated?: number;
    restricted?: number;
    active_requests?: number;
    concurrency?: number;
    concurrency_ceiling?: number;
    backoff_seconds?: number;
    total: number;
    remaining: number;
    unit: string;
  };
  last_polish?: { time: number; changes: unknown[] };
};

export type Task = {
  id: string;
  name: string;
  status: string;
  phase: string;
  progress: number;
  error: string;
  control: string;
  queue_channel?: "local" | "cloud" | "";
  queue_position?: number | null;
  queue_ahead?: number | null;
  queue_sequence?: number | null;
  queue_rank?: number | null;
  params: Params;
  subtitles: Segment[];
  probe: {
    duration: number;
    audio: {
      index: number;
      codec: string;
      language: string;
      title: string;
      default: boolean;
    }[];
    subtitles: { index: number; codec: string; language: string }[];
    video: { codec: string } | null;
  };
};

export type Settings = {
  base_url: string;
  model: string;
  key?: string;
  target_language?: string;
  default_diarize: boolean;
  default_auto_polish_translate: boolean;
  topic?: string;
  glossary?: string;
  has_key: boolean;
  session_only: boolean;
};

export type System = {
  disk_free_gb: number;
  ffmpeg: boolean;
  ffprobe: boolean;
  privacy: string;
  versions: Record<string, string>;
  models: {
    id: string;
    repo: string;
    ready: boolean;
    size_gb: number;
    gated?: boolean;
  }[];
  download: { status: string; model: string; error: string };
};

export const statuses: Record<string, string> = {
  ready: "待开始",
  queued: "等待处理",
  running: "处理中",
  paused: "已暂停",
  failed: "处理失败",
  interrupted: "意外中断",
  complete: "已完成",
  cancelled: "已取消",
};

export const phases: Record<string, string> = {
  transcribe: "语音识别",
  diarize: "说话人识别",
  polish: "AI 润色",
  translate: "翻译",
  polish_translate: "润色并翻译",
  preview: "生成预览",
  embedded: "提取内嵌字幕",
};

export const DEFAULT_POLISH_PROMPT =
  "清理语气词（如‘那个、然后、嗯、啊’）、口吃与无意义重复，纠正错别字，理顺标点与语序；保留原意与说话人语气，严禁无故扩写或擅自总结。";

export const DEFAULT_TRANSLATE_PROMPT =
  "表达自然，结合语境、术语和人物语气，保留原意与事实，不添加解释，不机械逐字翻译。";

export const languages = [
  ["", "自动检测"],
  ["en", "英语"],
  ["ja", "日语"],
  ["ko", "韩语"],
  ["fr", "法语"],
  ["de", "德语"],
  ["es", "西班牙语"],
  ["ru", "俄语"],
  ["zh", "中文"],
  ["yue", "粤语"],
  ["pt", "葡萄牙语"],
  ["it", "意大利语"],
  ["ar", "阿拉伯语"],
  ["hi", "印地语"],
  ["vi", "越南语"],
  ["th", "泰语"],
];

export const time = (ms: number) => {
  const s = ms / 1000;
  return `${Math.floor(s / 3600)
    .toString()
    .padStart(
      2,
      "0",
    )}:${Math.floor(s / 60) % 60 < 10 ? "0" : ""}${Math.floor(s / 60) % 60}:${(s % 60).toFixed(3).padStart(6, "0")}`;
};

// 本机 API 统一请求封装
// 附带 X-Tinglan: 1 自定义头以配合后端 local_only 中间件进行安全校验
export async function api<T>(
  url: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const form = body instanceof FormData;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => reject(Object.assign(new Error("请求超时"), { timeout: true })),
      5000,
    );
  });
  let response: Response;
  try {
    response = await Promise.race([
      fetch("/api" + url, {
        method,
        signal: signal ?? AbortSignal.timeout(5000),
        headers:
          method === "GET"
            ? {}
            : {
                "X-Tinglan": "1",
                ...(form ? {} : { "Content-Type": "application/json" }),
              },
        body: body === undefined ? undefined : form ? body : JSON.stringify(body),
      }),
      timeout,
    ]);
  } catch (e) {
    if (signal?.aborted || e instanceof TypeError || (e as Error).name === "AbortError") {
      throw Object.assign(new Error("请求超时"), { timeout: true });
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "请求失败" }));
    throw Object.assign(
      new Error(
        typeof error.detail === "string"
          ? error.detail
          : JSON.stringify(error.detail),
      ),
      { status: response.status },
    );
  }
  return response.json();
}


