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
