// 本机 API 统一请求封装
// 写请求附带 X-Tinglan: 1，配合后端 local_only 中间件进行来源校验
export async function api<T>(
  url: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const form = body instanceof FormData;
  let timer: ReturnType<typeof setTimeout> | undefined;
  // 即使调用方传入自己的 signal，也给请求等待设置独立上限。
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
