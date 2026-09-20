import { Page, Locator, expect } from '@playwright/test';

export class BasePage {
  readonly page: Page;

  // Mapping English nav names to Chinese text rendered in the UI
  private static readonly NAV_TEXT_MAP: Record<string, string> = {
    'Home': '首页',
    'Restore': '修复',
    'History': '历史记录',
    'Settings': '关于',
  };

  // Reverse mapping: Chinese → English
  private static readonly NAV_TEXT_MAP_REVERSE: Record<string, string> = Object.fromEntries(
    Object.entries(BasePage.NAV_TEXT_MAP).map(([en, zh]) => [zh, en]),
  );

  // Shared locators from base.html
  readonly navBar: Locator;
  readonly mainNav: Locator;
  readonly navLinks: Locator;
  readonly themeToggle: Locator;
  readonly toastContainer: Locator;
  readonly confirmModal: Locator;
  readonly breadcrumb: Locator;

  constructor(page: Page) {
    this.page = page;

    this.navBar = page.locator('.sv-navbar');
    this.mainNav = page.locator('#mainNav');
    this.navLinks = page.locator('#mainNav .sv-nav-link');
    this.themeToggle = page.locator('#btnThemeToggle');
    this.toastContainer = page.locator('#toastContainer');
    this.confirmModal = page.locator('#confirmModal');
    this.breadcrumb = page.locator('.sv-breadcrumb');
  }

  /**
   * 导航前掐断当前文档的 SSE 连接。
   *
   * app.js 每次载入都 `new EventSource('/api/sse/events')`，而 api-mocks 的 SSE 用
   * `route.fulfill` 返回**有限**响应体，连接关闭后 EventSource 依规范自动重连，
   * 形成重连风暴；此时发起导航，firefox 会在“旧文档拆载 + 新文档 domcontentloaded”
   * 之间死锁。原本只有 `reloadApplyingClientState()` 做这一步，`navigate()` 没做，
   * 于是同一个坑从 reload 挪到了 goto：CI #103 的 `theme.spec.ts` 就是
   * `page.goto: Timeout 60000ms`（本类 navigate 内部即是 goto）。
   */
  private async closeSseBeforeNavigation(): Promise<void> {
    // 首个文档（about:blank）没有连接可关；evaluate 在文档拆载中会抛错，一并忽略。
    if (this.page.url() === 'about:blank') return;
    try {
      await this.page.evaluate(() => {
        try {
          const conn = (window as unknown as { __sseConnection?: { close?: () => void } })
            .__sseConnection;
          conn?.close?.();
        } catch (e) {
          /* ignore */
        }
      });
    } catch (e) {
      /* 文档正在切换：没有连接可关 */
    }
  }

  async navigate(path: string): Promise<void> {
    await this.closeSseBeforeNavigation();
    await this.page.goto(path, { waitUntil: 'domcontentloaded' });
    await this.waitForPageLoad();
  }

  async waitForPageLoad(): Promise<void> {
    await this.page.waitForLoadState('domcontentloaded');
  }

  /**
   * 重新进入当前页面以应用已被改动的客户端状态（如清空后的 localStorage）。
   *
   * 不用 page.reload()：CI E2E run #64-#70 的 firefox 反复红在
   * `page.reload: Timeout 60000ms`。根因不是 waitUntil 档位——app.js 在每次
   * 载入都会 `new EventSource('/api/sse/events')`，而 api-mocks 的 SSE 用
   * `route.fulfill` 返回**有限**响应体，服务端关闭连接后 EventSource 依规范
   * 自动重连，形成重连风暴；此时发起 reload，firefox 会在“旧文档拆载 + 新文档
   * domcontentloaded”之间死锁（无论 load 还是 domcontentloaded 都超时）。
   *
   * 对策（两处都改）：
   * 1) 先客户端 `__sseConnection.close()` 掐断重连风暴（app.js 在
   *    `initGlobalSSE` 里把实例挂到了 `window.__sseConnection`）；本类已把它抽成
   *    `closeSseBeforeNavigation()`，`navigate()` 同样会走一遍；
   * 2) 用 `page.goto(当前 URL)` 而非 `reload()`。
   *
   * 更正：原文此处写过"8 次失败签名里从无一次卡 goto"——不成立。#103 上 firefox 就是
   * `page.goto: Timeout 60000ms`（theme.spec.ts 经 `navigate()`）。goto 只是比 reload
   * 更少触发，真正的因是第 1 点没做。
   */
  async reloadApplyingClientState(): Promise<void> {
    await this.closeSseBeforeNavigation();
    await this.page.goto(this.page.url(), { waitUntil: 'domcontentloaded' });
    await this.waitForPageLoad();
  }

