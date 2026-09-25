/**
 * Explicit wait utilities for SeedVR2 WebUI E2E tests.
 *
 * Provides higher-level wait functions that go beyond Playwright's
 * built-in auto-waiting, specifically tailored for the SeedVR2 UI:
 * - Waiting for API responses after user actions
 * - Waiting for UI elements to become visible/hidden
 * - Waiting for toast notifications
 * - Waiting for progress bars to complete
 * - Waiting for loading indicators to disappear
 *
 * Usage:
 *   import { waitForApiResponse, waitForToast } from '@utils/wait-helpers';
 *   await waitForApiResponse(page, '/api/system/model/status');
 *   await waitForToast(page, 'Model loaded successfully');
 */
import { Page, Response, Locator } from '@playwright/test';

// ============================================================
// API response waits
// ============================================================

/**
 * Wait for an API response matching the specified URL pattern.
 *
 * Useful for verifying that a user action (e.g., clicking a button)
 * triggers the expected backend request.
 *
 * @param page - Playwright page instance
 * @param urlPattern - Substring or regex to match the request URL
 * @param timeout - Maximum wait time in milliseconds (default: 10000)
 * @returns The matching Response object
 * @throws TimeoutError if no matching response is received within the timeout
 */
export async function waitForApiResponse(
  page: Page,
  urlPattern: string | RegExp,
  timeout = 10000,
): Promise<Response> {
  return page.waitForResponse(
    (response) => {
      const url = response.url();
      if (typeof urlPattern === 'string') {
        return url.includes(urlPattern);
      }
      return urlPattern.test(url);
    },
    { timeout },
  );
}

/**
 * Wait for a successful (2xx) API response matching the URL pattern.
 *
 * @param page - Playwright page instance
 * @param urlPattern - Substring or regex to match the request URL
 * @param timeout - Maximum wait time in milliseconds (default: 10000)
 * @returns The matching Response object
 */
export async function waitForSuccessfulApiResponse(
  page: Page,
  urlPattern: string | RegExp,
  timeout = 10000,
): Promise<Response> {
  return page.waitForResponse(
    (response) => {
      const url = response.url();
      const matchesUrl = typeof urlPattern === 'string'
        ? url.includes(urlPattern)
        : urlPattern.test(url);
      return matchesUrl && response.status() >= 200 && response.status() < 300;
    },
    { timeout },
  );
}

// ============================================================
// Element visibility waits
// ============================================================

/**
 * Wait for an element to become visible in the DOM.
 *
 * @param locator - Playwright locator for the target element
 * @param timeout - Maximum wait time in milliseconds (default: 10000)
 */
export async function waitForElementVisible(
  locator: Locator,
  timeout = 10000,
): Promise<void> {
  await locator.waitFor({ state: 'visible', timeout });
}

/**
 * Wait for an element to become hidden or detached from the DOM.
 *
 * @param locator - Playwright locator for the target element
 * @param timeout - Maximum wait time in milliseconds (default: 10000)
 */
export async function waitForElementHidden(
  locator: Locator,
  timeout = 10000,
): Promise<void> {
  await locator.waitFor({ state: 'hidden', timeout });
}

// ============================================================
// Toast notification waits
// ============================================================

/**
 * Wait for a toast notification to appear with specific text.
 *
 * The SeedVR2 UI uses toast notifications for success/error messages.
 * This function waits for a toast element containing the expected text.
 *
 * @param page - Playwright page instance
 * @param expectedText - Text content to look for in the toast (substring match)
 * @param timeout - Maximum wait time in milliseconds (default: 10000)
 * @returns The toast locator for further assertions
 */
export async function waitForToast(
  page: Page,
  expectedText?: string,
  timeout = 10000,
): Promise<Locator> {
  // Common toast/notification selectors used in the SeedVR2 UI
  const toastSelectors = [
    '.sv-toast',
    '#toastContainer .sv-toast',
    '.toast',
    '[role="alert"]',
    '.notification',
  ];

  // Wait for any toast element to appear
  const toastLocator = page.locator(toastSelectors.join(', ')).first();
  await toastLocator.waitFor({ state: 'visible', timeout });

  // If specific text is expected, further filter the toast
  if (expectedText) {
    const textToast = page.locator(toastSelectors.join(', ')).filter({ hasText: expectedText }).first();
    await textToast.waitFor({ state: 'visible', timeout });
    return textToast;
  }

  return toastLocator;
}

