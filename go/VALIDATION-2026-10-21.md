# Проверка Go SDK 0.1.0

Дата: 2026-10-21.

Реализация: отдельный Go-модуль `go/`, нативный процесс и Connect JSON RPC.
Тестовый стенд: `go/testbench/`, mock по умолчанию, live по явному флагу.
Go 1.26.3 / Windows amd64 / Antigravity 2.24.0.
Python SDK остаётся версии 0.1.1; его runtime-код не менялся.

## Локальная проверка

- `go test -race ./... -count=1`: 44 PASS с учётом subtests, 1 SKIP.
  Пропущен только symlink escape test: локальная учётная запись не может
  создавать symbolic links. Проверка containment реализована; исполнение
  именно этого symlink-сценария на Windows остаётся неподтверждённым.
- `go vet ./...`: PASS.
- `go build ./...` для Linux amd64 и macOS arm64: PASS (cross-build,
  без запуска нативного движка на этих платформах).
- `testbench/build.ps1`: Windows exe собран, 11 954 688 bytes.
- Собранный exe: 8/8 mock checks PASS, включая отдельный protocol process.
- Python regression: 47 PASS.

Race detector использовал имеющийся `C:/msys64/ucrt64/bin/gcc.exe` и
`GORACE=atexit_sleep_ms=0`. Первый запуск через системный GCC не собрался
из-за отсутствующего `stddef.h`; для обычной сборки SDK C-компилятор не нужен.

Локальные regression checks проверяют свежий каталог и восстановление после
provider error, native IDs/usage, payload/tool restrictions, media receipt,
JSON/schema failures и `null`, отсутствие finish, deadline/queued cancellation,
concurrent metadata, callback panic, close при активном запросе/старте,
startup timeout, authentication, process ownership/restart и image artifacts.

Обнаруженный дефект JSON `null` исправлен: nil map теперь явно отвергается
как `contract_violation`, даже при схеме без обязательных свойств.
Короткий deadline в тесте отмены увеличен до 2 s, чтобы под race detector
проверялся активный invocation, а не timeout во время компиляции схемы.

## Реальный движок

Planner: точный catalog ID `gemini-3.8-flash-low`.
Каждый high-level запрос — отдельный cascade/workspace на одном reusable client.

| Проверка | Результат | Время стенда, ms |
| --- | --- | ---: |
| Холодный запуск + каталог | PASS, 27 catalog records | 7033 |
| Sign-in metadata | PASS | 1 |
| Text через finish | PASS | 32819 |
| Object schema + local `$ref`, thinking=low | PASS: answer=42, nested.ok=true | 2694 |
| Vision PNG | PASS: Red, green, blue, yellow | 5137 |
| Concurrent catalog + cancel | PASS | 238 |
| Запрос после отмены | PASS: ready | 3118 |
| Image tool, отдельный запуск | PASS | 19866 |

Image: actual `gemini-3.1-flash-image`, JPEG 518 240 bytes, 1024x1024.
Полученные байты успешно прочитаны после cleanup; изображение визуально
проверено: синяя чашка с жёлтыми треугольниками на белой поверхности.
Модель нарисовала несколько треугольников вместо одного: точность исполнения
композиции этим SDK acceptance не подтверждается.

Длительности — отдельные наблюдения конкретного запуска, не benchmark ускорения.
Usage: native `trajectory_model_steps`, строковые counters сохранены.
Отменённый запрос и image price: UNKNOWN. Наличие model ID в каталоге
не гарантирует inference availability. Catalog additions/removals и provider
recovery проверены в mock, без изменения внешнего каталога аккаунта.

## Артефакты

Raw отчёты и бинарник локальные, исключены из Git:

- `reports/go-live-20261021-173834.969557800/report.json` — text/schema/vision/cancel/recovery.
- `reports/go-live-20261021-174049.427041900/` — image report и `image-1.jpg`.
- `reports/go-mock-20261021-174616.560094300/report.json` — собранный exe.
- `reports/go-race.jsonl` — полный race-test log.
- `testbench/bin/antigravity-testbench.exe` — самостоятельный Windows стенд.

SHA-256 exe:
`d7f32399d0d4794c1485daa89193a80b49823ae3a41b4b6bb5511d3a78fd3ece`.

Границы: внутренний протокол Antigravity; Linux/macOS live, editing fidelity,
весь модельный каталог и все reasoning overrides отдельно не принимались.
Go 0.1.0 предоставляет raw RPC через Client; отдельного публичного Engine нет.
JSON numbers декодируются стандартным encoding/json как float64.
