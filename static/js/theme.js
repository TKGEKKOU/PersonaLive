"use strict";
(function () {
  const STORAGE_KEY = "yumeno:theme";
  const COOKIE_KEY = "yumeno_theme";

  function readCookie(name) {
    try {
      const match = document.cookie.match(new RegExp("(?:^|;\\s*)" + name + "=([^;]*)"));
      return match ? decodeURIComponent(match[1]) : null;
    } catch (e) {
      return null;
    }
  }

  function writeCookie(value) {
    try {
      document.cookie = `${COOKIE_KEY}=${encodeURIComponent(value)}; path=/; max-age=31536000; SameSite=Lax`;
    } catch (e) {}
  }

  function current() {
    let stored = null;
    try {
      stored = localStorage.getItem(STORAGE_KEY);
    } catch (e) {}
    if (stored === "dark" || stored === "light") return stored;
    return readCookie(COOKIE_KEY) === "dark" ? "dark" : "light";
  }

  function apply(theme) {
    const next = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch (e) {}
    writeCookie(next);
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      const title = next === "dark" ? "切换为浅色主题" : "切换为深色主题";
      btn.setAttribute("aria-pressed", String(next === "dark"));
      btn.title = title;
      btn.setAttribute("aria-label", title);
    }
    document.dispatchEvent(new CustomEvent("yumeno:themechange", { detail: { theme: next } }));
  }

  function toggle() {
    apply(current() === "dark" ? "light" : "dark");
  }

  apply(current());
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", toggle);
    apply(current());
  });

  window.PLTheme = { current, apply, toggle };
})();
