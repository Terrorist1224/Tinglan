import { test, expect } from "@playwright/test";
import path from "node:path";
import { execFileSync } from "node:child_process";

test("导入、校对、自动保存、重开、导出与本地设置", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByText("听清原文，译好每一句。")).toBeVisible();
  await page.screenshot({ path: "../.tmp/welcome.png", fullPage: true });
  await page.getByRole("button", { name: "＋ 导入媒体" }).click();
  await expect(page.getByRole("dialog", { name: "选择本地文件" })).toBeVisible();
  await page.screenshot({ path: "../.tmp/import-dialog.png", fullPage: true });
  await page
    .getByLabel("本地绝对路径")
    .fill(path.resolve("../.tmp/samples/听澜样片.mp4"));
  await page.getByRole("button", { name: "极速导入" }).click();
  await expect(page.getByRole("dialog", { name: "选择本地文件" })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "听澜样片.mp4", exact: true }),
  ).toBeVisible();
  const imported = (await (await page.request.get("/api/tasks")).json())[0];
  expect(imported.media).toBe(path.resolve("../.tmp/samples/听澜样片.mp4"));
  expect(
    await page.evaluate(
      async (id) =>
        (
          await fetch(`/api/tasks/${id}/media`, {
            headers: { Range: "bytes=0-99" },
          })
        ).status,
      imported.id,
    ),
  ).toBe(206);
  const task = imported;
  const script = `import sys;sys.path.insert(0,'.');from backend import db;db.init();c=db.connect();d=c.__enter__();d.execute("INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",('browser-'+sys.argv[1],sys.argv[1],0,3000,'Hello from Tinglan.','Hello from Tinglan.','en','browser fixture'));c.__exit__(None,None,None)`;
  execFileSync(
    path.resolve("../.venv/Scripts/python.exe"),
    ["-c", script, task.id],
    {
      cwd: path.resolve(".."),
      env: {
        ...process.env,
        TINGLAN_DATA: path.resolve("../.tmp/browser-data"),
      },
    },
  );
  await expect(
    page.getByRole("button", { name: /Hello from Tinglan/ }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "搜索字幕与术语" })
    .fill("no matching subtitle");
  const incompleteDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出字幕 ↓" }).click();
  await incompleteDownload;
  await expect(page.getByText("已导出，1 句未完成，已用原文补位。")).toBeVisible();
  // 保持搜索框与筛选状态原状
  await expect(
    page.getByRole("textbox", { name: "搜索字幕与术语" }),
  ).toHaveValue("no matching subtitle");
  await page.getByRole("textbox", { name: "搜索字幕与术语" }).fill("");
  await page.locator(".subtitle-content").first().click();
  await expect(page.getByLabel("原文", { exact: true })).toHaveValue(
    "Hello from Tinglan.",
  );
  await page.getByRole("combobox", { name: "筛选字幕" }).selectOption("all");
  const stale = await page.request.patch(
    `/api/tasks/${task.id}/subtitles/browser-${task.id}`,
    {
      headers: { "X-Tinglan": "1" },
      data: {
        version: 0,
        start: 0,
        end: 3000,
        source: "Hello from Tinglan.",
        translation: "旧译文",
        speaker: "",
        confirmed: false,
      },
    },
  );
  expect(stale.ok()).toBeTruthy();
  const staleRow = await stale.json();
  const sourceEdit = await page.request.patch(
    `/api/tasks/${task.id}/subtitles/browser-${task.id}`,
    {
      headers: { "X-Tinglan": "1" },
      data: {
        version: staleRow.version,
        start: staleRow.start,
        end: staleRow.end,
        source: "Hello from Tinglan, corrected.",
        translation: staleRow.translation,
        speaker: staleRow.speaker,
        confirmed: false,
      },
    },
  );
  expect(sourceEdit.ok()).toBeTruthy();
  await page.getByRole("combobox", { name: "筛选字幕" }).selectOption(
    "untranslated",
  );
  await expect(page.getByText("译文已过期", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "筛选字幕" }).selectOption("all");
  await expect(
    page.getByRole("button", {
      name: /第二模型复核|复核选中|时间对齐|区分说话人|采用候选/,
    }),
  ).toHaveCount(0);
  await expect(page.getByLabel("已知说话人数")).toHaveCount(0);
  const source = page.getByLabel("原文", { exact: true });
  await source.fill("Hello from the edited subtitle.");
  await page.getByLabel("译文", { exact: false }).fill("你好，听澜字幕。");
  await expect(page.getByText("已保存", { exact: true })).toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: /听澜样片.mp4/ })
    .first()
    .click();
  await expect(source).toHaveValue("Hello from the edited subtitle.");
  await expect(page.getByLabel("译文", { exact: false })).toHaveValue(
    "你好，听澜字幕。",
  );
  await page.screenshot({ path: "../.tmp/workspace.png", fullPage: true });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出字幕 ↓" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("听澜样片.srt");
  await file.saveAs("../.tmp/browser-export.srt");
  await page.getByRole("button", { name: /全局 API 设置/ }).click();
  await expect(page.locator(".model-list .model")).toHaveCount(2);
  await expect(page.getByLabel("模型拒译时自动使用 Azure 翻译")).toHaveCount(0);
  await expect(page.getByLabel("Azure 密钥")).toHaveCount(0);
  await expect(page.locator(".model-list")).toContainText("whisper-large-v3");
  await expect(page.getByLabel("Hugging Face 下载凭证")).toHaveCount(0);
  await page.getByLabel("基础地址").fill("https://example.invalid/v1");
  await page.getByLabel("模型 ID").fill("user-selected-model");
  await page.getByLabel("API 密钥").fill("local-test-secret");
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(
    page.getByText("设置与 API 密钥已保存到本地", { exact: false }),
  ).toBeVisible();
  expect(
    await (await page.request.get("/api/settings")).json(),
  ).toMatchObject({ model: "user-selected-model" });
  await page.evaluate(async (taskId) => {
    const task = await (await fetch(`/api/tasks/${taskId}`)).json();
    const row = task.subtitles[0];
    const response = await fetch(
      `/api/tasks/${taskId}/subtitles/${row.id}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-Tinglan": "1" },
        body: JSON.stringify({
          version: row.version,
          start: row.start,
          end: row.end,
          source: row.source,
          translation: "Google 来源样例",
          speaker: row.speaker,
          confirmed: false,
        }),
      },
    );
    if (!response.ok) throw new Error(await response.text());
  }, task.id);
  const providerScript = `import sys;sys.path.insert(0,'.');from backend import db;db.init();c=db.connect();d=c.__enter__();d.execute("UPDATE subtitles SET translation_provider='google' WHERE task_id=? AND id=(SELECT id FROM subtitles WHERE task_id=? ORDER BY start,id LIMIT 1)",(sys.argv[1],sys.argv[1]));c.__exit__(None,None,None)`;
  execFileSync(
    path.resolve("../.venv/Scripts/python.exe"),
    ["-c", providerScript, task.id],
    {
      cwd: path.resolve(".."),
      env: {
        ...process.env,
        TINGLAN_DATA: path.resolve("../.tmp/browser-data"),
      },
    },
  );
  await page.reload();
  await page.locator(".task-link").first().click();
  await expect(page.getByText("谷歌翻译（历史）", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: /全局 API 设置/ }).click();
  await expect(page.getByRole("button", { name: "清除密钥", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "清除密钥", exact: true }).click();
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    "local-test-secret",
  );
  await page.getByRole("button", { name: "清除密钥", exact: true }).click();
  await page.getByRole("button", { name: "关闭设置" }).click();
  await page.setViewportSize({ width: 780, height: 1000 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../.tmp/workspace-narrow.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("单屏工作台多视口适配、无横纵滚动与页签切换", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");

  await page.getByRole("button", { name: "＋ 导入媒体" }).click();
  await page
    .getByRole("dialog", { name: "选择本地文件" })
    .locator('input[name="media-upload"]')
    .setInputFiles(path.resolve("../.tmp/samples/听澜样片.mp4"));
  await expect(
    page.getByRole("heading", { name: "听澜样片.mp4", exact: true }),
  ).toBeVisible();

  const task = (await (await page.request.get("/api/tasks")).json())[0];
  const insertScript = `import sys;sys.path.insert(0,'.');from backend import db;db.init();c=db.connect();d=c.__enter__();d.execute("INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",('sub-a-'+sys.argv[1],sys.argv[1],0,3000,'First subtitle text.', 'First subtitle text.','en','fixture'));d.execute("INSERT INTO subtitles(id,task_id,start,end,source,original,language,model) VALUES(?,?,?,?,?,?,?,?)",('sub-b-'+sys.argv[1],sys.argv[1],3000,6000,'Second subtitle text.','Second subtitle text.','en','fixture'));c.__exit__(None,None,None)`;
  execFileSync(
    path.resolve("../.venv/Scripts/python.exe"),
    ["-c", insertScript, task.id],
    {
      cwd: path.resolve(".."),
      env: {
        ...process.env,
        TINGLAN_DATA: path.resolve("../.tmp/browser-data"),
      },
    },
  );

  await page.reload();
  await page.locator(".task-link").first().click();
  await expect(
    page.getByRole("button", { name: /First subtitle text/ }),
  ).toBeVisible();

  // 1. 桌面视口: 1920x1080 (页面无横纵滚动，按钮可见)
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.waitForTimeout(300);
  const scroll1080 = await page.evaluate(() => ({
    scrollHeight: document.documentElement.scrollHeight,
    innerHeight: window.innerHeight,
    scrollY: window.scrollY,
  }));
  expect(scroll1080.scrollY).toBe(0);
  expect(scroll1080.scrollHeight).toBeLessThanOrEqual(scroll1080.innerHeight + 2);

  // 验证左侧上下两栏高度平分（误差在 2px 以内）
  const playerBox = await page.locator(".player-box").boundingBox();
  const editContainer = await page.locator(".edit-container").boundingBox();
  expect(playerBox).not.toBeNull();
  expect(editContainer).not.toBeNull();
  expect(Math.abs(playerBox!.height - editContainer!.height)).toBeLessThanOrEqual(2);

  // 验证文本框高度显著拉伸，顶到最下方操作区之上
  const textareaBox = await page.getByLabel("原文", { exact: true }).boundingBox();
  expect(textareaBox).not.toBeNull();
  expect(textareaBox!.height).toBeGreaterThan(editContainer!.height * 0.5);

  await expect(page.getByRole("button", { name: "保存修改" })).toBeVisible();
  await expect(page.getByRole("button", { name: "润色原文", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "翻译当前句", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "润色并翻译当前句", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /AI 润色原文/ })).toBeVisible();
  await expect(page.locator(".play-tools").getByText("单句循环")).toHaveCount(0);
  await expect(page.locator(".play-tools").getByText("速度")).toHaveCount(0);
  await page.getByRole("button", { name: "任务设置与提示词 ⚙" }).click();
  await expect(page.getByLabel("说话人数量")).toBeVisible();
  await expect(page.getByText("转录后自动分人")).toBeVisible();
  await page.getByRole("button", { name: "关闭" }).click();
  await expect(page.locator('select[aria-label="导出格式"]')).toContainText("TXT（时间轴文稿）");
  await expect(page.getByRole("button", { name: "导出字幕 ↓" })).toBeVisible();
  await expect(page.locator(".mobile-tabs")).toHaveCount(0);

  // 125% 缩放等效视口下截图并验证不被切割
  await page.screenshot({ path: "../.tmp/workbench-desktop.png" });
  await page.setViewportSize({ width: 1536, height: 750 });
  await page.waitForTimeout(300);
  await page.screenshot({ path: "../.tmp/workbench-125zoom.png" });
  await page.setViewportSize({ width: 1920, height: 1080 });

  // 纯原文一键导出 TXT 校验
  await page.locator('select[aria-label="导出模式"]').selectOption("source");
  await page.locator('select[aria-label="导出格式"]').selectOption("txt");
  const downloadTxt = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出字幕 ↓" }).click();
  const txtFile = await downloadTxt;
  expect(txtFile.suggestedFilename()).toBe("听澜样片.txt");

  // 2. 桌面视口: 1440x900
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(300);
  const scroll900 = await page.evaluate(() => ({
    scrollHeight: document.documentElement.scrollHeight,
    innerHeight: window.innerHeight,
    scrollY: window.scrollY,
  }));
  expect(scroll900.scrollY).toBe(0);
  expect(scroll900.scrollHeight).toBeLessThanOrEqual(scroll900.innerHeight + 2);
  await expect(page.getByRole("button", { name: "保存修改" })).toBeVisible();

  // 3. 桌面视口: 1280x720
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.waitForTimeout(300);
  const scroll720 = await page.evaluate(() => ({
    scrollHeight: document.documentElement.scrollHeight,
    innerHeight: window.innerHeight,
    scrollY: window.scrollY,
  }));
  expect(scroll720.scrollY).toBe(0);
  expect(scroll720.scrollHeight).toBeLessThanOrEqual(scroll720.innerHeight + 2);
  await expect(page.getByRole("button", { name: "保存修改" })).toBeVisible();

  // 4. 小视口切换页签: 1024x768 (width 1024 < 1100 -> compact mode)
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.waitForTimeout(300);
  await expect(page.locator(".mobile-tabs")).toBeVisible();
  await expect(page.locator(".mobile-tabs button", { hasText: "预览" })).toBeVisible();
  await expect(page.locator(".mobile-tabs button", { hasText: "编辑" })).toBeVisible();
  await expect(page.locator(".mobile-tabs button", { hasText: "字幕" })).toBeVisible();

  // 点击“字幕”页签
  await page.locator(".mobile-tabs button", { hasText: "字幕" }).click();
  await expect(page.locator(".subtitle-list")).toBeVisible();

  // 点击第 2 条字幕，应自动切换到“编辑”页签并载入第 2 句
  await page.getByRole("button", { name: /Second subtitle text/ }).click();
  await expect(page.getByLabel("原文", { exact: true })).toHaveValue("Second subtitle text.");
  await expect(page.getByRole("button", { name: "保存修改" })).toBeVisible();

  // 5. 窄视口: 780x1000
  await page.setViewportSize({ width: 780, height: 1000 });
  await page.waitForTimeout(300);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();

  // 6. 移动端视口: 390x844
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBeTruthy();
  await expect(page.locator(".mobile-tabs")).toBeVisible();

  expect(errors).toEqual([]);
});

test("状态栏单行进度、键盘滚动、阶段显示与旧任务兼容", async ({ page }) => {
  test.setTimeout(60000);
  const fixture = {
    id: "progress-fixture",
    name: "自动并发测试.mp4",
    status: "running",
    phase: "polish_translate",
    progress: 68,
    error: "",
    control: "",
    params: {
      progress_detail: {
        completed: 136, translated: 136, restricted: 0, total: 200, remaining: 64,
        active_requests: 8, concurrency: 10, concurrency_ceiling: 16, backoff_seconds: 0, unit: "句",
      },
    } as Record<string, any>,
    subtitles: [],
    probe: { duration: 60, audio: [{ index: 0, default: true }], subtitles: [], video: null },
  };
  await page.route("**/api/tasks", (route) => route.fulfill({ json: [fixture] }));
  await page.route("**/api/tasks/progress-fixture", (route) => route.fulfill({ json: fixture }));
  await page.route("**/api/tasks/progress-fixture/media*", (route) => route.fulfill({ status: 204 }));
  await page.route("**/api/settings", (route) => route.fulfill({ json: { base_url: "https://example.invalid/v1", model: "fixture", has_key: false, session_only: false } }));
  await page.goto("/");
  await page.getByRole("button", { name: /自动并发测试.mp4/ }).first().click();
  const detail = page.locator(".progress-detail");
  await expect(detail).toHaveText("进度 136/200 | 受限0句 | 并发10/16");
  await expect(detail).not.toContainText(/已翻译|剩余|当前请求|限流等待/);
  await expect(page.locator(".task-link").first()).toContainText("136/200 句 · 请求 8 · 并发 10/16");
  expect(await page.locator(".task-link small").first().evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
  fixture.params.progress_detail.active_requests = 3;
  fixture.params.progress_detail.concurrency = 3;
  fixture.params.progress_detail.backoff_seconds = 2;
  await expect(detail).toHaveText("进度 136/200 | 受限0句 | 并发3/16");
  await expect(detail).not.toContainText("限流等待");
  const progress = { ...fixture.params.progress_detail };
  const geometry = () => page.locator(
    ".task-strip, .task-strip > div:first-child, .task-strip .actions, .task-strip .actions button, .progress, .player-box, .subtitle-column, .workspace",
  ).evaluateAll((elements) => elements.map((el) => {
    const { x, y, width, height } = el.getBoundingClientRect();
    return { element: el.className || el.textContent, x, y, width, height };
  }));
  const measurements = [];
  for (const width of [1920, 1440, 1280, 780, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 });
    fixture.params = {};
    await expect(detail).toHaveCount(0);
    const before = await geometry();
    if (width === 1920) await page.screenshot({ path: "../.tmp/status-strip-simple/without-detail.png" });
    fixture.params = { progress_detail: { ...progress } };
    await expect(detail).toBeVisible();
    const after = await geometry();
    expect(after).toEqual(before);
    const stripBox = await page.locator(".task-strip").boundingBox();
    const statusBox = await page.locator(".task-strip > div:first-child").boundingBox();
    const detailBox = await detail.boundingBox();
    const actionsBox = await page.locator(".task-strip .actions").boundingBox();
    expect(stripBox!.height).toBe(32);
    expect(detailBox!.x).toBeGreaterThanOrEqual(statusBox!.x + statusBox!.width);
    expect(detailBox!.x + detailBox!.width).toBeLessThanOrEqual(actionsBox!.x);
    expect(detailBox!.y).toBeGreaterThanOrEqual(stripBox!.y);
    expect(detailBox!.y + detailBox!.height).toBeLessThanOrEqual(stripBox!.y + stripBox!.height - 2);
    const lines = await detail.locator("span").evaluateAll((elements) => elements.map((el) => ({
      top: el.getBoundingClientRect().top, height: el.getBoundingClientRect().height,
    })));
    expect(new Set(lines.map((line) => line.top)).size).toBe(1);
    expect(new Set(lines.map((line) => line.height)).size).toBe(1);
    await expect(detail).toHaveAttribute("title", "进度 136/200 | 受限0句 | 并发3/16");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
    const overflow = await detail.evaluate((el) => el.scrollWidth > el.clientWidth);
    if (width === 1920) expect(overflow).toBe(false);
    if (width === 390) {
      expect(overflow).toBe(true);
      await page.getByRole("button", { name: "暂停", exact: true }).focus();
      await page.keyboard.press("Shift+Tab");
      await expect(detail).toBeFocused();
      await page.keyboard.press("ArrowRight");
      await expect.poll(() => detail.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
      await page.keyboard.press("ArrowLeft");
      await expect.poll(() => detail.evaluate((el) => el.scrollLeft)).toBe(0);
      await page.keyboard.press("Tab");
      await expect(page.getByRole("button", { name: "暂停", exact: true })).toBeFocused();
      expect(await page.locator("main").evaluate((el) => el.scrollLeft)).toBe(0);
    }
    measurements.push({ width, before, after, overflow });
    await page.screenshot({ path: `../.tmp/status-strip-simple/processing-${width}.png` });
  }
  await test.info().attach("状态栏与内容坐标", {
    body: JSON.stringify(measurements, null, 2), contentType: "application/json",
  });

  const controls: string[] = [];
  await page.route("**/api/tasks/progress-fixture/control/*", (route) => {
    controls.push(route.request().url().split("/").pop()!);
    return route.fulfill({ json: {} });
  });
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(page.getByText("暂停中…", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByText("取消中…", { exact: true })).toBeVisible();
  expect(controls).toEqual(["pause", "cancel"]);

  await page.setViewportSize({ width: 1920, height: 1080 });
  fixture.phase = "polish";
  await expect(page.locator(".task-strip > div:first-child")).toContainText("AI 润色");
  await expect(detail).toHaveText("进度 136/200 | 并发3/16");
  fixture.phase = "polish_translate";
  await expect(page.locator(".task-strip > div:first-child")).toContainText("润色并翻译");
  await expect(detail).toHaveText("进度 136/200 | 受限0句 | 并发3/16");
  fixture.phase = "transcribe";
  fixture.params = { progress_detail: { completed: 2, total: 5, remaining: 3, unit: "片段" } };
  await expect(detail).toHaveText("进度 2/5");
  await expect(detail).toHaveAttribute("title", "进度 2/5");
  fixture.phase = "translate";
  fixture.params = { progress_detail: { ...progress, concurrency: undefined } };
  await expect(detail).toHaveText("进度 136/200 | 受限0句");
  fixture.params = { progress_detail: { ...progress, concurrency_ceiling: undefined, completed: 137 } };
  await expect(detail).toHaveText("进度 137/200 | 受限0句");
  fixture.params = { progress_detail: { ...progress, active_requests: undefined, restricted: 4 } };
  await expect(detail).toHaveText("进度 136/200 | 受限4句 | 并发3/16");
  fixture.params = { progress_detail: { ...progress, restricted: undefined } };
  await expect(detail).toHaveText("进度 136/200 | 并发3/16");
  fixture.params = { progress_detail: { ...progress } };
  fixture.status = "complete";
  fixture.progress = 100;
  fixture.params.progress_detail.restricted = 64;
  fixture.params.progress_detail.completed = 200;
  fixture.params.progress_detail.remaining = 0;
  fixture.params.progress_detail.active_requests = 0;
  fixture.params.progress_detail.backoff_seconds = 0;
  fixture.error = "有 64 句被上游内容策略拒绝，需要人工翻译";
  await expect(page.getByText("已完成自动翻译", { exact: true })).toBeVisible();
  await expect(page.getByText("有 64 句需要人工翻译", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "全局 API 设置 ↗" }).click();
  await expect(page.getByRole("dialog", { name: "模型与 API 设置" })).toBeVisible();
  await expect(page.getByLabel("请求并发数")).toHaveCount(0);
  await page.getByRole("button", { name: "关闭设置", exact: true }).click();
  fixture.status = "running";
  fixture.progress = 42;
  fixture.params = {};
  await expect(page.locator(".task-strip")).toContainText("42%");
  await expect(detail).toHaveCount(0);
});

test("全局默认开关可见且可键盘操作", async ({ page }) => {
  const settings = {
    base_url: "https://example.invalid/v1",
    model: "fixture",
    has_key: false,
    session_only: false,
    default_diarize: true,
    default_auto_polish_translate: false,
  };
  await page.route("**/api/settings", (route) => {
    if (route.request().method() === "PUT") {
      return route.fulfill({ json: { ...settings, ...route.request().postDataJSON(), notice: "saved" } });
    }
    return route.fulfill({ json: settings });
  });
  await page.route("**/api/system", (route) => route.fulfill({ json: { privacy: "", disk_free_gb: 1, models: [], download: { status: "", model: "", error: "" }, ffmpeg: true, ffprobe: true, versions: {} } }));
  await page.goto("/");
  await page.getByRole("button", { name: "全局 API 设置 ↗" }).click();
  const diarize = page.getByLabel("新任务默认分人");
  const auto = page.getByLabel("新任务转录后自动润色并翻译");
  await expect(diarize).toBeVisible();
  await expect(diarize).toBeChecked();
  await expect(auto).toBeVisible();
  await expect(auto).not.toBeChecked();
  await auto.focus();
  await page.keyboard.press("Space");
  await expect(auto).toBeChecked();
  await expect(page.getByText("可能产生费用", { exact: false })).toBeVisible();
});

test("多选导入逐项创建任务，排队状态按服务端位置显示", async ({ page }) => {
  let created = 0;
  await page.route("**/api/settings", (route) =>
    route.fulfill({
      json: {
        base_url: "https://example.invalid/v1",
        model: "fixture",
        has_key: false,
        session_only: false,
        default_diarize: true,
        default_auto_polish_translate: false,
      },
    }),
  );
  await page.route("**/api/tasks", async (route) => {
    if (route.request().method() === "POST") {
      created += 1;
      const name = created === 1 ? "A.mp4" : "B.mp4";
      return route.fulfill({
        json: {
          id: `new-${created}`,
          name,
          status: "ready",
          phase: "",
          progress: 0,
          error: "",
          control: "",
          params: { audio_track: 0, diarize: true, auto_polish_translate: false },
          subtitles: [],
          probe: { duration: 1, audio: [{ index: 0, default: true }], subtitles: [], video: null },
        },
      });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "＋ 导入媒体" }).click();
  await page.locator('input[type="file"]').setInputFiles([
    { name: "A.mp4", mimeType: "video/mp4", buffer: Buffer.from("a") },
    { name: "B.mp4", mimeType: "video/mp4", buffer: Buffer.from("b") },
  ]);
  await expect.poll(() => created).toBe(2);
  await expect(page.getByText(/已导入 2 个任务/)).toBeVisible();

  const queued = {
    id: "queued-fixture",
    name: "等待任务.mp4",
    status: "queued",
    phase: "transcribe",
    progress: 0,
    error: "",
    control: "",
    queue_channel: "local",
    queue_position: 2,
    queue_ahead: 1,
    queue_rank: 2,
    params: { audio_track: 0, progress_detail: {} },
    subtitles: [],
    probe: { duration: 1, audio: [{ index: 0, default: true }], subtitles: [], video: null },
  };
  await page.route("**/api/tasks", (route) => route.fulfill({ json: [queued] }));
  await page.route("**/api/tasks/queued-fixture", (route) => route.fulfill({ json: queued }));
  await page.route("**/api/tasks/queued-fixture/media*", (route) => route.fulfill({ status: 204 }));
  await page.reload();
  await page.getByRole("button", { name: /等待任务.mp4/ }).first().click();
  await expect(page.locator(".task-strip")).toContainText("等待转录 · 前方 1 个任务");
  await expect(page.locator(".queue-badge-yellow")).toHaveText("2");
  await expect(page.getByRole("button", { name: "退出队列" })).toBeVisible();
});


test("字幕精细化标签展示、多维筛选与媒体不兼容占位", async ({ page }) => {
  const taskWithBadges = {
    id: "badge-task",
    name: "测试标签.mp4",
    status: "complete",
    phase: "",
    progress: 100,
    error: "",
    control: "",
    params: { audio_track: 0 },
    subtitles: [
      {
        id: "sub-1",
        task_id: "badge-task",
        start: 0,
        end: 2000,
        source: "第一句正常文字",
        original: "第一句正常文字",
        translation: "First normal line",
        translation_provider: "llm",
        speaker: "",
        language: "zh",
        version: 1,
        source_version: 1,
        translated_version: 1,
        manual_translation: 0,
        confirmed: 0,
        review: "润色被模型拒绝（原文直译）",
        model: "llm",
        alignment: "sentence",
      },
      {
        id: "sub-2",
        task_id: "badge-task",
        start: 2000,
        end: 2000,
        source: "第二句时间相同",
        original: "第二句时间相同",
        translation: "",
        translation_provider: "",
        speaker: "",
        language: "zh",
        version: 0,
        source_version: 0,
        translated_version: -1,
        manual_translation: 0,
        confirmed: 0,
        review: "",
        model: "llm",
        alignment: "sentence",
      },
      {
        id: "sub-3",
        task_id: "badge-task",
        start: 3000,
        end: 5000,
        source: "第三句策略拦截",
        original: "第三句策略拦截",
        translation: "",
        translation_provider: "",
        speaker: "",
        language: "zh",
        version: 0,
        source_version: 0,
        translated_version: -1,
        manual_translation: 0,
        confirmed: 0,
        review: "模型拦截（需人工翻译）",
        model: "llm",
        alignment: "sentence",
      },
    ],
    probe: { duration: 5, audio: [{ index: 0, default: true }], subtitles: [], video: null },
  };

  await page.route("**/api/tasks", (route) => route.fulfill({ json: [taskWithBadges] }));
  await page.route("**/api/tasks/badge-task", (route) => route.fulfill({ json: taskWithBadges }));
  await page.route("**/api/tasks/badge-task/media*", (route) => route.abort("failed"));
  await page.route("**/api/system", (route) => route.fulfill({ json: { privacy: "", disk_free_gb: 1, models: [], download: { status: "", model: "", error: "" }, ffmpeg: true, ffprobe: true, versions: {} } }));
  await page.route("**/api/settings", (route) => route.fulfill({ json: { base_url: "https://example.invalid/v1", model: "m", has_key: false, session_only: false, default_diarize: true, default_auto_polish_translate: false } }));

  await page.goto("/");
  await page.getByRole("button", { name: /测试标签.mp4/ }).first().click();

  // 1. 验证标签徽标渲染
  await expect(page.locator(".tag-badge.badge-warning", { hasText: "润色被拒" })).toBeVisible();
  await expect(page.locator(".tag-badge.badge-danger", { hasText: "时间异常" })).toBeVisible();
  await expect(page.locator(".tag-badge.badge-danger", { hasText: "模型拦截" })).toBeVisible();

  // 2. 验证多维筛选功能
  const filterSelect = page.getByRole("combobox", { name: "筛选字幕" });
  await filterSelect.selectOption("polish_refused");
  await expect(page.locator(".subtitle-row", { hasText: "第一句正常文字" })).toBeVisible();
  await expect(page.locator(".subtitle-row", { hasText: "第二句时间相同" })).toHaveCount(0);
  await expect(page.locator(".subtitle-row", { hasText: "第三句策略拦截" })).toHaveCount(0);

  await filterSelect.selectOption("time_issue");
  await expect(page.locator(".subtitle-row", { hasText: "第二句时间相同" })).toBeVisible();
  await expect(page.locator(".subtitle-row", { hasText: "第一句正常文字" })).toHaveCount(0);

  await filterSelect.selectOption("restricted");
  await expect(page.locator(".subtitle-row", { hasText: "第三句策略拦截" })).toBeVisible();
  await expect(page.locator(".subtitle-row", { hasText: "第一句正常文字" })).toHaveCount(0);

  // 3. 验证媒体不兼容友好占位
  await expect(page.getByText("当前格式无法在浏览器中直接播放预览")).toBeVisible();
  await expect(page.getByRole("button", { name: "生成兼容预览" })).toHaveCount(0);
});
