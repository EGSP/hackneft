// Справочник провайдеров: просмотр, добавление, правка, проверка связи и удаление карточек.

import { api } from "./api.js";
import {
  actions, button, checked, el, keyValueEditor, keyValues, muted, openDialog, selectField,
  status, table, textField, toolbar,
} from "./dom.js";

const TYPES = [
  ["yandex", "Yandex AI Studio"],
  ["openai_compatible", "OpenAI-совместимый (vLLM, Ollama)"],
];

/** Допустимые ключи секретов по типу; первые в списке предлагаются новой карточке. */
const KEYS = {
  yandex: {
    initial: ["YANDEX_FOLDER_ID", "YANDEX_KEY_ID", "YANDEX_SERVICE_ACCOUNT_ID", "YANDEX_PRIVATE_KEY"],
    all: ["YANDEX_FOLDER_ID", "YANDEX_KEY_ID", "YANDEX_SERVICE_ACCOUNT_ID", "YANDEX_PRIVATE_KEY",
      "YANDEX_IAM_TOKEN", "YANDEX_BASE_URL"],
  },
  openai_compatible: {
    initial: ["OPENAI_BASE_URL", "OPENAI_API_KEY"],
    all: ["OPENAI_BASE_URL", "OPENAI_API_KEY"],
  },
};

const SECRETS_HELP =
  "Ключи те же, что в env-файле. Yandex: обязателен YANDEX_FOLDER_ID и ключ сервисного " +
  "аккаунта либо YANDEX_IAM_TOKEN. OpenAI-совместимый: обязателен OPENAI_BASE_URL. " +
  "Закрытый ключ вставляется целиком, с переносами строк. Значение «••••••» оставляет " +
  "прежний секрет.";

function providerForm(provider) {
  const type = provider?.type ?? "yandex";
  const secrets = keyValueEditor("Секреты", {
    entries: provider
      ? Object.entries(provider.secrets)
      : KEYS[type].initial.map((key) => [key, ""]),
    keys: KEYS[type].all,
    help: SECRETS_HELP,
  });
  const body = [
    textField("Имя", {
      name: "name",
      value: provider?.name ?? "",
      required: !provider,
      readonly: Boolean(provider),
      pattern: "[a-z][a-z0-9_\\-]{0,39}",
      placeholder: "yandex",
      help: provider
        ? "Имя не меняется: по нему на карточку ссылаются модели."
        : "Латиница в нижнем регистре, цифры, «_» и «-». После создания не меняется.",
    }),
    textField("Название", { name: "title", value: provider?.title ?? "", placeholder: "Яндекс" }),
    selectField("Тип", {
      name: "type",
      value: type,
      options: TYPES,
      onchange: (event) => secrets.setKeys(KEYS[event.target.value].all),
    }),
    secrets.node,
  ];
  const read = (form) => ({
    title: form.elements.title.value.trim() || null,
    type: form.elements.type.value,
    secrets: secrets.value(),
  });
  return { body, read };
}

function create(ctx) {
  const { body, read } = providerForm(null);
  openDialog({
    title: "Новый провайдер",
    body,
    submitLabel: "Добавить",
    wide: true,
    submit: (form) => api.post("/providers", { name: form.elements.name.value.trim(), ...read(form) }),
    done: (provider) => ctx.reload(`Провайдер «${provider.name}» добавлен: ${provider.lastCheckMessage ?? ""}`),
  });
}

function edit(ctx, provider) {
  const { body, read } = providerForm(provider);
  openDialog({
    title: `Провайдер «${provider.name}»`,
    body,
    wide: true,
    submit: (form) => api.patch(`/providers/${provider.id}`, read(form)),
    done: (saved) => ctx.reload(`Провайдер «${saved.name}» сохранён: ${saved.lastCheckMessage ?? ""}`),
  });
}

async function check(ctx, provider) {
  await ctx.run(async () => {
    const result = await api.post(`/providers/${provider.id}/check`);
    await ctx.reload(`Провайдер «${provider.name}»: ${result.lastCheckMessage ?? result.checkStatus}`);
  });
}

async function remove(ctx, provider) {
  if (!confirm(`Удалить карточку провайдера «${provider.name}»?`)) return;
  await ctx.run(async () => {
    await api.remove(`/providers/${provider.id}`);
    await ctx.reload(`Провайдер «${provider.name}» удалён`);
  });
}

export const providers = {
  path: "/providers",
  count: (data) => data.providers.length,
  render(data, ctx) {
    return [
      toolbar(button("Добавить провайдера", () => create(ctx), "positive")),
      table(
        ["Имя", "Тип", "Секреты", "Состояние", "Последняя проверка", ""],
        data.providers.map((p) => [
          el("div", {}, el("strong", {}, p.name), p.title ? el("div", {}, muted(p.title)) : null),
          p.type,
          keyValues(Object.entries(p.secrets)),
          status(p.checkStatus),
          checked(p.lastCheckAt, p.lastCheckMessage),
          actions(
            button("Изменить", () => edit(ctx, p)),
            button("Проверить", () => check(ctx, p)),
            button("Удалить", () => remove(ctx, p), "negative")),
        ]),
        "Провайдеров нет.",
      ),
    ];
  },
};
