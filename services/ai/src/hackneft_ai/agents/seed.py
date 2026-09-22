"""Шесть причин и два режима. Однократные обновления с архивом прежних карточек."""

from sqlalchemy import select

from hackneft_common.ai import CreateAgentRequest, CreateInstructionRequest

from ..db.database import Database
from ..db.schema import AgentRow, CatalogMigrationRow, InstructionRow

COMMON = (
    "Работай по участку 24-2000 и только по причинам из задачи. "
    "Порог серы в продукте — 10 мг/кг (sulfur_threshold во входных данных); ранний порог "
    "предупреждения — настройка платформы (policy.warning), а не второй порог. "
    "Проверяй время, источник, единицы и достоверность измерений. "
    "Каждое утверждение подкрепляй числами из входных данных: параметр, значение с единицей "
    "(parameters во входных данных), время и изменение за период. Формулировки без параметра "
    "и числа — «проверить сырьё», «усилить контроль», «тренд роста» — недопустимы: пиши "
    "«Q20 8 900 мг/кг, +600 за 1 ч», «ПАК 9,99 мг/кг, +1,2 мг/кг за 30 мин». "
    "Не выдумывай пределы оборудования, задержку процесса и эффект изменения уставки; "
    "целевое значение называй, только если оно следует из данных. "
    "Любое изменение — предложение оператору, не команда оборудованию. "
)
"""Общая часть промптов агентов. Только в промптах: инструкции её не повторяют, потому что
промпт сессии — это промпт карточки и за ним тексты инструкций."""

