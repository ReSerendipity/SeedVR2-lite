/**
 * 首启协议确认（P1-1）回归测试。
 *
 * 这个浮层是合规门禁而不是装饰：未确认前必须挡住整个修复工作台。它同时也是
 * main 分支 E2E 连红 4 天的肇因——`setupAllMocks()` 预置 seen 标记后其余用例
 * 不再看到它，于是没有任何测试守着「该出现时是否真的出现」。本 spec 专门补这格。
 */
import { test, expect } from '@playwright/test';

const SEEN_KEY = 'sv_agreement_seen_v1';
const AGREEMENT_VERSION = '2026-09-15';

test.describe('First-run agreement gate', () => {
  test.beforeEach(async ({ page }) => {
    // 只预置引导弹窗，保留协议弹窗的真实首启状态：两个 overlay 叠加时
    // 指针事件的拦截方是哪一个不确定，会与本 spec 的断言对象混淆。
    await page.addInitScript(() => {
      try {
        localStorage.setItem('sv_onboarding_seen_v2', '1');
      } catch (e) {
        /* ignore */
      }
    });
  });

  test('未确认时协议遮罩可见，且真实点击被遮罩吃掉', async ({ page }) => {
    await page.goto('/restore', { waitUntil: 'domcontentloaded' });

    const modal = page.locator('#agreementModal');
    await expect(modal).toBeVisible();
    await expect(modal).toHaveClass(/show/);
    await expect(page.locator('#agreementAck')).toBeDisabled();

    // 「点击被拦截」正是 main E2E 连红的机制，把它钉成断言而不是靠 CI 复现
    await expect(page.locator('#btnStartRestore')).toBeVisible();
    let blockedBy = '';
    try {
      await page.locator('#btnStartRestore').click({ timeout: 3000 });
      blockedBy = 'clicked';
    } catch (e) {
      blockedBy = String(e);
    }
    expect(blockedBy).toMatch(/intercepts pointer events/);
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