  async getPageTitle(): Promise<string> {
    return await this.page.title();
  }

  async getCurrentTheme(): Promise<string> {
    return await this.page.evaluate(() => {
      return document.documentElement.getAttribute('data-theme') || 'dark';
    });
  }

  async switchTheme(theme: 'dark' | 'light'): Promise<void> {
    const currentTheme = await this.getCurrentTheme();
    if (currentTheme !== theme) {
      await this.themeToggle.click();
      // Wait for theme attribute to update
      await this.page.waitForFunction(
        (expected) => document.documentElement.getAttribute('data-theme') === expected,
        theme
      );
    }
  }

  async takeScreenshot(name: string): Promise<void> {
    await this.page.screenshot({ path: `screenshots/${name}.png`, fullPage: true });
  }

  async getToastMessages(): Promise<string[]> {
    const toasts = this.toastContainer.locator('.sv-toast');
    const count = await toasts.count();
    const messages: string[] = [];
    for (let i = 0; i < count; i++) {
      const text = await toasts.nth(i).textContent();
      if (text) messages.push(text.trim());
    }
    return messages;
  }

  async waitForToast(message?: string, type?: string, timeout = 10000): Promise<Locator> {
    // Toast elements have classes like "sv-toast toast-error" on the same element
    let locator = type
      ? this.toastContainer.locator(`.sv-toast.toast-${type}`)
      : this.toastContainer.locator('.sv-toast');
    if (message) {
      locator = locator.filter({ hasText: message });
    }
    // 使用 .first() 避免 strict mode violation（同一时刻可能存在多个 toast）
    await locator.first().waitFor({ state: 'visible', timeout });
    return locator.first();
  }

  async dismissToast(): Promise<void> {
    const closeBtn = this.toastContainer.locator('.sv-toast button[aria-label="Close"]').first();
    if (await closeBtn.isVisible()) {
      await closeBtn.click();
    }
  }

  async getActiveNavItem(): Promise<string> {
    // The .active class is on the <a> tag itself: <a class="sv-nav-link active">
    const activeLink = this.page.locator('#mainNav .sv-nav-link.active');
    // The nav link structure is: <a class="sv-nav-link"><i class="nav-icon"></i><span>中文文本</span><span class="nav-shortcut">Alt+N</span></a>
    // Get just the <span> text (not the shortcut span)
    const navText = await activeLink.locator('span:not(.nav-shortcut)').textContent();
    const trimmedText = navText?.trim() || '';
    // Reverse-map Chinese text back to English name
    return BasePage.NAV_TEXT_MAP_REVERSE[trimmedText] || trimmedText;
  }

  async clickNavItem(name: string): Promise<void> {
    // Translate English name to Chinese text that appears in the rendered UI
    const displayText = BasePage.NAV_TEXT_MAP[name] || name;
    const link = this.navLinks.filter({ hasText: displayText });
    await link.click();
    await this.waitForPageLoad();
  }

  async getBreadcrumb(): Promise<string[]> {
    const items = this.breadcrumb.locator('a, .current');
    const count = await items.count();
    const crumbs: string[] = [];
    for (let i = 0; i < count; i++) {
      const text = await items.nth(i).textContent();
      if (text) crumbs.push(text.trim());
    }
    return crumbs;
  }
}
