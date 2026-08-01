# Security Policy

**English** · [Русский](#политика-безопасности)

## Supported versions

Security fixes go into the latest released version. There are no long-term
support branches.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |
| older | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** A public report
tells everyone about the hole before there is a fix, and this server holds a
credential that grants full REST access to a Bitrix24 portal.

Two private channels, in order of preference:

1. **GitHub private advisory** — the *Security* tab of this repository →
   *Report a vulnerability*. It creates a discussion visible only to you and
   the maintainer, and it can turn into a published advisory with a CVE once
   the fix ships.
2. **Email** — the address on the maintainer's GitHub profile
   ([@john7ross](https://github.com/john7ross)).

A regular GitHub issue is the right place for ordinary bugs, questions and
feature requests — just not for vulnerabilities.

### What to include

- what an attacker can do, and what they need to have first;
- steps to reproduce, ideally the smallest possible case;
- affected version or commit;
- your assessment of the impact, if you have one.

**Never attach a real webhook URL, portal address, event dump or `.sqlite3`
file.** A Bitrix24 webhook URL is a credential: anyone holding it can read and
write everything the issuing user can. Redact it as
`https://<portal>/rest/<id>/<token>/`.

### What to expect

- acknowledgement within a few days;
- an assessment and a plan, or an explanation of why it is not a vulnerability;
- credit in the release notes when the fix ships, unless you prefer otherwise.

This is a personal open-source project, not a commercial product with an SLA.
There is no bug bounty.

## Threat model in one paragraph

The server is a gateway: it turns MCP tool calls into authenticated Bitrix24
REST calls. The valuable asset is the **webhook URL** — it is a bearer
credential with the permissions of the user who issued it. Everything worth
reporting usually comes down to that credential leaking (into logs, into tool
output, into an error message, into the event store) or to the server making a
call the operator did not intend. Two mechanisms exist specifically for this:
`sanitize.py`, the single choke point every tool's output and every log record
passes through, and `BITRIX_READ_ONLY=1`, which blocks all writes.

## Operating this server safely

- Keep the webhook in `.env` or an environment variable. `.env` is gitignored;
  keep it that way.
- Issue the webhook to a user with the **minimum scopes** the task needs, not
  to an administrator.
- Turn on `BITRIX_READ_ONLY=1` for anything that only needs to read.
- The event store (`*.sqlite3`) accumulates **real portal payloads** — staff
  emails, phone numbers, LDAP ids. It is gitignored, and it should not be
  copied around or attached to bug reports either.
- Rotate the webhook if it has ever appeared in a log, a screenshot, a chat
  message or a terminal recording. Rotation is instant: revoke it in
  *Profile → Webhooks* and issue a new one.

---

# Политика безопасности

## Поддерживаемые версии

Исправления безопасности выходят в последней версии. Веток с долгосрочной
поддержкой нет.

| Версия | Поддерживается |
|---|---|
| 0.1.x | ✅ |
| старее | ❌ |

## Как сообщить об уязвимости

**Не открывайте публичный issue по проблеме безопасности.** Публичное
сообщение рассказывает о дыре всем раньше, чем появится исправление, а этот
сервер хранит учётные данные, дающие полный REST-доступ к порталу Bitrix24.

Два приватных канала, по убыванию предпочтительности:

1. **Приватный advisory на GitHub** — вкладка *Security* этого репозитория →
   *Report a vulnerability*. Обсуждение видите только вы и сопровождающий, а
   после выхода исправления его можно опубликовать как advisory с CVE.
2. **Почта** — адрес указан в профиле сопровождающего на GitHub
   ([@john7ross](https://github.com/john7ross)).

Обычный issue — правильное место для рядовых багов, вопросов и предложений.
Но не для уязвимостей.

### Что приложить

- что может сделать атакующий и что ему для этого нужно;
- шаги воспроизведения, желательно минимальный случай;
- версия или коммит;
- ваша оценка последствий, если она есть.

**Никогда не прикладывайте настоящий URL вебхука, адрес портала, дамп событий
или файл `.sqlite3`.** URL вебхука Bitrix24 — это учётные данные: кто им
владеет, тот читает и пишет всё, что доступно выдавшему пользователю.
Замажьте его как `https://<портал>/rest/<id>/<токен>/`.

### Чего ждать в ответ

- подтверждение получения в течение нескольких дней;
- оценку и план либо объяснение, почему это не уязвимость;
- упоминание в описании релиза при выходе исправления, если вы не против.

Это личный open-source проект, а не коммерческий продукт с SLA. Вознаграждения
за найденные уязвимости нет.

## Модель угроз в одном абзаце

Сервер — шлюз: он превращает вызовы MCP-инструментов в аутентифицированные
REST-вызовы к Bitrix24. Ценный актив здесь один — **URL вебхука**, это
предъявительские учётные данные с правами выдавшего их пользователя. Почти всё,
о чём стоит сообщать, сводится к утечке этих данных (в логи, в вывод
инструмента, в текст ошибки, в хранилище событий) либо к тому, что сервер
делает вызов, которого оператор не заказывал. Ровно против этого сделаны два
механизма: `sanitize.py` — единственная точка, через которую проходит вывод
каждого инструмента и каждая запись лога, и `BITRIX_READ_ONLY=1`, который
блокирует любую запись.

## Как эксплуатировать сервер безопасно

- Держите вебхук в `.env` или в переменной окружения. `.env` в `.gitignore` —
  пусть так и остаётся.
- Выдавайте вебхук пользователю с **минимально необходимыми правами**, а не
  администратору.
- Включайте `BITRIX_READ_ONLY=1` везде, где нужно только читать.
- Хранилище событий (`*.sqlite3`) накапливает **настоящие данные портала** —
  почту сотрудников, телефоны, LDAP-идентификаторы. Оно в `.gitignore`, и его
  не стоит ни копировать по машинам, ни прикладывать к сообщениям об ошибках.
- Перевыпустите вебхук, если он хоть раз попал в лог, скриншот, переписку или
  запись экрана. Это делается мгновенно: отозвать в *Профиль → Вебхуки* и
  выдать новый.
