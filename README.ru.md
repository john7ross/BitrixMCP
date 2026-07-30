# bitrix-mcp

[English](README.md) · **Русский**

Универсальный, полнофункциональный, переносимый **MCP-сервер для REST API
Bitrix24**. Чтение **и** запись. Не привязан к какому-либо приложению — это
generic-шлюз к Bitrix24, который может подключить любой MCP-клиент или агент
(Claude Code, Claude Desktop, Cursor, Windsurf, Cline или собственный код на
Python/Node).

- **Язык:** Python + FastMCP (официальный MCP SDK)
- **Транспорты:** `stdio` (по умолчанию, максимально переносимый и надёжный) и
  **Streamable HTTP** (stateless JSON — без хрупкого долгоживущего SSE-моста)
- **Покрытие:** универсальные `b24_call` / `b24_batch` дают **100%** REST API;
  каталог из официальной доки (1930 методов) подсказывает, какой метод нужен и
  какие у него параметры; 99 типизированных инструментов закрывают частые домены
  с учётом всех нюансов.
- **События портала:** три способа получения — pull-канал (работает из-под NAT
  и VPN), приёмник исходящего вебхука, поллер — плюс архив с историей и
  пересылка в Telegram: [docs/EVENTS.ru.md](docs/EVENTS.ru.md)

## Зачем это и что исправлено

Пересобрано по полевым заметкам о прежней обёртке. Баги, из-за которых всё
затевалось, исправлены *архитектурно*, а не заплатками:

| Как было | Как теперь |
|---|---|
| `filter` молча игнорировался (`groups_list`, `users_list`), выгрузка всего портала → таймауты | Параметры уходят **JSON-телом**, поэтому вложенные `filter`/`select`/`order` парсятся Битриксом корректно. Честная пагинация с лимитом страниц. |
| Ошибки доступа подменялись фейковым «0 результатов» (`read_pipelines` и др.) | Ошибки **никогда** не глотаются — `error`/`error_description` от Битрикса всплывает со своим `code` (напр. `ACCESS_DENIED`). |
| `calendar_list` возвращал 0 без явного `ownerId` | `owner_id` **авторезолвится** в текущего пользователя. |
| Канбан Scrum читался не оттуда | Зашит правильный флоу: фильтр активного спринта + `tasks.api.scrum.kanban.getStages` (`b24_scrum_board` делает это одним вызовом). |
| Обрывы SSE-сессии `mcp-remote` / зависания | Приоритет **stdio** (без моста) или **stateless Streamable HTTP**. |
| У `department.get` **вообще нет серверного фильтра** (недокументированное ограничение самого Битрикса) — `filter` молча игнорировался, отдавалось всё дерево отделов (95+ строк) целиком | `b24_department_get` фильтрует **на стороне клиента** после полной выгрузки, так что `filter`/`ID` реально сужают результат, а не тихо дампят всё. |
| Битрикс иногда отдаёт ошибку как `{"error": "", "error_description": "Access denied."}` — с **пустой строкой** в коде ошибки, которую наивная проверка на истинность (`if data.get("error")`) пропускает, теряя код и сообщение в общем HTTP-фоллбэке | Проверяется **наличие ключа**, а не истинность значения — `code`/`message` всегда отражают то, что реально сказал Битрикс. |
| `calendar.event.add` / `.update` молча **теряют `attendees`**, если не выставлен `is_meeting` — 200 OK, событие создано, никто не приглашён, ошибки нигде нет | `is_meeting` **автоматически выставляется в `'Y'`**, когда `attendees` не пуст и явно не передан. |
| Перенос задачи на **Scrum-доске спринта** через `STAGE_ID` в `tasks.task.update` принимается без ошибки и корректно читается назад, но карточка **физически не двигается** на реальной доске (проверено вживую: обновление страницы — карточка всё ещё в старой колонке) | Новый `b24_scrum_task_move` вызывает предназначенный именно для доски `tasks.api.scrum.kanban.addTask` — он реально переносит карточку. `b24_task_update`/`b24_tasks_list` теперь документируют эту ловушку, а не тихо вводят в заблуждение. |

## Установка

```bash
uv sync                       # создать venv + поставить зависимости
# либо как инструмент в PATH:
uv tool install .             # даёт команду `bitrix-mcp`
```

