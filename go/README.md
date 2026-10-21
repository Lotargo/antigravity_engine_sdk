# Antigravity Engine SDK for Go

Нативная Go-версия SDK `0.1.0`. Модуль:
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
`Engine` в Go 0.1.0 отсутствует: raw RPC доступен через `Client`.

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
