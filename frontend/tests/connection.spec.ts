import { test, expect, type Page, type Route } from "@playwright/test";

const warning = "无法连接后端或获取最新状态，正在重试。当前显示的是最后一次获取的进度。";

function fixture(id: string) {
  return {
    id, name: `${id}.mp4`, status: "running", phase: "transcribe", progress: 20,
    error: "", control: "", params: { device: "cpu", audio_track: 0, language: "en" },
    probe: { duration: 60, audio: [], subtitles: [], video: null },
    subtitles: [{
      id: `sub-${id}`, task_id: id, start: 0, end: 3000, source: `Original ${id}`,
      original: `Original ${id}`, translation: "", translation_provider: "",
      speaker: "", language: "en", version: 0, source_version: 0, translated_version: -1,
      manual_translation: 0, confirmed: 0, review: "", model: "fixture", alignment: "",
    }],
  };
}

async function setup(page: Page) {
  const state = {
    tasks: [fixture("alpha"), fixture("beta")],
    failure: "" as "" | "abort" | "5xx" | "hang" | "404",
    target: "list" as "list" | "detail",
    patches: 0, failSave: false, holdSave: false, statusReads: 0,
    patchHeld: [] as { route: Route; body: string }[],
    held: [] as { route: Route; body: string }[],
  };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    const isList = url.pathname === "/api/tasks";
    const detail = state.tasks.find((task) => url.pathname === `/api/tasks/${task.id}`);
    if (method === "PATCH") {
      state.patches++;
      if (state.failSave) return route.abort("connectionrefused");
      const task = state.tasks.find((t) => url.pathname.includes(t.id))!;
      const change = route.request().postDataJSON();
      if (change.version !== task.subtitles[0].version) {
        return route.fulfill({ status: 409, json: { detail: "Version conflict" } });
      }
      Object.assign(task.subtitles[0], change);
      task.subtitles[0].version++;
      if (state.holdSave) {
        state.patchHeld.push({ route, body: JSON.stringify(task.subtitles[0]) });
        return;
      }
      return route.fulfill({ json: task.subtitles[0] });
    }
    if (isList || detail) {
      state.statusReads++;
      if ((state.target === "list" && isList) || (state.target === "detail" && detail?.id === "alpha")) {
        if (state.failure === "abort") return route.abort("connectionrefused");
        if (state.failure === "5xx") return route.fulfill({ status: 503, json: { detail: "Unavailable" } });
        if (state.failure === "404") return route.fulfill({ status: 404, json: { detail: "Missing" } });
        if (state.failure === "hang") {
          state.held.push({ route, body: JSON.stringify(isList ? state.tasks : detail) });
          return;
        }
      }
      return route.fulfill({ json: isList ? state.tasks : detail });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/");
  await page.locator(".task-link").filter({ hasText: "alpha.mp4" }).click();
  await expect(page.getByLabel("原文", { exact: true })).toHaveValue("Original alpha");
  return state;
}

for (const failure of ["abort", "5xx", "hang"] as const) {
  test(`后台${failure}显示持续提示并自动恢复`, async ({ page }) => {
    const state = await setup(page);
    state.failure = failure;
    await expect(page.getByText(warning, { exact: true })).toBeVisible({ timeout: 8500 });
    await expect(page.getByText(warning, { exact: true })).toHaveAttribute("role", "status");
    await expect(page.getByLabel("原文", { exact: true })).toHaveValue("Original alpha");
    await expect(page.getByRole("heading", { name: "alpha.mp4", exact: true })).toBeVisible();
    expect(state.patches).toBe(0);
    if (failure === "abort") await page.screenshot({ path: "../.tmp/crash-protection/connection.png" });
    state.failure = "";
    state.tasks[0].progress = 67;
    await expect(page.getByText(warning, { exact: true })).toHaveCount(0, { timeout: 8500 });
    await expect(page.getByText("连接已恢复", { exact: true })).toBeVisible();
    await expect(page.locator(".task-strip")).toContainText("67%");
  });
}

test("悬挂的列表和当前任务查询均有超时，轮询不积压", async ({ page }) => {
  const state = await setup(page);
  for (const target of ["list", "detail"] as const) {
    state.target = target;
    state.failure = "hang";
    await expect.poll(() => state.held.length).toBeGreaterThan(0);
    const reads = state.statusReads;
    await page.waitForTimeout(2200);
    expect(state.statusReads).toBe(reads);
    await expect(page.getByText(warning, { exact: true })).toBeVisible({ timeout: 5500 });
    state.failure = "";
    await expect(page.getByText(warning, { exact: true })).toHaveCount(0, { timeout: 8500 });
    for (const held of state.held.splice(0)) await held.route.fulfill({ body: held.body }).catch(() => {});
  }
});

test("断线保存失败保留草稿，重连不重发修改", async ({ page }) => {
  const state = await setup(page);
  state.failure = "abort";
  state.failSave = true;
  await expect(page.getByText(warning, { exact: true })).toBeVisible();
  const source = page.getByLabel("原文", { exact: true });
  await source.fill("Unsaved private draft");
  await expect(page.getByText("保存失败 · 未保存", { exact: true })).toBeVisible();
  expect(state.patches).toBe(1);
  state.failure = "";
  state.failSave = false;
  state.tasks[0].subtitles[0].source = "Server copy after restart";
  state.tasks[0].progress = 78;
  await expect(page.getByText("连接已恢复", { exact: true })).toBeVisible();
  await expect(page.locator(".task-strip")).toContainText("78%");
  await expect(source).toHaveValue("Unsaved private draft");
  await page.waitForTimeout(1800);
  expect(state.patches).toBe(1);
  await expect(page.getByText("保存失败 · 未保存", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByText("已保存", { exact: true })).toBeVisible();
  expect(state.patches).toBe(2);
});

test("任务404不误报服务断线，并能切换到其他任务", async ({ page }) => {
  const state = await setup(page);
  state.target = "detail";
  state.failure = "404";
  await expect(page.getByText("任务已不存在", { exact: true })).toBeVisible();
  await expect(page.getByText(warning, { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("原文", { exact: true })).toHaveValue("Original alpha");
  await page.locator(".task-link").filter({ hasText: "beta.mp4" }).click();
  await expect(page.getByLabel("原文", { exact: true })).toHaveValue("Original beta");
  await expect(page.getByText("任务已不存在", { exact: true })).toHaveCount(0);
});

test("切换后迟到的轮询响应不覆盖新任务", async ({ page }) => {
  const state = await setup(page);
  state.target = "detail";
  state.failure = "hang";
  await expect.poll(() => state.held.length).toBe(1);
  await page.locator(".task-link").filter({ hasText: "beta.mp4" }).click();
  await expect(page.getByLabel("原文", { exact: true })).toHaveValue("Original beta");
  await state.held[0].route.fulfill({ body: state.held[0].body });
  await page.waitForTimeout(1800);
  await expect(page.getByRole("heading", { name: "beta.mp4", exact: true })).toBeVisible();
  await expect(page.getByLabel("原文", { exact: true })).toHaveValue("Original beta");
});


test("保存等待期间的新编辑不会被成功响应覆盖", async ({ page }) => {
  const state = await setup(page);
  state.holdSave = true;
  const source = page.getByLabel("原文", { exact: true });
  await source.fill("First edit awaiting save");
  await expect.poll(() => state.patchHeld.length).toBe(1);
  await source.fill("Newer edit while saving");
  await page.waitForTimeout(650);
  state.holdSave = false;
  await state.patchHeld[0].route.fulfill({ body: state.patchHeld[0].body });
  await expect(source).toHaveValue("Newer edit while saving");
  await expect(page.getByText("已保存", { exact: true })).toBeVisible();
  expect(state.patches).toBe(2);
  expect(state.tasks[0].subtitles[0].source).toBe("Newer edit while saving");
  expect(state.tasks[0].subtitles[0].version).toBe(2);
});

test("快速切换时旧的任务选择响应失效", async ({ page }) => {
  const state = await setup(page);
  await page.locator(".task-link").filter({ hasText: "beta.mp4" }).click();
  await expect(page.getByLabel("原文", { exact: true })).toHaveValue("Original beta");
  state.target = "detail";
  state.failure = "hang";
  await page.locator(".task-link").filter({ hasText: "alpha.mp4" }).click();
  await expect.poll(() => state.held.length).toBe(1);
  await page.locator(".task-link").filter({ hasText: "beta.mp4" }).click();
  await state.held[0].route.fulfill({ body: state.held[0].body });
  await page.waitForTimeout(1800);
  await expect(page.getByLabel("原文", { exact: true })).toHaveValue("Original beta");
});
