# Antigravity Engine SDK for Go

Нативная Go-версия SDK `0.5.0`. Модуль:
`github.com/Lotargo/antigravity_engine_sdk/go`. Python и agy CLI не нужны.
Go 1.23+, для live — установленный Antigravity 2.x с выполненным входом.
Лицензия AGPL-3.0-only, см. [LICENSE](LICENSE).

## Быстрая проверка без приложения

Отдельный консольный стенд находится в [testbench/](testbench/README.md).
Из корня репозитория:

```powershell
.\go\testbench\run.ps1
```

По умолчанию это **MOCK**: отдельный дочерний процесс реализует локальный
Connect JSON RPC без провайдера и авторизации. Проверяются каталог, текст,
JSON Schema, media, отмена, concurrent metadata, восстановление и image
artifacts. Mock подтверждает локальный протокол; inference проверяется отдельно.

```powershell
cd F:\projects\antigravity_engine_sdk\go
go test ./...
go vet ./...
go run ./testbench --live --case catalog
go run ./testbench --live --case all --model gemini-3.8-flash-low
go run ./testbench --live --case image --expected-image-model gemini-3.1-flash-image
```

Отчёты: новый `reports/go-<mode>-<timestamp>/` с `report.json`, vision fixture
и изображениями. `--output` задаёт новый каталог; существующий отклоняется.
Live `all` делает реальные запросы text/schema/vision/cancel/recovery;
image generation запускается отдельно. ID выбирается из каталога аккаунта.

## Подключение

Пока модуль не опубликован, добавьте локальный replace в `go.mod` потребителя:

```go
require github.com/Lotargo/antigravity_engine_sdk/go v0.0.0

replace github.com/Lotargo/antigravity_engine_sdk/go => F:/projects/antigravity_engine_sdk/go
```

```go
package main

import (
    "context"
    "fmt"
    "log"
    "time"
    ag "github.com/Lotargo/antigravity_engine_sdk/go"
)

func main() {
    client, err := ag.NewClient(ag.Config{Timeout: 180 * time.Second})
    if err != nil { log.Fatal(err) }
    defer client.Close()

    ctx := context.Background()
    models, err := client.Models(ctx, false)
    if err != nil { log.Fatal(err) }
    fmt.Println(models)

    result, err := client.Chat(ctx, ag.Request{
        Prompt: "Объясни принцип работы объектива в двух предложениях.",
        Model: "gemini-3.8-flash-low", // Точный ID из своего каталога.
    })
    if err != nil { log.Fatal(err) }
    text, err := result.Text()
    if err != nil { log.Fatal(err) }
    fmt.Println(text, result.Usage)
}
```

`NewClient` не запускает процесс. Первый запрос лениво создаёт свой движок;
последующие переиспользуют его. Каждый high-level запрос создаёт свежий cascade
и временный workspace. Разные клиенты владеют разными процессами.
`defer client.Close()` обязателен: завершает только свой процесс и удаляет
временный профиль. `Close` терминален.

## Структура, media и отмена

### Модели без finish: обычный текст

```go
result, err := client.Complete(ctx, ag.Request{
    Model: "tab_flash_lite_preview", // Или tab_jump_flash_lite_preview.
    Prompt: "Complete the Python function. Plain code only:\ndef add(a, b):\n    return ",
})
if err != nil { return err }
text, err := result.Text()
```

`Complete` явно отключает инструменты, не требует JSON Schema и читает
завершённый native `plannerResponse.response`. `OutputMode=planner_text`.
Это самостоятельный контракт; `Chat`/`Generate` по-прежнему требуют native
`finish`, а автоматического fallback нет. Schema в Complete запрещена.
Prompt не получает transport hints о finish; default MaxInvocations=1.
Native текст сохраняется буквально: лишние кавычки, escape-последовательности
и ошибки сгенерированного кода автоматически не исправляются.

Обе `tab_*` проверены в этом режиме. Это текстовое использование моделей;
Для нативных Supercomplete/Tab-to-Jump с diff/cursor используйте `EditorSession`,
описанный ниже; текстовый `Complete` не возвращает их редакторный контракт.
Подробнее: [TAB-MODELS-2026-10-21.md](TAB-MODELS-2026-10-21.md).

### Структурированный результат

```go
media, err := ag.MediaFromFile("photo.png")
if err != nil { return err }
ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
defer cancel() // Для кнопки Stop вызовите cancel().
result, err := client.Generate(ctx, ag.Request{
    Prompt: "Опиши изображение.",
    Model: "gemini-3.8-flash-low",
    Schema: ag.Object{
        "type": "object",
        "properties": ag.Object{"description": ag.Object{"type": "string"}},
        "required": []string{"description"},
        "additionalProperties": false,
    },
    Media: []ag.Media{media},
    Thinking: "low",
    OnProgress: func(e ag.ProgressEvent) { fmt.Println(e.Phase) },
})
```