## Настройка

Задай вебхук по умолчанию (см. `.env.example`):

```bash
export BITRIX_WEBHOOK_URL="https://your-portal.bitrix24.ru/rest/1/xxxxxxxx/"
# опционально:
export BITRIX_READ_ONLY=1     # заблокировать любую запись
```

Вебхук берётся в Битриксе: *Профиль → Вебхуки → входящий вебхук*, формат
`https://<портал>/rest/<user_id>/<токен>/`. Токен — это доступ; держи его вне
системы контроля версий (`.env` в `.gitignore`).

**Приоритет авторизации на вызов:** `personal_webhook` → `webhook_url` →
HTTP-заголовок `X-B24-Webhook` → `BITRIX_WEBHOOK_URL`. Передавай
`personal_webhook`, чтобы действовать (и писать) от имени конкретного
пользователя.

## Запуск

```bash
bitrix-mcp                         # stdio (по умолчанию)
bitrix-mcp --http                  # Streamable HTTP на 127.0.0.1:8000/mcp
bitrix-mcp --http --host 0.0.0.0 --port 5015   # общий сетевой сервис
```

## Подключение клиента

**Claude Code (stdio, рекомендуется):**
```bash
claude mcp add -s user bitrix24 -- uv run --directory C:/Scripts/BitrixMCP bitrix-mcp
```

**Общий `.mcp.json` в репозитории (stdio):**
```json
{
  "mcpServers": {
    "bitrix24": {
      "command": "uv",
      "args": ["run", "--directory", "C:/Scripts/BitrixMCP", "bitrix-mcp"],
      "env": { "BITRIX_WEBHOOK_URL": "https://your-portal.bitrix24.ru/rest/1/xxxx/" }
    }
  }
}
```

**Claude Code (HTTP):**
```bash
bitrix-mcp --http --port 5015          # затем на клиенте:
claude mcp add -s user --transport http bitrix24 http://HOST:5015/mcp
```

**Claude Desktop (stdio)** — `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "bitrix24": {
      "command": "uv",
      "args": ["run", "--directory", "C:/Scripts/BitrixMCP", "bitrix-mcp"],
      "env": { "BITRIX_WEBHOOK_URL": "https://your-portal.bitrix24.ru/rest/1/xxxx/" }
    }
  }
}
```
(Для удалённого HTTP-инстанса Desktop всё ещё нужен мост `mcp-remote`; вариант с
stdio выше этого избегает.)

## Каталог инструментов (99)

**Универсальные** — `b24_call`, `b24_batch`, `b24_test_connection`, `b24_list_methods`
**CRM** — `b24_crm_list`, `b24_crm_get`, `b24_crm_fields`, `b24_crm_add`, `b24_crm_update`, `b24_crm_delete`, `b24_crm_timeline_comment_add`, `b24_crm_timeline_comment_list`, `b24_crm_category_list` (воронки), `b24_crm_status_list` (стадии/справочники), `b24_crm_activity_list`, `b24_crm_activity_add`, `b24_crm_activity_delete`, `b24_crm_productrows_get`, `b24_crm_productrows_set`, `b24_crm_currency_list`, `b24_crm_requisite_list`, `b24_crm_deal_contacts_get`, `b24_crm_deal_contacts_set` (классические сущности *и* SPA через `entity_type_id`)
**Задачи** — `b24_tasks_list`, `b24_task_get`, `b24_task_add`, `b24_task_update`, `b24_task_complete`, `b24_task_delete`, `b24_task_comments_list`, `b24_task_comment_add`, `b24_task_stages_get`, `b24_task_checklist_list`, `b24_task_checklist_add`, `b24_task_elapsed_add`, `b24_task_result_list`
**Scrum** — `b24_scrum_sprint_list`, `b24_scrum_kanban_stages`, `b24_scrum_board`, `b24_scrum_task_move`
**Календарь** — `b24_calendar_event_list`, `b24_calendar_section_list`, `b24_calendar_event_add`, `b24_calendar_event_update`, `b24_calendar_event_delete`
**Диск** — `b24_disk_storage_list`, `b24_disk_folder_items`, `b24_disk_file_get`, `b24_disk_file_content` (серверное скачивание → base64), `b24_disk_folder_add`, `b24_disk_file_upload`, `b24_disk_file_delete`
**Пользователи/структура** — `b24_user_get`, `b24_user_search`, `b24_user_current`, `b24_department_get`
**Группы (рабочие группы)** — `b24_group_list`, `b24_group_users`, `b24_group_create`, `b24_group_update`, `b24_group_delete`
**Мессенджер** — `b24_im_recent`, `b24_im_dialog_messages`, `b24_im_message_add`, `b24_im_notify_personal`, `b24_im_user_get`, `b24_im_chat_create`, `b24_im_chat_user_add`, `b24_feed_post_add`
**Списки (универсальные списки)** — `b24_lists_get`, `b24_lists_element_list`, `b24_lists_element_add`, `b24_lists_element_update`, `b24_lists_element_delete`
**Каталог / товары** — `b24_catalog_list`, `b24_catalog_section_list`, `b24_catalog_product_list`, `b24_catalog_product_get`, `b24_catalog_product_add`, `b24_catalog_product_update`, `b24_crm_product_list`
**Заказы (магазин)** — `b24_sale_order_list`, `b24_sale_order_get`
**Документы** — `b24_documentgenerator_templates`, `b24_documentgenerator_add`
**Бизнес-процессы** — `b24_bizproc_template_list`, `b24_bizproc_start`
**Телефония** — `b24_telephony_statistics`

