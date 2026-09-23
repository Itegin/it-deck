// First-run tour on the phone: a few cards over the deck the first time it is
// shown on this device, then never again. Skipping counts as seen.
//
// Its own tiny EN/RU table rather than js/studio-i18n.js: that one carries
// every Studio string, and the deck has no other use for it. Language is
// picked the same way (navigator.languages, falling back to English).
//
// The token step exists because a Home Screen web app keeps its own storage,
// separate from Safari's: the icon starts without the token the QR link
// handed Safari (ws.js strips it from the address on purpose), so it asks
// once. Saying so up front turns a surprise prompt into an expected one.

const SEEN_KEY = "itdeck:onboarded";

const STRINGS = {
  en: {
    skip: "Skip",
    back: "Back",
    next: "Next",
    done: "Start",
    step: "Step {n} of {total}",
    tapTitle: "Tap a tile",
    tapBody: "Every tile is a button for your PC. Tap it and the PC does it: opens a program, mutes the mic, takes a screenshot.",
    holdTitle: "Hold for more",
    holdBody: "Press and hold a tile for its menu. “Force Stop” there closes a program the tile started.",
    homeTitle: "Put it on your Home Screen",
    homeBody: "In Safari tap Share, then “Add to Home Screen”. IT-Deck then opens full screen, like an app.",
    homeToken: "The first time it asks for a token, type the one shown in the IT-Deck window on the PC (admin, unless you changed it).",
    studioTitle: "Set it up on the PC",
    studioBody: "Tiles, icons and the layout are edited in Studio: the “Open Studio” button in the IT-Deck window on the PC. Changes appear here instantly.",
  },
  ru: {
    skip: "Пропустить",
    back: "Назад",
    next: "Далее",
    done: "Начать",
    step: "Шаг {n} из {total}",
    tapTitle: "Нажмите на плитку",
    tapBody: "Каждая плитка — кнопка для вашего ПК. Нажали — ПК выполнил: открыл программу, выключил микрофон, сделал скриншот.",
    holdTitle: "Удерживайте для меню",
    holdBody: "Нажмите и держите плитку, чтобы открыть её меню. «Force Stop» в нём закрывает программу, которую запустила плитка.",
    homeTitle: "Добавьте на экран «Домой»",
    homeBody: "В Safari нажмите «Поделиться», затем «На экран „Домой“». IT-Deck будет открываться на весь экран, как приложение.",
    homeToken: "При первом запуске он спросит токен — введите тот, что показан в окне IT-Deck на ПК (admin, если вы его не меняли).",
    studioTitle: "Настройка — на ПК",
    studioBody: "Плитки, значки и раскладка настраиваются в Studio: кнопка «Открыть Studio» в окне IT-Deck на ПК. Изменения сразу появляются здесь.",
  },
};

function detectLang() {
  const preferred = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language]) || [];
  for (const tag of preferred) {
    const base = String(tag || "").toLowerCase().split("-")[0];
    if (STRINGS[base]) return base;
  }
  return "en";
}

const T = STRINGS[detectLang()];

// A Home Screen launch already is what the Home Screen step asks for.
function isStandalone() {
  return window.navigator.standalone === true || window.matchMedia("(display-mode: standalone)").matches;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

// Stroke glyphs in the ICONS style (24x24, currentColor). Module-authored
// literals only.
const GLYPHS = {
  tap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="4"/><path d="M9 12l2 2 4-4"/></svg>',
  hold: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>',
  studio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="13" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>',
};

function steps() {
  const list = [
    { glyph: "tap", title: T.tapTitle, body: [T.tapBody] },
    { glyph: "hold", title: T.holdTitle, body: [T.holdBody] },
  ];
  if (!isStandalone()) {
    list.push({ glyph: "home", title: T.homeTitle, body: [T.homeBody, T.homeToken] });
  }
  list.push({ glyph: "studio", title: T.studioTitle, body: [T.studioBody] });
  return list;
}

function hasSeen() {
  try {
    return localStorage.getItem(SEEN_KEY) === "1";
  } catch (e) {
    // Blocked storage: better never to show it than to show it on every load.
    return true;
  }
}

function markSeen() {
  try {
    localStorage.setItem(SEEN_KEY, "1");
  } catch (e) {
    // As above.
  }
}

let open = false;

// Shown over whatever the deck is showing. Called after every render; does
// nothing once seen, or while already open.
export function maybeShowOnboarding() {
  if (open || hasSeen()) return;
  open = true;

  const list = steps();
  let index = 0;
  const previousFocus = document.activeElement;

  const overlay = el("div", "onboarding-overlay");
  const card = el("div", "onboarding-card");
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "true");
  card.setAttribute("aria-labelledby", "onboarding-title");

  const skip = el("button", "onboarding-skip", T.skip);
  skip.type = "button";
  const glyph = el("div", "onboarding-glyph");
  const counter = el("p", "onboarding-count");
  const title = el("h2", "onboarding-title");
  title.id = "onboarding-title";
  title.tabIndex = -1;
  const body = el("div", "onboarding-body");
  const dots = el("div", "onboarding-dots");
  dots.setAttribute("aria-hidden", "true");
  const nav = el("div", "onboarding-nav");
  const back = el("button", "onboarding-btn", T.back);
  back.type = "button";
  const next = el("button", "onboarding-btn onboarding-primary", T.next);
  next.type = "button";
  nav.append(back, next);

  card.append(skip, glyph, counter, title, body, dots, nav);
  overlay.appendChild(card);

  function show() {
    const step = list[index];
    glyph.innerHTML = GLYPHS[step.glyph];
    counter.textContent = T.step.replace("{n}", index + 1).replace("{total}", list.length);
    title.textContent = step.title;
    body.replaceChildren(...step.body.map((text) => el("p", "", text)));
    dots.replaceChildren(...list.map((_, i) => el("span", i === index ? "is-current" : "")));
    back.hidden = index === 0;
    next.textContent = index === list.length - 1 ? T.done : T.next;
    title.focus({ preventScroll: true });
  }

  function finish() {
    markSeen();
    open = false;
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    if (previousFocus && previousFocus.isConnected) previousFocus.focus();
  }

  function onKey(event) {
    if (event.key === "Escape") finish();
  }

  skip.addEventListener("click", finish);
  back.addEventListener("click", () => {
    if (index > 0) {
      index -= 1;
      show();
    }
  });
  next.addEventListener("click", () => {
    if (index < list.length - 1) {
      index += 1;
      show();
    } else {
      finish();
    }
  });
  document.addEventListener("keydown", onKey);

  document.body.appendChild(overlay);
  show();
}
