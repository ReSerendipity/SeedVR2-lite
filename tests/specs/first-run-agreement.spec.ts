/**
 * 首启协议确认（P1-1）回归测试。
 *
 * 这个浮层是合规门禁而不是装饰：未确认前必须挡住整个修复工作台。它同时也是
 * `main` 分支 E2E 连红的肇因——`playwright.config.ts` 用 storageState 预置 seen
 * 标记后，其余用例再也看不到它，于是没有任何测试守着「该出现时是否真的出现」。
 * 本 spec 专门补这格，因此显式清空 storageState 回到真实首启状态。
 */
import { test, expect } from '@playwright/test';

const SEEN_KEY = 'sv_agreement_seen_v1';
const AGREEMENT_VERSION = '2026-09-15';

// 清掉全局预置，拿到首启状态（只留 config 的 baseURL 等设置）
test.use({ storageState: { cookies: [], origins: [] } });

test.describe('First-run agreement gate', () => {
  test.beforeEach(async ({ page }) => {
    // 只预置引导弹窗：两个 overlay 同时打开时，挡住点击的是哪一个不确定，
    // 会与本 spec 的断言对象混淆。
    await page.addInitScript(() => {
      try {
        localStorage.setItem('sv_onboarding_seen_v2', '1');
      } catch (e) {
        /* ignore */
      }
    });
  });

  test('未确认时协议遮罩可见，且挡住下方控件的命中测试', async ({ page }) => {
    await page.goto('/restore', { waitUntil: 'domcontentloaded' });

    const modal = page.locator('#agreementModal');
    await expect(modal).toBeVisible();
    await expect(modal).toHaveClass(/show/);
    await expect(page.locator('#agreementAck')).toBeDisabled();

    // 用 trial click 断言「命中测试被遮罩抢走」：它只做 hit-target 检查，
    // 不等可交互性，因此报错文案在 chromium/firefox/webkit 下都稳定，
    // 不会像匹配 "intercepts pointer events" 字样那样依赖某个引擎的措辞。
    await expect(page.locator('#btnStartRestore')).toBeVisible();
    let blocked = false;
    try {
      await page.locator('#btnStartRestore').click({ trial: true, timeout: 3000 });
    } catch (e) {
      blocked = true;
    }
    expect(blocked, '未确认协议前，遮罩应挡住下方「开始修复」按钮').toBe(true);

    // 正向对照：遮罩自己的控件可以点，证明上面是拦截而非整页失能
    await expect(page.locator('#agreementChk').click({ trial: true })).resolves.toBeUndefined();
  });

  test('勾选并同意后遮罩关闭，seen 标记落库且跨页面保持', async ({ page }) => {
    await page.goto('/restore', { waitUntil: 'domcontentloaded' });

    const chk = page.locator('#agreementChk');
    const ack = page.locator('#agreementAck');
    await chk.check();
    await expect(ack).toBeEnabled();
    await ack.click();

    await expect(page.locator('#agreementModal')).toBeHidden();
    expect(await page.evaluate((k) => localStorage.getItem(k), SEEN_KEY)).toBe(AGREEMENT_VERSION);

    // 再次进入不再打扰（用 goto 而非 reload：app.js 的 SSE 重连会让 reload 偶发超时）
    await page.goto('/restore', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#agreementModal')).toBeHidden();
  });
});