INSTRUCTIONS = (
    CreateInstructionRequest(
        id="sulfur-risk",
        title="1. Риск по сере",
        description="Сера растёт или близка к порогу 10 мг/кг: возможен выход выше порога.",
        text="""Применяй при sulfur_risk. Назови последнее значение ПАК, его изменение за 30 и
60 мин, запас до порога 10 мг/кг, возраст и значение последнего ЛИМС со временем отбора.
Разделяй приближение к порогу и подтверждённое превышение. Сопоставь рост с подачей сырья
ht_f9, газом ht_f25 и ht_f2, температурой ht_t6 и давлением ht_p13: для каждого — значение и
изменение за тот же период. До снятия риска рост выпуска не предлагай. Назови показатель и
значение, по которому оператор увидит улучшение.""",
    ),
    CreateInstructionRequest(
        id="lab-result",
        title="2. Новый результат ЛИМС",
        description="Лаборатория уточняет качество и помогает проверить показания ПАК.",
        text="""Применяй при lab_result. Время отбора пробы отличается от времени публикации
результата. Сравни ЛИМС с ПАК на момент отбора и назови оба значения и разницу в мг/кг, а не
сравнивай с последней точкой ПАК. Если время отбора неизвестно, расхождение приборов не
утверждай. Определи, подтверждён ли риск превышения порога и нужна ли проверка анализатора
или повторная проба. Один результат ниже порога без устойчивого снижения ПАК не разрешает
увеличить нагрузку.""",
    ),
    CreateInstructionRequest(
        id="feed-changed",
        title="3. Изменилась подача сырья",
        description="Изменение нагрузки может повлиять на глубину очистки и запас до порога.",
        text="""Применяй при feed_changed. Назови ht_f9 и ht_q20 до и после события с
единицами и изменением в процентах; сопоставь с газом, температурой, давлением и серой ПАК за
тот же период. Изменение расхода само по себе не доказывает ухудшение состава сырья. Учти,
прошло ли время эффекта прежнего действия. При риске превышения порога оцени возврат к
прежнему значению ht_f9 и назови его; при запасе до порога — передай оценку производству.
Без данных о допустимых пределах новую уставку не назначай.""",
    ),
    CreateInstructionRequest(
        id="gas-supply",
        title="4. Изменилось обеспечение газом",
        description="Падение газового обеспечения относительно сырья может ухудшить очистку.",
        text="""Применяй при gas_supply. Назови ht_f25 и ht_f2, их отношение к ht_f9 до и после
события и ht_p13. Разделяй уменьшение расхода газа и рост подачи сырья. Отношение расходов
используй только при ненулевом знаменателе и не считай его концентрацией водорода. Оцени
риск недостатка газа по сере ПАК и запасу до порога; предложи проверить подачу газа с
указанием, какое отношение было до снижения. Нехватку газа повышением температуры не
компенсируй.""",
    ),
    CreateInstructionRequest(
        id="regime-changed",
        title="5. Изменились температура или давление",
        description="Смена режима влияет на очистку; резкий сдвиг требует проверки причины.",
        text="""Применяй при regime_changed. Назови ht_t6 и ht_p13 до и после изменения,
подачу сырья и серу ПАК за тот же период. Порог изменения, заданный обработчиком, не равен
аварийному пределу; опасной температуру без известной границы не называй. Учти плановое
изменение и незавершённый эффект предыдущего действия. При подтверждённом ограничении
приоритет у защиты; иначе назови показатель и значение, при котором режим оценить заново.
Несколько противоречащих друг другу изменений уставок не выдавай.""",
    ),
    CreateInstructionRequest(
        id="data-quality",
        title="6. Недостоверные или устаревшие данные",
        description="Без надёжных измерений нельзя обосновать изменение режима.",
        text="""Применяй при data_quality, включая восстановление данных. Перечисли коды
отсутствующих, устаревших или недостоверных измерений и время их последнего достоверного
значения. Значение 307, пропуск и отрицательное значение физическим показанием не считай и
пропуски догадками не заполняй. Предложи проверить конкретный источник, связь или
анализатор. До восстановления оптимизацию не рекомендуй; после — оцени накопленные причины
заново на свежем снимке.""",
    ),
    CreateInstructionRequest(
        id="protection-mode",
        title="Режим: защита",
        description="Сохранить качество и режим; задать ограничения для производства.",
        text="""Проверь каждую причину по исходным данным: значение, изменение за 30–60 мин,
запас до порога, свежесть и достоверность. Сопоставь с известными пределами оборудования, а
если их нет, так и скажи. Отделяй гипотезу от измеренного факта; отсутствие данных не
означает безопасность. При риске превышения порога запрети производству увеличивать подачу
сырья и снижать температуру и назови текущие значения этих параметров.""",
    ),
    CreateInstructionRequest(
        id="production-mode",
        title="Режим: производство",
        description="Искать улучшение выпуска или затрат только в пределах, проверенных защитой.",
        text="""Опирайся на итог защиты. При риске превышения порога или недостатке данных
отложи оптимизацию и назови значение, из-за которого она отложена. При допустимом режиме
сравни сохранение режима и один вариант улучшения: параметр, текущее и предлагаемое
значение, запас до порога по сере. Учитывай незавершённые эффекты прежних действий. Выгоду в
процентах называй только при расчёте по данным; без доказуемой выгоды сохрани режим.""",
    ),
)
CAUSE_IDS = [item.id for item in INSTRUCTIONS[:6]]
PROTECTION_PROMPT = (
    COMMON
    + """Ты — агент защиты. Оцени только причины из задачи по исходным данным.
Других агентов не вызывай. Отвечай коротко, без рассуждений, не больше четырёх строк:
строка 1 — вывод до 60 символов с числом: «Защита нужна: ПАК 9,99 мг/кг, +1,2 за 30 мин»,
«Риск не подтверждён: ПАК 6,1 мг/кг» или «Данных недостаточно: нет T6 с 10:20»;
строка 2 — одно приоритетное действие с параметром, текущим и целевым значением, если нужно;
строки 3–4 — ограничения для производства с параметрами и значениями."""
)

