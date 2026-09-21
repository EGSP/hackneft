// Справочник моделей: просмотр, добавление из перечня провайдера, правка признаков и удаление.

import { api } from "./api.js";
import {
  actions, button, checkbox, chip, el, muted, openDialog, selectField, status, table, textField,
  toolbar, when,
} from "./dom.js";

// Синоним: строчные латинские буквы и цифры, группы разделены одиночным дефисом.
const ALIAS_PATTERN = "[a-z0-9]+(-[a-z0-9]+)*";
const ALIAS_HELP = "Короткое имя, которое принимается наравне с идентификатором. Может быть общим "
  + "у нескольких моделей: тогда выбирается модель по умолчанию, затем доступная, затем добавленная раньше.";

function aliasField(value) {
  return textField("Синоним", {
    name: "alias", value, required: true, pattern: ALIAS_PATTERN, help: ALIAS_HELP,
  });
}

function create(ctx) {
  const providers = ctx.data.providers?.providers ?? [];
  const listId = "provider-models";
  const catalog = el("datalist", { id: listId });
  const hint = el("p", { class: "p-form-help-text" });

  // Перечень моделей провайдера служит подсказкой; ввести можно и идентификатор вне его.
  // Ответ на запрос для прежде выбранного провайдера отбрасывается: он мог прийти позже.
  let latest = 0;
  async function loadCatalog(name) {
    const provider = providers.find((p) => p.name === name);
    const current = ++latest;
    catalog.replaceChildren();
    hint.textContent = "Загрузка перечня моделей провайдера…";
    try {
      const listing = await api.get(`/providers/${provider.id}/models`);
      if (current !== latest) return;
      catalog.replaceChildren(...listing.models.map((m) =>
        el("option", { value: m.identifier }, m.vendor)));
      hint.textContent = listing.error
        ? `Перечень не получен: ${listing.error}`
        : `В перечне провайдера моделей: ${listing.models.length}. Начните вводить имя.`;
    } catch (error) {
      if (current === latest) hint.textContent = `Перечень не получен: ${error.message}`;
    }
  }

  const identifier = textField("Идентификатор модели", {
    name: "identifier",
    required: true,
    placeholder: "qwen3.6-35b-a3b/latest",
  });
  identifier.querySelector("input").setAttribute("list", listId);
  identifier.append(hint, catalog);

  openDialog({
    title: "Новая модель",
    submitLabel: "Добавить",
    body: [
      selectField("Провайдер", {
        name: "provider",
        value: providers[0]?.name,
        options: providers.map((p) => [p.name, p.title ? `${p.name} — ${p.title}` : p.name]),
        onchange: (event) => loadCatalog(event.target.value),
      }),
      identifier,
      aliasField("llm-medium"),
      checkbox("Поддерживает вызов инструментов", { name: "supportsTools", checked: true }),
      checkbox("Возвращает рассуждение", { name: "supportsReasoning" }),
      checkbox("Модель по умолчанию", {
        name: "isDefault",
        help: "Первая добавленная модель становится моделью по умолчанию сама.",
      }),
    ],
    submit: (form) => api.post("/models", {
      provider: form.elements.provider.value,
      identifier: form.elements.identifier.value.trim(),
      alias: form.elements.alias.value.trim(),
      supportsTools: form.elements.supportsTools.checked,
      supportsReasoning: form.elements.supportsReasoning.checked,
      isDefault: form.elements.isDefault.checked || undefined,
    }),
    done: (model) => ctx.reload(`Модель «${model.identifier}» добавлена`),
  });
  if (providers.length > 0) loadCatalog(providers[0].name);
}

function edit(ctx, model) {
  openDialog({
    title: `Модель «${model.identifier}»`,
    body: [
      aliasField(model.alias),
      checkbox("Поддерживает вызов инструментов", { name: "supportsTools", checked: model.supportsTools }),
      checkbox("Возвращает рассуждение", { name: "supportsReasoning", checked: model.supportsReasoning }),
      checkbox("Модель по умолчанию", {
        name: "isDefault",
        checked: model.isDefault,
        disabled: model.isDefault,
        help: model.isDefault
          ? "Пометка снимается назначением моделью по умолчанию другой записи."
          : null,
      }),
    ],
    submit: (form) => api.patch(`/models/${model.id}`, {
      alias: form.elements.alias.value.trim(),
      supportsTools: form.elements.supportsTools.checked,
      supportsReasoning: form.elements.supportsReasoning.checked,
      isDefault: !model.isDefault && form.elements.isDefault.checked ? true : undefined,
    }),
    done: () => ctx.reload(`Модель «${model.identifier}» сохранена`),
  });
}

async function remove(ctx, model) {
  if (!confirm(`Удалить модель «${model.identifier}» из справочника?`)) return;
  await ctx.run(async () => {
    await api.remove(`/models/${model.id}`);
    await ctx.reload(`Модель «${model.identifier}» удалена`);
  });
}

async function checkAll(ctx) {
  await ctx.run(async () => {
    await api.post("/models/check");
    await ctx.reload("Доступность моделей проверена");
  });
}

export const models = {
  path: "/models",
  count: (data) => data.models.length,
  render(data, ctx) {
    const hasProviders = (ctx.data.providers?.providers ?? []).length > 0;
    const add = button("Добавить модель", () => create(ctx), "positive");
    add.disabled = !hasProviders;
    return [
      toolbar(
        add,
        button("Проверить доступность", () => checkAll(ctx)),
        hasProviders ? null : muted("Сначала добавьте провайдера.")),
      table(
        ["Модель", "Синоним", "Провайдер", "Возможности", "Доступность", "Идёт ход", ""],
        data.models.map((m) => [
          el("div", {},
            el("code", {}, m.identifier),
            m.isDefault ? el("div", {}, chip("по умолчанию", "information")) : null,
            m.problems.length > 0 ? el("div", {}, chip("есть неполадки", "caution")) : null,
            m.problems.map((problem) => el("div", {}, el("small", {}, problem)))),
          el("code", {}, m.alias),
          m.provider,
          el("div", {},
            m.supportsTools ? chip("инструменты") : null,
            m.supportsReasoning ? chip("рассуждение") : null,
            !m.supportsTools && !m.supportsReasoning ? muted("—") : null),
          el("div", {},
            status(m.availability),
            el("div", {}, el("small", { class: "u-text--muted" }, when(m.lastCheckAt)))),
          m.activeSessions.length === 0
            ? muted("—")
            : el("ul", { class: "p-list" }, m.activeSessions.map((s) =>
              el("li", { class: "p-list__item" }, `${s.title} (${s.kind})`))),
          actions(
            button("Изменить", () => edit(ctx, m)),
            button("Удалить", () => remove(ctx, m), "negative")),
        ]),
        "Моделей нет.",
      ),
    ];
  },
};
