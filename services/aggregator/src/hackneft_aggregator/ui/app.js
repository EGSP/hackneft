// Страница агрегатора. Состояние запрашивается у собственного API и перерисовывается раз в
// половину интервала опроса. Перерисовывается только раздел с изменяющимися данными: поля
// формы настроек заполняются один раз при загрузке и после действий пользователя, иначе
// вводимое значение терялось бы при очередном обновлении.

const MIN_REFRESH_MS = 2000;

let timer = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

async function call(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status}: ${text.slice(0, 200)}`);
  }
  return response.json();
}

function moment(iso) {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "—";
}

function local(iso) {
  // Значение для поля datetime-local: отметки времени источника пояса не несут, поэтому
  // строка берётся как есть, без перевода через Date.
  return iso ? iso.slice(0, 19) : "";
}

function pairs(entries) {
  return el(
    "dl",
    { class: "facts" },
    entries.flatMap(([key, value]) => [el("dt", {}, key), el("dd", {}, value)]),
  );
}

function label(tone, text) {
  return el("span", { class: tone ? `p-status-label--${tone}` : "p-status-label" }, text);
}

function mode(state) {
  if (state.exhausted) return label("caution", "Источник исчерпан");
  if (state.running) return label("positive", "Работает");
  return label("", "Пауза");
}

function indexing(state) {
  const text = {
    pending: "Ожидание",
    running: "Строится указатель источников…",
    ready: "Готовы",
    failed: "Отказ",
  };
  const tone = { ready: "positive", failed: "negative", running: "caution", pending: "caution" };
  return label(tone[state.indexing], text[state.indexing] ?? state.indexing);
}

function tickFacts(tick) {
  if (tick === null) return el("p", { class: "u-text--muted" }, "Тактов ещё не было.");
  return pairs([
    ["Время такта", moment(tick.at)],
    ["Окно источника", `${moment(tick.windowStart)} — ${moment(tick.windowEnd)}`],
    ["Прочитано показаний", tick.readings],
    ["Принято платформой", tick.accepted],
    ["Отклонено как повтор", tick.duplicates],
    ["Отказ", tick.error ?? "нет"],
  ]);
}

function sourcesTable(sources) {
  const columns = ["Установка", "Файл", "Состояние", "Строк", "Тегов", "Период"];
  return el(
    "table",
    { class: "p-table--mobile-card" },
    el("thead", {}, el("tr", {}, columns.map((title) => el("th", {}, title)))),
    el(
      "tbody",
      {},
      sources.map((source) =>
        el(
          "tr",
          {},
          el("td", { "data-heading": columns[0] }, source.title),
          el("td", { "data-heading": columns[1] }, el("code", {}, source.fileName)),
          el(
            "td",
            { "data-heading": columns[2] },
            source.ready ? label("positive", "Готов") : label("negative", source.error ?? "Не готов"),
          ),
          el("td", { "data-heading": columns[3] }, source.rowCount.toLocaleString("ru-RU")),
          el("td", { "data-heading": columns[4] }, source.sensorCount),
          el(
            "td",
            { "data-heading": columns[5] },
            `${moment(source.firstTimestamp)} — ${moment(source.lastTimestamp)}`,
          ),
        ),
      ),
    ),
  );
}

function renderLive(state) {
  const live = document.getElementById("live");
  live.replaceChildren(
    el(
      "div",
      { class: "row u-no-padding--left u-no-padding--right" },
      el(
        "div",
        { class: "col-6" },
        el("h2", { class: "p-heading--5" }, "Курсор"),
        el("p", { class: "cursor-value" }, moment(state.cursor)),
        pairs([
          ["Состояние", mode(state)],
          ["Источники", indexing(state)],
          ["Следующий такт", moment(state.nextTickAt)],
          ["Интервал опроса", `${minutes(state.intervalMinutes)} мин`],
          ["Шаг курсора", `${minutes(state.stepMinutes)} мин`],
          ["Ускорение", `×${minutes(state.speedup)}`],
          ["Начальная дата", moment(state.startDate)],
        ]),
      ),
      el(
        "div",
        { class: "col-6" },
        el("h2", { class: "p-heading--5" }, "Последний такт"),
        tickFacts(state.lastTick),
        el("h2", { class: "p-heading--5" }, "Всего за запуск"),
        pairs([
          ["Тактов", state.ticks],
          ["Принято записей", state.acceptedTotal.toLocaleString("ru-RU")],
          ["Повторов", state.duplicatesTotal.toLocaleString("ru-RU")],
        ]),
      ),
    ),
    el("h2", { class: "p-heading--5" }, "Источники"),
    sourcesTable(state.sources),
  );

  const status = document.getElementById("platform-status");
  const online = state.platform.online;
  status.className =
    online === true
      ? "p-status-label--positive"
      : online === false
        ? "p-status-label--negative"
        : "p-status-label";
  status.textContent =
    online === true
      ? `Платформа доступна (${state.platform.url})`
      : online === false
        ? `Платформа недоступна (${state.platform.url})`
        : "Платформа: проверка…";
}

// Минуты с дробной частью. Поле текстовое, а не числовое: числовое поле в части
// браузеров не принимает запятую и отдаёт вместо «0,1» пустую строку.
const MIN_MINUTES = 0.01;

function minutes(value) {
  return String(value).replace(".", ",");
}

function parseMinutes(label, raw) {
  const text = raw.trim().replace(",", ".");
  const value = Number(text);
  if (text === "" || !Number.isFinite(value)) {
    throw new Error(`${label}: ожидается число, например 0,1`);
  }
  if (value < MIN_MINUTES) {
    throw new Error(`${label}: значение должно быть не меньше ${minutes(MIN_MINUTES)}`);
  }
  return value;
}

function fillForm(state) {
  document.getElementById("interval").value = minutes(state.intervalMinutes);
  document.getElementById("step").value = minutes(state.stepMinutes);
  document.getElementById("cursor").value = local(state.cursor);
}

function notice(message) {
  const box = document.getElementById("notice");
  box.querySelector("p").textContent = message ?? "";
  box.hidden = !message;
}

function schedule(state) {
  // Обновление раз в половину интервала опроса, как задано для страницы. Нижний предел
  // защищает сервис от потока запросов при очень малом интервале.
  const period = Math.max((state.intervalMinutes * 60 * 1000) / 2, MIN_REFRESH_MS);
  if (timer !== null) clearTimeout(timer);
  timer = setTimeout(refresh, period);
}

async function refresh() {
  try {
    const state = await call("GET", "../api/state");
    notice(null);
    renderLive(state);
    schedule(state);
  } catch (failure) {
    notice(`Состояние не получено: ${failure.message}`);
    timer = setTimeout(refresh, MIN_REFRESH_MS);
  }
}

async function act(request) {
  try {
    const state = await request();
    notice(null);
    renderLive(state);
    fillForm(state);
    schedule(state);
  } catch (failure) {
    notice(failure.message);
  }
}

document.getElementById("resume").addEventListener("click", () => {
  act(() => call("POST", "../api/control/resume"));
});

document.getElementById("pause").addEventListener("click", () => {
  act(() => call("POST", "../api/control/pause"));
});

document.getElementById("reset").addEventListener("click", () => {
  act(() => call("POST", "../api/cursor/reset"));
});

document.getElementById("settings").addEventListener("submit", (event) => {
  event.preventDefault();
  act(() => {
    const interval = parseMinutes("Интервал опроса", document.getElementById("interval").value);
    const step = parseMinutes("Шаг курсора", document.getElementById("step").value);
    return call("PATCH", "../api/settings", { intervalMinutes: interval, stepMinutes: step });
  });
});

document.getElementById("cursor-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const cursor = document.getElementById("cursor").value;
  act(() => call("PUT", "../api/cursor", { cursor }));
});

// Первая загрузка заполняет и обновляемый раздел, и поля формы. Дальше форму заполняют
// только действия пользователя, а обновления касаются лишь раздела с состоянием.
try {
  const initial = await call("GET", "../api/state");
  renderLive(initial);
  fillForm(initial);
  schedule(initial);
} catch (failure) {
  notice(`Состояние не получено: ${failure.message}`);
  timer = setTimeout(refresh, MIN_REFRESH_MS);
}
