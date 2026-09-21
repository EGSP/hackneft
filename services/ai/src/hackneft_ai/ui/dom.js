// Построение разметки и общие элементы форм. Разметка строится через textContent, а не
// innerHTML: значения приходят из справочников и могут содержать что угодно.

export const MASK = "••••••";

const STATUS = {
  ok: ["positive", "Доступен"],
  available: ["positive", "Доступна"],
  unsatisfied: ["caution", "Требует настройки"],
  not_listed: ["caution", "Нет в перечне провайдера"],
  unreachable: ["negative", "Недоступен"],
  unknown: ["", "Не проверялся"],
};

let uid = 0;

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

export function status(value, text) {
  const [tone, label] = STATUS[value] ?? ["", value];
  return el("span", { class: tone ? `p-status-label--${tone}` : "p-status-label" }, text ?? label);
}

export function chip(text, tone) {
  return el("span", { class: tone ? `p-chip--${tone}` : "p-chip" },
    el("span", { class: "p-chip__value" }, text));
}

export function muted(text) {
  return el("span", { class: "u-text--muted" }, text);
}

export function when(iso) {
  return iso ? new Date(iso).toLocaleString("ru-RU") : "—";
}

export function checked(at, message) {
  return el("div", {}, el("div", {}, message ?? "—"), el("small", { class: "u-text--muted" }, when(at)));
}

export function keyValues(entries) {
  if (entries.length === 0) return muted("—");
  return el("dl", { class: "pairs" },
    entries.flatMap(([key, value]) => [el("dt", {}, key), el("dd", {}, value)]));
}

export function table(columns, rows, empty) {
  if (rows.length === 0) {
    return el("p", { class: "u-text--muted empty" }, empty);
  }
  return el("table", { class: "p-table--mobile-card" },
    el("thead", {}, el("tr", {}, columns.map((title) => el("th", {}, title)))),
    el("tbody", {}, rows.map((cells) =>
      el("tr", {}, cells.map((cell, i) => el("td", { "data-heading": columns[i] }, cell))))));
}

export function button(text, onclick, tone) {
  const kind = tone ? `p-button--${tone}` : "p-button";
  return el("button", { class: `${kind} is-small`, type: "button", onclick }, text);
}

export function toolbar(...buttons) {
  return el("div", { class: "toolbar" }, buttons);
}

export function actions(...buttons) {
  return el("div", { class: "row-actions" }, buttons);
}

// ─── Поля форм ────────────────────────────────────────────────────────────────

function labelled(label, control, help) {
  const id = `f${++uid}`;
  control.id = id;
  return el("div", { class: "p-form__group" },
    el("label", { for: id }, label),
    control,
    help ? el("p", { class: "p-form-help-text" }, help) : null);
}

export function textField(label, { name, value = "", required, pattern, placeholder, help, readonly } = {}) {
  return labelled(label, el("input", {
    type: "text", name, value, required, pattern, placeholder, readonly, autocomplete: "off",
  }), help);
}

export function textArea(label, { name, value = "", rows = 4, placeholder, help, required } = {}) {
  const area = el("textarea", { name, rows, placeholder, required, spellcheck: "false" });
  area.value = value;
  return labelled(label, area, help);
}

export function selectField(label, { name, value, options, help, onchange } = {}) {
  const select = el("select", { name, onchange },
    options.map(([key, text]) => el("option", { value: key, selected: key === value }, text)));
  return labelled(label, select, help);
}

export function checkbox(label, { name, value, checked: isChecked, disabled, help } = {}) {
  return el("div", { class: "p-form__group" },
    el("label", { class: "p-checkbox" },
      el("input", { type: "checkbox", class: "p-checkbox__input", name, value, checked: isChecked, disabled }),
      el("span", { class: "p-checkbox__label" }, label)),
    help ? el("p", { class: "p-form-help-text" }, help) : null);
}

/**
 * Редактор пар «ключ — значение»: секреты провайдера, переменные окружения и заголовки MCP.
 * Значение-маска сохраняется как есть: сервис понимает его как «оставить прежнее значение».
 */
export function keyValueEditor(label, { entries = [], keys = [], placeholders = {}, help } = {}) {
  const listId = `k${++uid}`;
  const rows = el("div", { class: "kv-rows" });
  const datalist = el("datalist", { id: listId });
  const helpText = el("p", { class: "p-form-help-text", hidden: !help }, help ?? "");
  let hints = placeholders;

  function setKeys(known) {
    datalist.replaceChildren(...known.map((key) => el("option", { value: key })));
  }

  function addRow(key = "", value = "") {
    const area = el("textarea", {
      rows: 1, class: "kv-value", placeholder: hints[key] ?? "значение", spellcheck: "false",
    });
    area.value = value;
    const row = el("div", { class: "kv-row" },
      el("input", { type: "text", class: "kv-key", value: key, list: listId, placeholder: "КЛЮЧ", autocomplete: "off" }),
      area,
      button("Убрать", () => row.remove()));
    rows.append(row);
  }

  setKeys(keys);
  entries.forEach(([key, value]) => addRow(key, value));
  const node = el("div", { class: "p-form__group" },
    el("span", { class: "p-form__label" }, label),
    rows,
    datalist,
    button("Добавить ключ", () => addRow()),
    helpText);

  return {
    node,
    setKeys,
    /**
     * Переход к другому набору ключей: незаполненные строки заменяются предполагаемыми ключами
     * набора, а заполненные сохраняются — введённое значение не должно пропадать.
     */
    useKeys({ initial, all, placeholders: nextHints = {}, help: nextHelp }) {
      hints = nextHints;
      setKeys(all);
      for (const row of [...rows.children]) {
        if (row.querySelector(".kv-value").value.trim() === "") row.remove();
      }
      const present = new Set([...rows.children].map((row) => row.querySelector(".kv-key").value.trim()));
      initial.filter((key) => !present.has(key)).forEach((key) => addRow(key));
      helpText.textContent = nextHelp ?? "";
      helpText.hidden = !nextHelp;
    },
    value() {
      const result = {};
      for (const row of rows.children) {
        const key = row.querySelector(".kv-key").value.trim();
        if (key !== "") result[key] = row.querySelector(".kv-value").value;
      }
      return result;
    },
  };
}

// ─── Диалог ───────────────────────────────────────────────────────────────────

/**
 * Диалог с формой. `submit` получает форму и выполняет запрос; отказ показывается в самом
 * диалоге, и введённое не теряется. После успеха диалог закрывается и вызывается `done`.
 */
export function openDialog({ title, body, submitLabel = "Сохранить", submit, done, wide }) {
  const error = el("div", { class: "p-notification--negative", hidden: true },
    el("div", { class: "p-notification__content" },
      el("p", { class: "p-notification__message" })));
  const submitButton = el("button", { class: "p-button--positive", type: "submit" }, submitLabel);
  const form = el("form", { class: "p-form p-form--stacked", novalidate: false },
    body,
    error,
    el("div", { class: "dialog-actions" },
      el("button", { class: "p-button", type: "button", onclick: () => dialog.close() }, "Отмена"),
      submitButton));
  const dialog = el("dialog", { class: wide ? "app-dialog is-wide" : "app-dialog" },
    el("h2", { class: "p-heading--4" }, title),
    form);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submitButton.disabled = true;
    submitButton.textContent = "Выполняется…";
    error.hidden = true;
    try {
      const result = await submit(form);
      dialog.close();
      await done?.(result);
    } catch (failure) {
      error.querySelector("p").textContent = failure.message;
      error.hidden = false;
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = submitLabel;
    }
  });
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}
