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
  "清理语气词（如‘那个、然后、嗯、啊’）、口吃与无意义重复，纠正错别字，理顺标点与语序；保留原意与说话人语气，严禁无故扩写或擅自总结；若整句仅为语气词或感叹词，请保留原文字，严禁返回空文本。";

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