Всё, что здесь не типизировано, доступно через `b24_call` (например почта,
открытые линии, запись корзины заказов, admin/app-методы).

## Документация

- [docs/USAGE.ru.md](docs/USAGE.ru.md) — как управлять инструментами: авторизация, фильтры, пагинация, batch, ошибки, рецепты.
- [ARCHITECTURE.ru.md](ARCHITECTURE.ru.md) — компоненты, поток вызова, развёртывание; диаграммы в [docs/diagrams/](docs/diagrams/).
- [ROADMAP.ru.md](ROADMAP.ru.md) — статус, история и границы «вне охвата».

## Разработка

```bash
uv sync                 # установить runtime + dev-зависимости
uv run pytest -q        # офлайн-тесты (портал не нужен)
uv run python scripts/smoke.py "<вебхук>"   # живая карта доступа (запускать из сети с доступом к порталу)
```

Диаграммы перегенерируются: `java -jar plantuml.jar -tpng docs/diagrams/*.puml`.

## О лимитах

- `fetch_all=true` ограничен `BITRIX_MAX_PAGES` (по умолчанию 40 страниц ≈ 2000
  записей) и выставляет `truncated: true` при достижении лимита — молча не
  обрывает выборку.
- Read-only-гейт классифицирует запись по глаголу метода; типизированные
  write-инструменты классифицируются всегда верно. `b24_call`/`b24_batch`
  используют эвристику.

## Поддержать автора

<p align="center">
  <img src="donate-qr.png" alt="Donate QR" width="200"/>
</p>

BTC: bc1q3frrup5neh7nhfg944etu2agd4j9u0vg3jyee6

ETH(Arbitrum): 0x43B349d8Cea83215D707EBa3bc35e9917f746b0a

TRX: THSzvy49KNeqRjXsGkurh2A5G4avV4RgN4

XRP: rLWZjS3DMupC4ZdXCX3BVYn4dEtC3iNhgy

SOL: 3xwfybxJ6Tz5t6pjBBkL5yYQCZo6wfbv932UNA4ThdP8

ADA: addr1q926ys75jp5wn2pv32a3t8r8pdhr7w02v0t9j4a8pmg0ruww5rlkctu4lnz2hfcwa5qfn3zhsd0s23r22uqwzx9gu6cq5c4e76

TON: UQC4qlAOD9Nly4K_66GJ_yCsSM3x2sB0vZ2GrBQbc--gZUui

DOGE: DTjNYmbtymzcjUiV4MsZY8MP4dM7MJ6qLC

XMR: 44qRqM6YtnxXUhkgCFqDDrKMPjWriu69FLBoop8Kwp7e1VQsBUJoVQ8JYQjfMV5C6uidTUgSSyoJ65mq8aYG2esZ1rrqfwt