/**
 * Wait for a success toast notification to appear.
 *
 * @param page - Playwright page instance
 * @param expectedText - Optional text to match in the success toast
 * @param timeout - Maximum wait time in milliseconds (default: 10000)
 */
export async function waitForSuccessToast(
  page: Page,
  expectedText?: string,
  timeout = 10000,
): Promise<Locator> {
  const successSelectors = [
    '.sv-toast.toast-success',
    '#toastContainer .sv-toast.toast-success',
    '.toast.success',
    '.toast.toast-success',
  ];

  let locator = page.locator(successSelectors.join(', ')).first();

  if (expectedText) {
    locator = page.locator(successSelectors.join(', ')).filter({ hasText: expectedText }).first();
  }

  await locator.waitFor({ state: 'visible', timeout });
  return locator;
}

/**
 * Wait for an error toast notification to appear.
 *
 * @param page - Playwright page instance
 * @param expectedText - Optional text to match in the error toast
 * @param timeout - Maximum wait time in milliseconds (default: 10000)
 */
export async function waitForErrorToast(
  page: Page,
  expectedText?: string,
  timeout = 10000,
): Promise<Locator> {
  const errorSelectors = [
    '.sv-toast.toast-error',
    '#toastContainer .sv-toast.toast-error',
    '.toast.error',
    '.toast.toast-error',
  ];

  let locator = page.locator(errorSelectors.join(', ')).first();

  if (expectedText) {
    locator = page.locator(errorSelectors.join(', ')).filter({ hasText: expectedText }).first();
  }

  await locator.waitFor({ state: 'visible', timeout });
  return locator;
}

// ============================================================
// Progress bar waits
// ============================================================

/**
 * Wait for a progress bar or progress indicator to reach 100% (complete).
 *
 * Polls the progress element's value or width style until it reaches
 * completion, or until the progress element disappears (indicating
 * the task is done).
 *
 * @param page - Playwright page instance
 * @param progressSelector - CSS selector for the progress element
 * @param timeout - Maximum wait time in milliseconds (default: 60000)
 */
export async function waitForProgressComplete(
  page: Page,
  progressSelector = '.progress-bar, [role="progressbar"], .ant-progress, .el-progress',
  timeout = 60000,
): Promise<void> {
  const progressLocator = page.locator(progressSelector).first();
  const startTime = Date.now();

  while (Date.now() - startTime < timeout) {
    // Check if the progress element still exists
    const isVisible = await progressLocator.isVisible().catch(() => false);

    if (!isVisible) {
      // Progress element disappeared — task likely completed
      return;
    }

    // Try to read the progress value from aria-valuenow attribute
    const ariaValue = await progressLocator.getAttribute('aria-valuenow').catch(() => null);
    if (ariaValue !== null) {
      const progress = parseFloat(ariaValue);
      if (progress >= 100 || progress >= 1.0) {
        return;
      }
    }

    // Try to read the progress from the element's width style
    const widthStyle = await progressLocator.evaluate((el: Element) => {
      const inner = el.querySelector('.progress-bar-fill, .ant-progress-bg, .el-progress-bar__inner')
        ?? el;
      return (inner as HTMLElement).style.width || (inner as HTMLElement).style.getPropertyValue('width');
    }).catch(() => '');

    if (widthStyle) {
      const percentMatch = widthStyle.match(/(\d+(?:\.\d+)?)%/);
      if (percentMatch) {
        const percent = parseFloat(percentMatch[1]);
        if (percent >= 100) {
          return;
        }
      }
    }

    // Wait before polling again
    await page.waitForTimeout(500);
  }

  throw new Error(`Progress did not complete within ${timeout}ms`);
}

// ============================================================
// Loading indicator waits
// ============================================================

/**
 * Wait for all loading indicators to disappear from the page.
 *
 * This is useful as a pre-condition before interacting with the UI,
 * ensuring that the page has finished loading data.
 *
 * @param page - Playwright page instance
 * @param loadingSelector - CSS selector for loading indicators
 * @param timeout - Maximum wait time in milliseconds (default: 30000)
 */
export async function waitForLoadingComplete(
  page: Page,
  loadingSelector = '.loading, .spinner, .ant-spin, .el-loading-mask, [data-loading="true"], .skeleton',
  timeout = 30000,
): Promise<void> {
  const loadingLocator = page.locator(loadingSelector);
  const count = await loadingLocator.count();

  if (count === 0) {
    // No loading indicators found, page is ready
    return;
  }

  // Wait for all loading indicators to become hidden
  await loadingLocator.last().waitFor({ state: 'hidden', timeout });
}

