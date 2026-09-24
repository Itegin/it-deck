// Pure frontend logic, run with `node --test tests/`. No DOM: only modules
// that don't touch document at import time.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { cleanUrl, brandForApp, WEB_PRESETS } from "../frontend/js/tile-catalog.js";
import { BRAND_ICONS, BRAND_NAMES } from "../frontend/js/brand-icons.js";

test("cleanUrl adds a scheme and refuses what the agent refuses", () => {
  assert.equal(cleanUrl("youtube.com"), "https://youtube.com");
  assert.equal(cleanUrl("localhost:8080"), "http://localhost:8080");
  assert.equal(cleanUrl("discord://"), "discord://");
  assert.equal(cleanUrl("file:///C:/x"), "");
  assert.equal(cleanUrl("javascript:alert(1)"), "");
  assert.equal(cleanUrl(""), "");
});

test("every preset's logo exists", () => {
  for (const preset of WEB_PRESETS) {
    const key = preset.icon.replace(/^brand:/, "");
    assert.ok(BRAND_ICONS[key], `missing logo for ${preset.label}`);
  }
  assert.deepEqual(Object.keys(BRAND_ICONS).sort(), Object.keys(BRAND_NAMES).sort());
});

test("installed programs map to their logos", () => {
  assert.equal(brandForApp("Google Chrome"), "brand:chrome");
  assert.equal(brandForApp("Claude"), "brand:claude");
  assert.equal(brandForApp("Notepad"), null);
});

test("no Russian services in presets or logos", () => {
  const keys = Object.keys(BRAND_ICONS);
  const urls = WEB_PRESETS.map((preset) => new URL(preset.url).hostname);
  for (const banned of ["vk", "yandex", "mailru", "rutube", "ok"]) {
    assert.ok(!keys.includes(banned), `logo ${banned}`);
  }
  for (const host of urls) {
    assert.ok(!/(^|\.)(vk\.com|yandex\.\w+|mail\.ru|rutube\.ru|ok\.ru)$/.test(host), host);
  }
});

test("Studio strings exist in both languages", () => {
  const source = readFileSync(new URL("../frontend/js/studio-i18n.js", import.meta.url), "utf8");
  const en = source.slice(source.indexOf("  en: {"), source.indexOf("  ru: {"));
  const ru = source.slice(source.indexOf("  ru: {"));
  const keys = (text) => new Set([...text.matchAll(/^ {4}"([^"]+)":/gm)].map((m) => m[1]));
  const a = keys(en);
  const b = keys(ru);
  assert.deepEqual([...a].filter((k) => !b.has(k)), []);
  assert.deepEqual([...b].filter((k) => !a.has(k)), []);
});

test("the theme and mode allowlists agree everywhere they are copied", () => {
  // CLAUDE.md's keep-in-step list, checked: a slug missing from any one copy
  // raises no error anywhere -- the theme just 422s, or flashes Flat on load.
  const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
  const quoted = (text) => new Set([...text.matchAll(/"([a-z-]+)"/g)].map((m) => m[1]));
  const between = (text, start, end) => {
    const from = text.indexOf(start);
    assert.ok(from >= 0, `missing ${start}`);
    return text.slice(from, text.indexOf(end, from));
  };

  const settings = read("backend/app/api/settings.py");
  const themeJs = read("frontend/js/theme.js");
  const indexHtml = read("frontend/index.html");
  const themesCss = read("frontend/css/themes.css");

  const backendThemes = quoted(between(settings, "THEMES = (", ")"));
  const frontendThemes = quoted(between(themeJs, "export const THEMES = [", "]"));
  // The boot script lists every non-default theme, then falls back to "flat".
  const bootThemes = quoted(between(indexHtml, 't = (t ===', ";"));
  const cssThemes = new Set([...themesCss.matchAll(/data-theme="([a-z-]+)"/g)].map((m) => m[1]));

  assert.deepEqual([...frontendThemes].sort(), [...backendThemes].sort());
  assert.deepEqual([...bootThemes].sort(), [...backendThemes].sort());
  assert.deepEqual([...cssThemes].sort(), [...backendThemes].sort());

  const backendModes = quoted(between(settings, "MODES = (", ")"));
  const frontendModes = quoted(between(themeJs, "export const MODES = [", "]"));
  assert.deepEqual([...frontendModes].sort(), [...backendModes].sort());
});
