# Архитектура и происхождение

Дата: 2026-10-21.

SDK выделен из `F:/projects/comfy-meta-viewer`, исходная ревизия
`fb2ec82f98e0de121f68e31584eb064f99582887`.

| Исходный компонент | Компонент SDK | Изменение |
| --- | --- | --- |
| `app/ai/antigravity_host.py` | `runtime.py` | Нативное обнаружение, Engine, Session, cleanup; удалены UI probe, глобальный shared-client и application run registry |
| `app/ai/managed_process.py` | `_process.py` | Только необходимый супервизор: Windows Job Object, флаги процесса, завершение дерева |
| `app/ai/cli.py` | `_sanitize.py`, `errors.py` | Выделены диагностика и независимое исключение |
| `app/ai/execution/antigravity.py` | `client.py` | Универсальные object schemas, text/vision, reasoning, trajectory, usage; отсутствует PromptResult/PromptCompiler |
| `scripts/probe_antigravity_image.py` | `Client.generate_image()` | Нативный image tool, проверка actual model и получение байтов до cleanup |
| `app/ai/refusal.py` | `_refusal.py` | Сохранено различение provider block и model refusal |

В исходном адаптере high-level результат связан с промптами VEIL. В SDK этот
слой заменён схемой вызывающего проекта и локальной JSON Schema validation.
AGPL-3.0-only и исходный LICENSE сохранены. SDK не включает бинарники, токены,
профиль, настройки, инструкции Create, БД или историю VEIL.

## Владение и время жизни

`Client` лениво создаёт свой `Engine`. Движок использует отдельный временный
gemini/config и workspace, пустой MCP config, свежий локальный CSRF и нативную
OS-keyring авторизацию. Конфигурация plugins/MCP из пользовательского профиля
не импортируется. Внешние API-key переменные удаляются из environment процесса.

`--app_data_dir` имеет имя `antigravity-engine-sdk`; override IDE/user-agent
остаётся `antigravity`, как у проверенного исходного адаптера. Движок работает
на loopback. У клиента нет зависимости от сервера VEIL, от его глобального
shutdown или от настроек приложений, которые установят SDK.

Вызовы клиента сериализованы. Каждый запрос создаёт отдельный cascade и каталог.
Session ограничивает запрос своим deadline/cancel callback, а процесс остаётся
доступным для следующего запроса. Запрос, ожидающий блокировку, тоже можно отменить;
он не создаёт cascade. `close()` может прервать активный запрос и закрывает только
процесс данного клиента. При аварии следующий вызов создаёт новый движок.

Отмена охватывает также ошибку отправки после `StartCascade`, а не только polling.
Cancel RPC выполняется с отдельным коротким timeout; при его неудаче процесс
закрывается. Windows Job Object и `--exit_on_stdin_close` сохраняют связь с
родительским процессом; fallback завершает принадлежащее SDK дерево.

## Слои API

- `Client.chat`, `generate`: только finish, независимый объектный результат,
  media attachments, нативный reasoning override, события и usage.
- `Client.generate_image`: только generate_image и finish; planner и image model
  учитываются отдельно; expected_model — assertion, не выдуманный model parameter.
- `Client.models`, `authentication`: native discovery без продуктовой политики.
- `Client.rpc`: расширяемый доступ к текущему локальному протоколу.
- `Engine`, `Session`, `Installation`, `find_installation`: низкий уровень для
  собственного жизненного цикла, бесед и инструментов.

Доступность инструмента в бинарнике или строка в каталоге не подтверждают его
практическую работу. SDK не заявляет полный typed wrapper всех внутренних RPC.
Неизвестные поля могут молча отбрасываться. Повторные попытки и смена модели
автоматически не выполняются: вызывающий проект сохраняет контроль над расходом
и ошибками. Секреты не экспортируются из OS keyring; raw RPC остаётся расширенным
доступом, поэтому его результаты не следует без разбора писать в публичные логи.

## Подключение других проектов

Установите пакет через local path, editable install или wheel. Импортируйте
только `antigravity_engine_sdk`; не добавляйте исходный VEIL в sys.path.
Выберите модели из `client.models()` и сохраняйте точные catalog IDs.
Завершайте Client вместе с жизненным циклом приложения. Серверным приложениям
обычно достаточно одного Client на worker; для параллельной генерации используйте
несколько клиентов, учитывая лимиты аккаунта и число процессов.

Исходный адаптер VEIL в этой задаче не мигрируется на SDK. Это отдельное изменение,
которое должно сохранить его product contracts и пройти его backend/UI проверки.