/**
 * 导航前掐断当前文档的 SSE 连接。
 *
 * 为什么需要：`app.js` 每次载入都会 `new EventSource('/api/sse/events')`，而 api-mocks
 * 的 SSE 用 `route.fulfill` 返回**有限**响应体；服务端关闭连接后 EventSource 依规范自动
 * 重连，形成重连风暴。此时发起导航，firefox 会在「旧文档拆载 + 新文档 domcontentloaded」
 * 之间死锁，表现为 `page.goto: Timeout 60000ms`（CI 上 theme.spec.ts / performance.spec.ts
 * 都是这一类）。
 *
 * 只掐连接、不改 `goto` 的 waitUntil 档位：死锁的因是重连风暴，不是等待哪一档
 * （实测 load 与 domcontentloaded 都会超时），改档位反而会悄悄改变各用例的时序假设。
 *
 * `BasePage.navigate()` 与裸 `page.goto()` 的 spec 共用这一份实现，避免同一修复两处漂移。
 *
 * @param page - Playwright page 实例
 */
export async function closeSseBeforeNavigation(page: Page): Promise<void> {
  // 首个文档（about:blank）没有连接可关；文档正在切换时 evaluate 会抛错，一并忽略。
  if (page.url() === 'about:blank') return;
  try {
    await page.evaluate(() => {
      try {
        const conn = (window as unknown as { __sseConnection?: { close?: () => void } })
          .__sseConnection;
        conn?.close?.();
      } catch (e) {
        /* 页面没有初始化 SSE：无需关闭 */
      }
    });
  } catch (e) {
    /* 文档正在拆载：没有连接可关 */
  }
}

/**
 * 等一个元素的几何真正停下来，再对它操作。
 *
 * 为什么需要：`restore.html` 展开高级参数时会 `scrollIntoView({behavior:'smooth'})`
 * （restore.html:1979），而被滚动的容器高度同时还在跑 `max-height 0.35s` 的 CSS 过渡
 * （style.css:5090）——平滑滚动追的是一个仍在变长的目标，于是被点的入口控件会持续位移
 * 约 600ms（实测 firefox：toggle 的 y 依次 723 → 402 → 198 → 148 → 132 → 131 → 130）。
 * 空闲机器上 Playwright 的稳定性检查能扛过去；CI 高负载下主线程被 SSE 重连占住，动画
 * 中途出现 ≥2 帧的停顿就会被判"已稳定"，随后的点击落在过期坐标上 → 折叠没发生，
 * `expect(advParams).toBeHidden()` 在 15s 内一直读到 `class="sv-advanced-params open"`
 * （CI run #162）。
 *
 * 这不是重试，也不是放宽断言：它把"我要操作的控件已经不动了"变成显式前置条件；
 * 若元素在 timeout 内始终不停，这里会**直接失败并给出采样值**，而不是把问题推给
 * 后面那条语义断言去偶发。
 *
 * @param locator - 目标元素
 * @param opts.stableSamples - 连续多少次采样几何不变算稳定（默认 3）
 * @param opts.timeout - 最长等待（默认 5000ms）
 * @returns 稳定后的几何值
 */
export async function waitForGeometryStable(
  locator: Locator,
  opts: { stableSamples?: number; timeout?: number } = {},
): Promise<string> {
  const need = opts.stableSamples ?? 3;
  const timeout = opts.timeout ?? 5000;
  const deadline = Date.now() + timeout;
  let last = '';
  let streak = 0;
  const samples: string[] = [];

  while (Date.now() < deadline) {
    const box = await locator.boundingBox();
    const cur = box
      ? `${Math.round(box.x)},${Math.round(box.y)},${Math.round(box.width)},${Math.round(box.height)}`
      : 'null';
    if (cur === last) {
      streak += 1;
    } else {
      streak = 1;
      last = cur;
      samples.push(cur);
    }
    if (streak >= need) return cur;
    await locator.page().waitForTimeout(50);
  }
  throw new Error(
    `元素几何在 ${timeout}ms 内没有停下来（连续 ${need} 次同框未达成），` +
      `说明有动画/抖动一直在跑，不适合对它发起操作。采样序列: ${samples.join(' → ')}`,
  );
}
