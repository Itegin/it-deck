// Pure frontend logic, run with `node --test tests/`. No DOM: only modules
// that don't touch document at import time.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { cleanUrl, brandForApp, WEB_PRESETS, CATALOG } from "../frontend/js/tile-catalog.js";
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

test("every catalog tile has a handler, an icon and its strings (CLAUDE.md: keep in step)", () => {
  const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
  const agent = read("agents/windows/agent.py");
  const handlers = new Set(
    [...agent.slice(agent.indexOf("HANDLERS = {")).matchAll(/^ {4}"([a-z_]+)":/gm)].map((m) => m[1]),
  );
  const render = read("frontend/js/render.js");
  const iconsBlock = render.slice(render.indexOf("export const ICONS = {"), render.indexOf("\n};", render.indexOf("export const ICONS = {")));
  const icons = new Set([...iconsBlock.matchAll(/^ {2}"?([a-z-]+)"?: `/gm)].map((m) => m[1]));
  const i18n = read("frontend/js/studio-i18n.js");
  const en = i18n.slice(i18n.indexOf("  en: {"), i18n.indexOf("  ru: {"));
  const hasKey = (key) => en.includes(`"${key}":`);

  for (const entry of CATALOG) {
    if (entry.kind === "action") {
      assert.ok(handlers.has(entry.type), `${entry.id}: agent.py HANDLERS has no "${entry.type}"`);
    }
    if (entry.icon) {
      assert.ok(icons.has(entry.icon), `${entry.id}: ICONS has no "${entry.icon}"`);
    }
    assert.ok(hasKey(`type.${entry.id}.name`) && hasKey(`type.${entry.id}.desc`), `${entry.id}: name/desc strings`);
    for (const field of entry.fields) {
      if (["devices", "city"].includes(field.control)) continue;
      assert.ok(hasKey(`field.${field.param}`), `${entry.id}: no string field.${field.param}`);
      for (const option of field.options || []) {
        assert.ok(hasKey(`option.${field.param}.${option}`), `${entry.id}: no string option.${field.param}.${option}`);
      }
    }
  }
});

test("game logos: launchers and games map to their marks", () => {
  assert.equal(brandForApp("VALORANT"), "brand:valorant");
  assert.equal(brandForApp("Counter-Strike 2"), "brand:counterstrike");
  assert.equal(brandForApp("Genshin Impact"), null);
  assert.equal(brandForApp("EA app"), "brand:ea");
  assert.equal(brandForApp("Battle.net"), "brand:battlenet");
  // A bare "ea" would have caught these.
  assert.equal(brandForApp("Realtek Audio Console"), null);
  assert.equal(brandForApp("Steam"), "brand:steam");
});

test("every logo is one 24-unit path, and every app mapping has a logo", () => {
  for (const [key, markup] of Object.entries(BRAND_ICONS)) {
    assert.match(markup, /^<svg viewBox="0 0 24 24" fill="currentColor"[^>]*><path d="[^"<>]+"\/><\/svg>$/, key);
  }
  const source = readFileSync(new URL("../frontend/js/tile-catalog.js", import.meta.url), "utf8");
  const block = source.slice(source.indexOf("const APP_BRANDS"), source.indexOf("export function brandForApp"));
  for (const [, key] of block.matchAll(/\["[^"]+", "([^"]+)"\]/g)) {
    assert.ok(BRAND_ICONS[key], `APP_BRANDS points at missing logo ${key}`);
  }
});