Схема проверяется до запуска процесса, результат — локально после нативного
`finish`. Поддерживаются локальные `$defs`/`$ref`; внешние ресурсы заблокированы
в том числе на уровне loader. Валидатор:
[santhosh-tekuri/jsonschema v6](https://github.com/santhosh-tekuri/jsonschema).
Media: 1 байт — 20 MiB на элемент, явный MIME type. Проверяется receipt media
в native user-input step; это подтверждает транспорт, не качество vision.

`Thinking`: `minimal`, `low`, `medium`, `high` или пустая строка для native default.
Поддержка override зависит от модели. `MaxInvocations=0` выбирает default 3.
Progress — синхронные polling snapshots с фазами `starting`, `working`,
`trajectory`, `completed`. Сериализованные методы того же клиента нельзя
вызывать из callback. Callback panic распространяется после cleanup/cancel.

Context cancellation и deadline включают очередь, запуск и RPC. Ошибка отмены
завершает процесс; следующий вызов создаёт новый. `Close` прерывает текущий
и ожидающие запросы. После обычной отмены клиент можно использовать снова.

## Изображения и native RPC

```go
result, err := client.GenerateImage(ctx, ag.ImageRequest{
    Prompt: "A blue ceramic cup on a white table. Square image.",
    Planner: "gemini-3.8-flash-low",
    ExpectedModel: "gemini-3.1-flash-image",
})
if err != nil { return err }
err = result.Images[0].Save("cup.jpg") // Расширение выбирайте по MIMEType.

catalog, err := client.RPC(ctx, "GetAvailableModels", nil,
    ag.RPCOptions{Concurrent: true})
```

`ExpectedModel` проверяет actual model, а не выбирает image-модель. Байты
сохраняются до удаления workspace. `Save` не перезаписывает файл. Внешние URI
не скачиваются; file output читается только из собственного output directory
с проверкой symlink. Editing fidelity отдельно не подтверждена.

`RPC` даёт произвольный native метод; вызывающий проект управляет порядком и
побочными эффектами. `Concurrent=true` читает метаданные из уже запущенного
движка во время generation; cold start сериализован. Отдельный экспортируемый
`Engine` в Go отсутствует: raw RPC доступен через `Client`.

## Нативный редактор: Supercomplete и Tab-to-Jump

`EditorSession` держит отдельный native процесс и временный workspace.
`Sync` получает фактические снимки текста, без вызова модели; первая версия
становится исходной. Последние два различных снимка сохраняются с исходным
временем наблюдения. Реальные файлы проекта API не меняет.

```go
editor, err := ag.NewEditorSession(ag.Config{})
if err != nil { log.Fatal(err) }
defer editor.Close()
doc := ag.EditorDocument{Name: "sample.py", Language: "python",
    Text: "def add(a, b):\n    return a + b\n", CursorOffset: 26}
if err := editor.Sync(ctx, doc); err != nil { log.Fatal(err) }
doc.Text = "def add(a, b):\n    return \n"
doc.CursorOffset = len("def add(a, b):\n    return ")
if err := editor.Sync(ctx, doc); err != nil { log.Fatal(err) }
jump, err := editor.TabToJump(ctx, nil) // независимо от Supercomplete и возраста правки
if err != nil { log.Fatal(err) }
if jump.Status == "jump" {
    fmt.Println(jump.Response.JumpPosition.Row, jump.Response.JumpPosition.Col)
}
edit, err := editor.Supercomplete(ctx, nil)
if err != nil { log.Fatal(err) }
// Проверенный предпросмотр: без записи на диск и автоматического применения.
preview, previewErr := edit.Preview(doc.Text)
_ = preview
_ = previewErr // filtered/no-op/empty не являются применимой правкой
```

`CursorOffset` — байты UTF-8, позиция на границе символа. `JumpPosition`
содержит нативные zero-based `row/col`; IDE использует их как позицию редактора.
`SelectionEndLine` — exclusive, одинаковые start/end обозначают одну строку.
`Preview` сверяет SHA-256 снимка и весь исходный текст CharacterDiff, сохраняет
окончания строк, отклоняет неизвестные изменения и устаревший исходник.
Не применяйте промежуточные кадры: они являются растущими снимками результата.

`Supercomplete` и `TabToJump` работают отдельно: на каждый вызов создаётся временный движок,
подготавливается контекст без генерации Supercomplete, затем воспроизводятся
до двух последних реальных снимков и выполняется один запрос к модели выбранной команды.
Достаточно одного `Sync`: предварительно менять код или запускать другую команду не нужно.
Оригинальные даты наблюдения сохраняются в `Preparation.Snapshots`; время
загрузки контекста — в `Preparation.ReplayedAt`. Это повторная загрузка
сохранённого контекста, а не новые действия пользователя. Технический пустой
документ подготовки отделён от пользовательского кода в отчёте.

Путь проверен на native **2.24.0**. Для других версий независимый вызов возвращает
`unsupported_editor_version` до bootstrap: порядок внутренней инициализации
и фильтра нельзя предполагать стабильным. Неожиданный data frame подготовки
тоже останавливает вызов. Raw bootstrap frames/terminal сохраняются отдельно
от кадров основной команды. Дополнительной генерации Supercomplete нет.

`TabToJumpNative` оставляет прежний автоматический путь: для него нужен
предварительный `SupercompleteNative`, и действует нативное окно недавней правки.
`SupercompleteNative` также сохраняет исходное автоматическое поведение.
Ручные `Supercomplete` и `TabToJump` не подготавливают автоматическую сессию.
Команды запрашивают `tab_flash_lite_preview` / `tab_jump_flash_lite_preview`:
native ID каждый раз читается из свежего каталога, сохраняется в
`RequestedModel` / `RequestedNativeModel` и передаётся в requestedModelId.
Это другой API, чем `Complete`. Native-конфигурация может влиять на routing;
точное имя реально выбранной модели и token usage этот ответ не
подтверждает (`Usage: UNKNOWN`). Поля diff/comboDiff и буквальные JSON-кадры
сохраняются в результате. Отмена через context прерывает HTTP stream;
незавершённый ответ имеет `Status: incomplete` и не применяется.

Ручной стенд: `go run ./testbench --live --ui --listen 127.0.0.1:8771`,
затем `/editor`. Можно выбрать собственный текстовый файл UTF-8 до 4 МиБ:
имя сохраняется, язык определяется по расширению и доступен для ручного выбора.
Файл загружается в исходную и текущую версии. Нажмите «Запомнить исходную
версию», установите курсор и запустите любую команду; править код перед этим
необязательно. Загрузка не вызывает модель и не меняет исходный файл на диске.
После «Применить правку к текущему коду» нажмите «Скачать текущий файл»:
браузер сохранит текущий текст редактора в UTF-8 с исходным именем. Можно
также скачать ручные изменения; неприменённый предпросмотр в файл не попадёт.
CLI-проба: `go run ./testbench --live --case editor`.
Проверка независимого перехода после паузы:
`go run ./testbench --live --case jump --editor-idle 65s`.
Проверка независимой правки после паузы:
`go run ./testbench --live --case supercomplete --editor-idle 65s`.
Подробности: [INDEPENDENT-JUMP-2026-10-21.md](INDEPENDENT-JUMP-2026-10-21.md),
[INDEPENDENT-SUPERCOMPLETE-2026-10-21.md](INDEPENDENT-SUPERCOMPLETE-2026-10-21.md).

## Диагностика и границы

`ErrorCode(err)` и `*ag.Error`: `engine_unavailable`, `authentication_required`,
`engine_connection`, `engine_exited`, `timeout`, `cancelled`, `client_closed`,
`invalid_input`, `invalid_schema`, `model_unavailable`, `incompatible_image_input`,
`provider_error`, `content_rejected`, `incompatible_format`, `contract_violation`,
`image_missing`, `image_model_mismatch`. Контекстная причина доступна через `errors.Is`.

`Result`: output, raw finish, SHA-256, trajectory, cascade ID, точный catalog/native
model ID, версия движка, latency и native usage. JSON objects — `map[string]any`,
JSON numbers — `float64` по соглашению `encoding/json`; строковые counters
сохраняются строками. Usage scope: `trajectory_model_steps`, не расход аккаунта.
Usage отменённых запросов и стоимость изображений — `UNKNOWN`.
Отчёты/trajectory содержат входной текст и локальные workspace paths.

Каталог запрашивается заново, без denylist/negative cache. Временная ошибка
не запрещает повторный запрос. Native logs/OAuth URL не сохраняются,
API-key overrides удаляются из окружения, plugins/MCP пользовательского профиля
не импортируются. Local HTTP transport не использует системный proxy.

Используется внутренний протокол Antigravity. Проверена Windows / native 2.24.0.
Linux/macOS проходят cross-build; live acceptance на них отсутствует.
Подробности: [VALIDATION-2026-10-21.md](VALIDATION-2026-10-21.md).