PRODUCTION_PROMPT = (
    COMMON
    + """Ты — агент производства. Учитывай итог защиты из задачи и её ограничения.
Других агентов не вызывай. Отвечай коротко, без рассуждений, не больше четырёх строк:
строка 1 — вывод до 60 символов с числом: «Оптимизация отложена: ПАК 9,99 мг/кг»,
«Сохранить режим: запас 3,8 мг/кг» или «Можно: F9 до 230 т/ч»;
строки 2–4 — вариант с текущим и предлагаемым значением, ограничение и критерий проверки
с показателем и сроком."""
)

ADVISOR_PROMPT = (
    COMMON
    + """Ты — советник оператора и распорядитель агентов. Частоту и причины запуска
определяет платформа. Исходные данные приложены к задаче; агентам их передаёт
include_input=true дословно, поэтому в task их не пересказывай.
Порядок работы строго такой:
1. run_agent с agent_id="protection" и include_input=true; в task перечисли причины запуска
   одной строкой.
2. После итога защиты — run_agent с agent_id="production" и include_input=true; в task —
   причины и итог защиты дословно.
3. attempt_completion без аргументов, затем итоговый ответ на запрос сервиса.
Агентов запускай по одному, каждого один раз. Если агент не завершился или run_agent
недоступен, продолжай без его итога и так и скажи; не имитируй итоги агентов.
Итог — решение одного из трёх типов: режим не менять, проверить или изменить режим.
Действие добавляй только при реальной необходимости; в каждом — параметр, его текущее
значение и то, что именно сделать. При противоречии приоритет у защиты. Ответа оператора
не жди и подтверждения не проси. Пиши коротко: оператор не читает длинный текст."""
)

AGENTS = (
    CreateAgentRequest(
        id="protection",
        name="Агент защиты",
        description="Оценивает риск по шести причинам и задаёт ограничения для производства.",
        system_prompt=PROTECTION_PROMPT,
        instructions=[*CAUSE_IDS, "protection-mode"],
    ),
    CreateAgentRequest(
        id="production",
        name="Агент производства",
        description="Оценивает улучшение режима с учётом результата защиты.",
        system_prompt=PRODUCTION_PROMPT,
        instructions=[*CAUSE_IDS, "production-mode"],
    ),
    CreateAgentRequest(
        id="advisor",
        name="Советник",
        description="Запускает агентов защиты и производства и выдаёт один короткий совет.",
        system_prompt=ADVISOR_PROMPT,
        # Инструкции по причинам применяют агенты; распорядителю они только удлиняют контекст.
        instructions=[],
    ),
)

LEGACY_IDS = {
    "protection-first",
    "wait-for-effect",
    "sulfur-norm",
    "analyzer-trust",
    "protection-actions",
    "production-actions",
    "significant-gain",
    "load-limit",
    "quality-norms",
    "tag-units",
    "mode-selection",
    "operator-answer",
}


async def seed_defaults(db: Database) -> None:
    """Однократные обновления штатного каталога по порядку их появления."""
    await _seed_six_reasons(db)
    await _seed_advisor_agents(db)
    await _seed_concrete_texts(db)


CONCRETE_MIGRATION = "concrete-advice"


async def _seed_concrete_texts(db: Database) -> None:
    """Порог вместо нормы, обязательные числа в выводах и инструкции без повтора промпта.

    Прежде инструкции режимов начинались с общей части промпта агента, и в промпте сессии она
    стояла дважды. Обновляются промпты трёх штатных агентов и тексты восьми штатных
    инструкций; прежние тексты архивируются. Модель и перечни инструкций агентов не меняются.
    """
    async with db.write() as tx:
        if await tx.get(CatalogMigrationRow, CONCRETE_MIGRATION) is not None:
            return
        agents = [row for spec in AGENTS if (row := await tx.get(AgentRow, spec.id)) is not None]
        instructions = [
            row
            for item in INSTRUCTIONS
            if (row := await tx.get(InstructionRow, item.id)) is not None
        ]
        tx.add(
            CatalogMigrationRow(
                id=CONCRETE_MIGRATION,
                snapshot={
                    "agents": [
                        {"id": row.id, "system_prompt": row.system_prompt} for row in agents
                    ],
                    "instructions": [
                        {"id": row.id, "description": row.description, "text": row.text}
                        for row in instructions
                    ],
                },
            )
        )
        prompts = {spec.id: spec.system_prompt for spec in AGENTS}
        for row in agents:
            row.system_prompt = prompts[row.id]
        texts = {item.id: item for item in INSTRUCTIONS}
        for row in instructions:
            row.description = texts[row.id].description
            row.text = texts[row.id].text


