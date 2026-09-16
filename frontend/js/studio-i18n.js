// Studio's text in English and Russian, picked from the browser's language.
// Same rule as the launcher window's _STRINGS: every key exists in both
// languages, and the Russian is written as Russian, not translated word for
// word. A key missing from the active language falls back to English and then
// to an explicit fallback, so a gap shows up as English text, never as a raw
// key.
//
// Static text in studio.html carries data-i18n="<key>". Its English content
// there is only what shows before this module runs.

const STRINGS = {
  en: {
    "bar.deck": "Deck",
    "bar.background": "Deck background",
    "bar.compact": "Compact layout",
    "bar.dashboard": "Dashboard",

    "preview.title": "Your deck",
    "preview.new": "+ New tile",
    "preview.hint": "Click a tile to edit it. Click + to add a tile there. Drag a tile onto an empty cell to move it.",
    "preview.editTile": "Edit {label}",
    "preview.addAt": "Add a tile at row {row}, column {col}",
    "preview.needsPath": "Needs the path to your VPN client",
    "preview.badParams": "This tile's settings are broken JSON. Open it and fix them under More settings.",

    "token.title": "Agent token",
    "token.body": "Studio needs the agent token to save changes. It's AGENT_TOKEN in config.env. On a standard install it's \"admin\". Studio remembers it in this browser.",
    "token.save": "Save token",

    "common.save": "Save",
    "common.saving": "Saving…",
    "common.cancel": "Cancel",
    "common.close": "Close editor",
    "common.delete": "Delete",

    "inspector.emptyTitle": "Pick a tile",
    "inspector.emptyBody": "Click a tile on your deck to change it, or a + to add a new one.",
    "inspector.newTitle": "New tile",
    "inspector.editTitle": "Edit “{label}”",
    "inspector.deleteNamed": "Delete {label}",

    "step.type": "What does it do?",
    "step.setup": "Setup",
    "step.appearance": "Appearance",
    "step.placement": "Size and position",
    "step.advanced": "More settings",

    "group.actions": "Actions",
    "group.sound": "Sound",
    "group.widgets": "Widgets",
    "group.other": "Other",

    "type.vpn.name": "VPN",
    "type.vpn.desc": "Starts your VPN client and lights up while it runs",
    "type.app.name": "Open an app",
    "type.app.desc": "Starts a program, script or file on the PC",
    "type.screenshot.name": "Screenshot",
    "type.screenshot.desc": "Captures the PC's screen",
    "type.process_toggle.name": "Program on/off",
    "type.process_toggle.desc": "Starts a program, or stops it if it's running",
    "type.agent_shutdown.name": "Close agent",
    "type.agent_shutdown.desc": "Stops the IT-Deck agent on the PC",
    "type.mic.name": "Microphone",
    "type.mic.desc": "Mutes and unmutes the PC's microphone",
    "type.speaker_mute.name": "Sound on/off",
    "type.speaker_mute.desc": "Mutes and unmutes the PC's speakers",
    "type.volume.name": "Volume",
    "type.volume.desc": "A slider for the PC's volume (2 cells wide)",
    "type.audio_switch.name": "Audio output",
    "type.audio_switch.desc": "Switches between speakers and headphones",
    "type.clock_weather.name": "Clock & weather",
    "type.clock_weather.desc": "Time, date and the current weather for a city",
    "type.custom.name": "Custom",
    "type.custom.desc": "Any command, with raw settings",
    "type.change": "Change type",

    "setup.nothing": "Nothing to set up. This tile works as it is.",
    "setup.title": "Set the path to your VPN client",
    "setup.body": "The VPN tile starts your VPN app on this PC, and it needs to know where that app is. Paste the full path to its .exe file.",
    "setup.doneTitle": "VPN is set up:",
    "setup.restartNote": "The tile works now. Its on/off light starts working after you restart IT-Deck.",
    "setup.change": "Change",

    "field.kind": "Kind",
    "field.type": "Command",
    "field.path": "Path to the program",
    "field.fallback_path": "Backup program",
    "field.process_name": "Process name",
    "field.active_style": "Colour when on",
    "field.show_seconds": "Show seconds",
    "field.city": "City",
    "field.cityName": "Name shown on the tile",
    "field.lat": "Latitude",
    "field.lon": "Longitude",
    "field.primaryDevice": "First output",
    "field.secondaryDevice": "Second output",
    "field.label": "Label",
    "field.icon": "Icon",
    "field.color": "Tile colour",
    "field.stateColors": "State colours",
    "field.width": "Width",
    "field.height": "Height",
    "field.row": "Row",
    "field.col": "Column",
    "field.target": "Runs on (agent)",
    "field.stateKey": "State key",
    "field.extraParams": "Extra settings (JSON)",

    "option.active_style.normal": "Accent (teal)",
    "option.active_style.alert": "Warning (red)",

    "badge.required": "Required",
    "badge.set": "Set",

    "placeholder.path": "C:\\Program Files\\App\\app.exe",
    "placeholder.vpn.path": "C:\\Program Files\\v2RayTun\\v2RayTun.exe",
    "placeholder.city": "Start typing a city, then press Enter",

    "hint.path": "The full path to the file to start.",
    "hint.vpn.path": "The full path to your VPN client's .exe file, e.g. v2RayTun.exe.",
    "hint.copyPath": "How to get it: in Explorer, find the file (right-click its desktop shortcut → Open file location), select it and press Ctrl+Shift+C to copy it as a path. The quotes are fine, Studio strips them.",
    "hint.vpn.admin": "If the VPN client insists on running as administrator, IT-Deck can't start it for you. The tile will say so instead of doing nothing.",
    "hint.fallback_path": "Started instead if the first program can't be found.",
    "hint.process_name": "The running process to stop on Force Stop, e.g. WindowsTerminal.exe. Leave empty to use the file name from the path.",
    "hint.process_toggle.process_name": "The process to look for, e.g. Discord.exe.",
    "hint.type": "The agent command name, e.g. launch_app.",
    "hint.devices": "Pick two outputs to swap between them. Leave one as “none” to cycle through every output the PC has.",
    "hint.widgetLabel": "Not shown on the widget. Screen readers read it out.",
    "hint.stateColors": "Tick “Theme colour” to follow the deck's theme.",
    "hint.minWidth": "This tile needs to be at least {n} cells wide.",
    "hint.placement": "Counted from the top-left cell. You can also drag tiles on the preview.",
    "hint.stateKey": "The live value that colours the tile, e.g. mic.muted. Set automatically for known tile types.",
    "hint.extraParams": "Settings this editor doesn't show. Most tiles need nothing here.",

    "devices.none": "— none —",
    "devices.disconnected": "disconnected",
    "devices.missing": "{id} (not on this PC now)",
    "devices.saved": "{id} (saved)",
    "devices.loading": "Loading the PC's audio devices…",
    "devices.failed": "Couldn't load audio devices: {detail}. Saved choices are kept.",
    "devices.empty": "The agent reported no output devices.",

    "city.find": "Find",
    "city.searching": "Searching…",
    "city.tooShort": "Type at least two letters.",
    "city.none": "No places found. Try the name in English, or enter coordinates below.",
    "city.failed": "Search didn't work ({detail}). You can enter the coordinates yourself below.",
    "city.current": "Showing weather for {city} ({lat}, {lon})",
    "city.notSet": "No city yet. The widget shows only the time.",
    "city.manual": "Enter coordinates yourself",

    "icon.none": "No icon",
    "color.active": "On",
    "color.alert": "Warning",
    "color.off": "Off",
    "color.theme": "Theme colour",

    "mode.auto": "Theme default",
    "mode.light": "Light",
    "mode.dark": "Dark",
    "mode.saved": "Saved. Every connected deck is now {mode}.",
    "mode.readFailed": "Couldn't read the current setting: {detail}",

    "error.label": "Give the tile a label.",
    "error.type": "Enter the command name.",
    "error.path": "Fill in “{field}”. Without it the tile has nothing to start.",
    "error.required": "Fill in “{field}”.",
    "error.json": "Extra settings aren't valid JSON: {detail}",
    "error.jsonObject": "they must be an object, like {\"key\": \"value\"}",
    "error.coords": "Latitude must be between -90 and 90 and longitude between -180 and 180. Fill in both or neither.",
    "error.placement": "Width, height, row and column must be whole numbers.",
    "error.save": "Not saved: {detail}",
    "error.delete": "Not deleted: {detail}",
    "error.move": "Couldn't move “{label}”: {detail}",
    "error.compact": "Couldn't compact the layout: {detail}",
    "error.load": "Couldn't load the deck: {detail}. Is IT-Deck running?",
    "error.gridFull": "This deck has no free cells. Delete or shrink a tile first.",
    "error.noToken": "no agent token entered",
    "error.badToken": "the agent token was rejected. Check AGENT_TOKEN in config.env and try again.",
    "error.network": "can't reach IT-Deck ({detail})",

    "confirm.delete": "Delete “{label}”? This can't be undone.",
    "confirm.compact": "Move every tile on “{name}” up and to the left to close the gaps? Positions are rewritten and this can't be undone.",

    "toast.saved": "Saved “{label}”.",
    "toast.deleted": "Deleted “{label}”.",
    "toast.vpnSaved": "VPN path saved.",
  },

  ru: {
    "bar.deck": "Дека",
    "bar.background": "Фон деки",
    "bar.compact": "Уплотнить",
    "bar.dashboard": "На деку",

    "preview.title": "Ваша дека",
    "preview.new": "+ Плитка",
    "preview.hint": "Нажмите на плитку, чтобы её изменить, или на «+», чтобы добавить новую. Плитки можно перетаскивать на свободные клетки.",
    "preview.editTile": "Изменить: {label}",
    "preview.addAt": "Добавить плитку: ряд {row}, колонка {col}",
    "preview.needsPath": "Не указан путь к VPN-клиенту",
    "preview.badParams": "Настройки этой плитки повреждены (неверный JSON). Откройте её и поправьте в разделе «Дополнительно».",

    "token.title": "Токен агента",
    "token.body": "Чтобы сохранять изменения, Studio нужен токен агента: это AGENT_TOKEN в config.env. При обычной установке он равен «admin». Studio запомнит его в этом браузере.",
    "token.save": "Сохранить токен",

    "common.save": "Сохранить",
    "common.saving": "Сохраняю…",
    "common.cancel": "Отмена",
    "common.close": "Закрыть редактор",
    "common.delete": "Удалить",

    "inspector.emptyTitle": "Выберите плитку",
    "inspector.emptyBody": "Нажмите на плитку в деке, чтобы её настроить, или на «+», чтобы добавить новую.",
    "inspector.newTitle": "Новая плитка",
    "inspector.editTitle": "Плитка «{label}»",
    "inspector.deleteNamed": "Удалить «{label}»",

    "step.type": "Что делает плитка",
    "step.setup": "Настройка",
    "step.appearance": "Внешний вид",
    "step.placement": "Размер и место",
    "step.advanced": "Дополнительно",

    "group.actions": "Действия",
    "group.sound": "Звук",
    "group.widgets": "Виджеты",
    "group.other": "Прочее",

    "type.vpn.name": "VPN",
    "type.vpn.desc": "Запускает VPN-клиент и горит, пока он работает",
    "type.app.name": "Открыть программу",
    "type.app.desc": "Запускает программу, скрипт или файл на ПК",
    "type.screenshot.name": "Скриншот",
    "type.screenshot.desc": "Снимок экрана ПК",
    "type.process_toggle.name": "Вкл/выкл программу",
    "type.process_toggle.desc": "Запускает программу, а если она уже открыта — закрывает",
    "type.agent_shutdown.name": "Закрыть агента",
    "type.agent_shutdown.desc": "Останавливает агент IT-Deck на ПК",
    "type.mic.name": "Микрофон",
    "type.mic.desc": "Выключает и включает микрофон ПК",
    "type.speaker_mute.name": "Звук вкл/выкл",
    "type.speaker_mute.desc": "Выключает и включает звук ПК",
    "type.volume.name": "Громкость",
    "type.volume.desc": "Ползунок громкости ПК (две клетки в ширину)",
    "type.audio_switch.name": "Аудиовыход",
    "type.audio_switch.desc": "Переключает колонки и наушники",
    "type.clock_weather.name": "Время и погода",
    "type.clock_weather.desc": "Часы, дата и погода в выбранном городе",
    "type.custom.name": "Своя плитка",
    "type.custom.desc": "Любая команда с настройками вручную",
    "type.change": "Сменить тип",

    "setup.nothing": "Настраивать нечего: плитка работает сразу.",
    "setup.title": "Укажите путь к VPN-клиенту",
    "setup.body": "Плитка VPN запускает ваш VPN на этом ПК, и ей нужно знать, где он установлен. Вставьте полный путь к его .exe-файлу.",
    "setup.doneTitle": "VPN настроен:",
    "setup.restartNote": "Кнопка уже работает, а индикатор включения заработает после перезапуска IT-Deck.",
    "setup.change": "Изменить",

    "field.kind": "Вид",
    "field.type": "Команда",
    "field.path": "Путь к программе",
    "field.fallback_path": "Запасная программа",
    "field.process_name": "Имя процесса",
    "field.active_style": "Цвет во включённом состоянии",
    "field.show_seconds": "Показывать секунды",
    "field.city": "Город",
    "field.cityName": "Название на плитке",
    "field.lat": "Широта",
    "field.lon": "Долгота",
    "field.primaryDevice": "Первый выход",
    "field.secondaryDevice": "Второй выход",
    "field.label": "Подпись",
    "field.icon": "Значок",
    "field.color": "Цвет плитки",
    "field.stateColors": "Цвета состояний",
    "field.width": "Ширина",
    "field.height": "Высота",
    "field.row": "Ряд",
    "field.col": "Колонка",
    "field.target": "Где выполняется (агент)",
    "field.stateKey": "Ключ состояния",
    "field.extraParams": "Прочие параметры (JSON)",

    "option.active_style.normal": "Акцентный (бирюзовый)",
    "option.active_style.alert": "Предупреждение (красный)",

    "badge.required": "Обязательно",
    "badge.set": "Указан",

    "placeholder.path": "C:\\Program Files\\App\\app.exe",
    "placeholder.vpn.path": "C:\\Program Files\\v2RayTun\\v2RayTun.exe",
    "placeholder.city": "Начните вводить город и нажмите Enter",

    "hint.path": "Полный путь к файлу, который нужно запустить.",
    "hint.vpn.path": "Полный путь к .exe-файлу VPN-клиента, например v2RayTun.exe.",
    "hint.copyPath": "Где его взять: щёлкните правой кнопкой по ярлыку на рабочем столе → «Расположение файла», выделите файл и нажмите Ctrl+Shift+C, чтобы скопировать путь. Кавычки можно оставить: Studio их уберёт.",
    "hint.vpn.admin": "Если VPN-клиент требует запуска от имени администратора, IT-Deck не сможет его запустить. Плитка так и скажет, а не промолчит.",
    "hint.fallback_path": "Запустится, если первая программа не найдена.",
    "hint.process_name": "Процесс, который закроет «Принудительно остановить», например WindowsTerminal.exe. Если оставить пустым, имя возьмётся из пути.",
    "hint.process_toggle.process_name": "Какой процесс искать, например Discord.exe.",
    "hint.type": "Имя команды агента, например launch_app.",
    "hint.devices": "Выберите два выхода, чтобы переключаться между ними. Если оставить «нет», плитка будет перебирать все выходы ПК по очереди.",
    "hint.widgetLabel": "На виджете не видна, её зачитывает экранный диктор.",
    "hint.stateColors": "Отметьте «Цвет темы», чтобы цвет брался из темы деки.",
    "hint.minWidth": "Эта плитка должна быть шириной не меньше {n} клеток.",
    "hint.placement": "Отсчёт с левой верхней клетки. Плитки можно и просто перетаскивать в превью.",
    "hint.stateKey": "Живое значение, от которого зависит цвет плитки, например mic.muted. Для известных типов ставится само.",
    "hint.extraParams": "Параметры, которых нет в редакторе. Обычно здесь ничего не нужно.",

    "devices.none": "— нет —",
    "devices.disconnected": "отключено",
    "devices.missing": "{id} (сейчас не подключено)",
    "devices.saved": "{id} (сохранено)",
    "devices.loading": "Загружаю аудиоустройства ПК…",
    "devices.failed": "Не удалось загрузить аудиоустройства: {detail}. Сохранённый выбор не пропадёт.",
    "devices.empty": "Агент не нашёл ни одного выхода звука.",

    "city.find": "Найти",
    "city.searching": "Ищу…",
    "city.tooShort": "Введите хотя бы две буквы.",
    "city.none": "Ничего не нашлось. Попробуйте написать иначе или введите координаты ниже.",
    "city.failed": "Поиск не сработал ({detail}). Координаты можно ввести вручную ниже.",
    "city.current": "Погода для: {city} ({lat}, {lon})",
    "city.notSet": "Город не выбран, виджет покажет только время.",
    "city.manual": "Ввести координаты вручную",

    "icon.none": "Без значка",
    "color.active": "Вкл",
    "color.alert": "Тревога",
    "color.off": "Выкл",
    "color.theme": "Цвет темы",

    "mode.auto": "Как в теме",
    "mode.light": "Светлый",
    "mode.dark": "Тёмный",
    "mode.saved": "Сохранено. Фон на всех деках: {mode}.",
    "mode.readFailed": "Не удалось прочитать настройку: {detail}",

    "error.label": "Дайте плитке подпись.",
    "error.type": "Укажите имя команды.",
    "error.path": "Заполните поле «{field}», иначе плитке нечего запускать.",
    "error.required": "Заполните поле «{field}».",
    "error.json": "В прочих параметрах ошибка JSON: {detail}",
    "error.jsonObject": "нужен объект вида {\"ключ\": \"значение\"}",
    "error.coords": "Широта должна быть от −90 до 90, долгота от −180 до 180. Заполните оба поля или ни одного.",
    "error.placement": "Ширина, высота, ряд и колонка должны быть целыми числами.",
    "error.save": "Не сохранено: {detail}",
    "error.delete": "Не удалено: {detail}",
    "error.move": "Не удалось переместить «{label}»: {detail}",
    "error.compact": "Не удалось уплотнить: {detail}",
    "error.load": "Не удалось загрузить деку: {detail}. IT-Deck запущен?",
    "error.gridFull": "Свободных клеток нет. Сначала удалите или уменьшите какую-нибудь плитку.",
    "error.noToken": "токен агента не введён",
    "error.badToken": "токен агента не подошёл. Проверьте AGENT_TOKEN в config.env и попробуйте снова.",
    "error.network": "нет связи с IT-Deck ({detail})",

    "confirm.delete": "Удалить «{label}»? Отменить это нельзя.",
    "confirm.compact": "Сдвинуть все плитки деки «{name}» вверх и влево, убрав пустые места? Позиции перезапишутся, отменить это нельзя.",

    "toast.saved": "«{label}» сохранена.",
    "toast.deleted": "«{label}» удалена.",
    "toast.vpnSaved": "Путь к VPN сохранён.",
  },
};

function detectLang() {
  const preferred = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language]) || [];
  for (const tag of preferred) {
    const base = String(tag || "").toLowerCase().split("-")[0];
    if (base in STRINGS) return base;
  }
  return "en";
}

export const LANG = detectLang();

export function t(key, vars = null, fallback) {
  let text = STRINGS[LANG][key] ?? STRINGS.en[key];
  if (text === undefined) {
    if (fallback !== undefined) return fallback;
    return key;
  }
  if (vars) {
    text = text.replace(/\{(\w+)\}/g, (match, name) => (name in vars ? String(vars[name]) : match));
  }
  return text;
}

export function applyStaticStrings() {
  document.documentElement.lang = LANG;
  for (const node of document.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.dataset.i18n);
  }
}
