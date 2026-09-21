// Справочник агентов: просмотр, добавление, правка и удаление карточек.

import { api } from "./api.js";
import {
  actions, button, checkbox, el, muted, openDialog, table, textArea, textField, toolbar,
} from "./dom.js";

const MODEL_HELP = "Идентификатор модели, её синоним или идентификатор записи справочника. "
  + "Ссылка разрешается в модель при создании сессии.";

/** Поле модели с подсказками: синонимы и идентификаторы моделей справочника. */
function modelField(ctx, value) {
  const listId = "agent-models";
  const names = new Set((ctx.data.models?.models ?? []).flatMap((m) => [m.alias, m.identifier]));
  const field = textField("Модель", { name: "model", value, required: true, help: MODEL_HELP });
  field.querySelector("input").setAttribute("list", listId);
  field.append(el("datalist", { id: listId }, [...names].map((name) => el("option", { value: name }))));
  return field;
}

/**
 * Выбор инструкций. Закреплённые идут первыми в своём порядке: в этом порядке их тексты
 * добавляются к system prompt. Остальные следуют по названию, как в справочнике.
 */
function instructionsField(ctx, attached = []) {
  const all = ctx.data.instructions?.instructions ?? [];
  const byId = new Map(all.map((item) => [item.id, item]));
  const ordered = [
    ...attached.map((id) => byId.get(id)).filter(Boolean),
    ...all.filter((item) => !attached.includes(item.id)),
  ];
  return el("fieldset", {},
    el("legend", {}, "Инструкции"),
    ordered.length === 0
      ? muted("Справочник инструкций пуст.")
      : ordered.map((item) => checkbox(item.title, {
        name: "instructions", value: item.id, checked: attached.includes(item.id),
      })),
    el("p", { class: "p-form-help-text" },
      "Тексты отмеченных инструкций добавляются к system prompt при создании сессии."));
}

/** Названия инструкций по идентификаторам, для таблицы агентов. */
function instructionTitles(ctx, ids) {
  const byId = new Map((ctx.data.instructions?.instructions ?? []).map((item) => [item.id, item.title]));
  return ids.map((id) => byId.get(id) ?? id);
}

function fields(ctx, agent) {
  return [
    textField("Название", { name: "name", value: agent?.name ?? "", required: true }),
    textArea("Описание", { name: "description", value: agent?.description ?? "", rows: 2 }),
    textArea("System prompt", {
      name: "systemPrompt",
      value: agent?.systemPrompt ?? "",
      rows: 12,
      required: true,
      help: "Заменяет общие указания сервиса. Указания о завершении хода сервис добавляет сам.",
    }),
    modelField(ctx, agent?.model ?? "llm-medium"),
    instructionsField(ctx, agent?.instructions),
  ];
}

function values(form) {
  return {
    name: form.elements.name.value.trim(),
    description: form.elements.description.value.trim(),
    systemPrompt: form.elements.systemPrompt.value,
    model: form.elements.model.value.trim(),
    instructions: [...form.querySelectorAll("input[name=instructions]:checked")].map((input) => input.value),
  };
}

function create(ctx) {
  openDialog({
    title: "Новый агент",
    submitLabel: "Добавить",
    wide: true,
    body: [
      textField("Идентификатор", {
        name: "id",
        required: true,
        pattern: "[a-z0-9]+(-[a-z0-9]+)*",
        placeholder: "orchestrator",
        help: "Строчные латинские буквы, цифры и дефис. После создания не меняется.",
      }),
      ...fields(ctx),
    ],
    submit: (form) => api.post("/agents", { id: form.elements.id.value.trim(), ...values(form) }),
    done: (agent) => ctx.reload(`Агент «${agent.id}» добавлен`),
  });
}

function edit(ctx, agent) {
  openDialog({
    title: `Агент «${agent.id}»`,
    wide: true,
    body: fields(ctx, agent),
    submit: (form) => api.patch(`/agents/${agent.id}`, values(form)),
    done: () => ctx.reload(`Агент «${agent.id}» сохранён`),
  });
}

async function remove(ctx, agent) {
  if (!confirm(`Удалить агента «${agent.id}»? Созданные по нему сессии сохранятся.`)) return;
  await ctx.run(async () => {
    await api.remove(`/agents/${agent.id}`);
    await ctx.reload(`Агент «${agent.id}» удалён`);
  });
}

export const agents = {
  path: "/agents",
  count: (data) => data.agents.length,
  render(data, ctx) {
    return [
      toolbar(button("Добавить агента", () => create(ctx), "positive")),
      table(
        ["Агент", "Описание", "Модель", "System prompt", "Инструкции", ""],
        data.agents.map((a) => [
          el("div", {}, el("strong", {}, a.name), el("div", {}, el("code", {}, a.id))),
          a.description || muted("—"),
          el("code", {}, a.model),
          el("small", {}, a.systemPrompt.length > 160 ? `${a.systemPrompt.slice(0, 160)}…` : a.systemPrompt),
          a.instructions.length === 0
            ? muted("—")
            : el("ul", { class: "p-list" }, instructionTitles(ctx, a.instructions).map((title) =>
              el("li", { class: "p-list__item" }, el("small", {}, title)))),
          actions(
            button("Изменить", () => edit(ctx, a)),
            button("Удалить", () => remove(ctx, a), "negative")),
        ]),
        "Агентов нет.",
      ),
    ];
  },
};