ORCHESTRATOR_MIGRATION = "advisor-orchestrator"


async def _seed_advisor_agents(db: Database) -> None:
    """Промпты штатных агентов под запуск агентов советником и короткий итог по схеме.

    Меняются промпты и описания трёх штатных карточек, а у советника снимаются инструкции по
    причинам. Модель и инструкции, добавленные пользователем, сохраняются. Прежнее состояние
    архивируется.
    """
    async with db.write() as tx:
        if await tx.get(CatalogMigrationRow, ORCHESTRATOR_MIGRATION) is not None:
            return
        rows = [await tx.get(AgentRow, spec.id) for spec in AGENTS]
        tx.add(
            CatalogMigrationRow(
                id=ORCHESTRATOR_MIGRATION,
                snapshot={
                    "agents": [
                        {
                            "id": row.id,
                            "description": row.description,
                            "system_prompt": row.system_prompt,
                            "instructions": row.instructions,
                        }
                        for row in rows
                        if row is not None
                    ]
                },
            )
        )
        for spec, row in zip(AGENTS, rows, strict=True):
            if row is not None:
                row.description = spec.description
                row.system_prompt = spec.system_prompt
                if spec.id == "advisor":
                    row.instructions = [ref for ref in row.instructions if ref not in CAUSE_IDS]


async def _seed_six_reasons(db: Database) -> None:
    """Обновляет штатные карточки один раз; модель и пользовательские дополнения сохраняются.

    Старые инструкции, используемые сторонними агентами, остаются. Полные прежние карточки
    архивируются в той же транзакции. Последующие правки каталога запуск сервиса не затирает.
    """
    async with db.write() as tx:
        if await tx.get(CatalogMigrationRow, "six-reasons-v1") is not None:
            return
        old_instructions = list((await tx.scalars(select(InstructionRow))).all())
        old_agents = list((await tx.scalars(select(AgentRow))).all())
        tx.add(
            CatalogMigrationRow(
                id="six-reasons-v1",
                snapshot={
                    "instructions": [
                        {
                            "id": row.id,
                            "title": row.title,
                            "description": row.description,
                            "text": row.text,
                        }
                        for row in old_instructions
                    ],
                    "agents": [
                        {
                            "id": row.id,
                            "name": row.name,
                            "description": row.description,
                            "system_prompt": row.system_prompt,
                            "instructions": row.instructions,
                            "model": row.model,
                        }
                        for row in old_agents
                    ],
                },
            )
        )
        for item in INSTRUCTIONS:
            row = await tx.get(InstructionRow, item.id)
            if row is None:
                tx.add(InstructionRow(**item.model_dump(by_alias=False)))
            # Совпадение с вручную созданным новым id не затираем.
        builtin_ids = {item.id for item in AGENTS}
        retained = {
            ref for row in old_agents if row.id not in builtin_ids for ref in row.instructions
        }
        for agent_spec in AGENTS:
            agent_row = await tx.get(AgentRow, agent_spec.id)
            if agent_row is None:
                tx.add(AgentRow(**agent_spec.model_dump(by_alias=False)))
            else:
                extras = [
                    ref
                    for ref in agent_row.instructions
                    if ref not in LEGACY_IDS and ref not in agent_spec.instructions
                ]
                agent_row.name = agent_spec.name
                agent_row.description = agent_spec.description
                agent_row.system_prompt = agent_spec.system_prompt
                agent_row.instructions = [*agent_spec.instructions, *extras]
        for row in old_instructions:
            if row.id in LEGACY_IDS and row.id not in retained:
                await tx.delete(row)
