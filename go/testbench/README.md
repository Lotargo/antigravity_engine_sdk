# Самостоятельный тестовый стенд

Консольное приложение для проверки Go SDK без интеграции в другой продукт.
Исходники стенда находятся отдельно от SDK в этой папке.

## Локальный интерфейс

```powershell
.\go\testbench\ui.ps1
# Либо без Go после сборки:
.\go\testbench\bin\antigravity-testbench.exe --ui --live
```

Откройте `http://127.0.0.1:8765`: свежий каталог моделей, промпт, одно изображение
с предпросмотром и отмена активного запроса. Generations запускаются только кнопкой.
Можно обновлять каталог во время генерации. После отмены доступен следующий запрос.
Для `tab_*` выберите формат **«Обычный текст»**. Этот режим вызывает
`Client.Complete` без инструментов/finish. Формат «Структурированный (finish)»
сохраняет прежний контракт Chat. Автоматических переключений или fallback нет.
Ответ, native usage и JSON-отчёт доступны в правой панели; отчёты сохраняются
в новом `go/reports/` каталоге. `--listen 127.0.0.1:8766` выбирает другой порт.
Для mock UI: `run.ps1 --ui`. При перезагрузке страницы состояние последнего
запроса восстанавливается из памяти сервера. Перезапуск сервера создаёт новую сессию.
Для остановки foreground сервера нажмите Ctrl+C в терминале.

Из корня репозитория:

```powershell
.\go\testbench\run.ps1
.\go\testbench\run.ps1 --live --case catalog
.\go\testbench\run.ps1 --live --case all --model gemini-3.8-flash-low
.\go\testbench\run.ps1 --live --case image --expected-image-model gemini-3.1-flash-image
```

Без `--live` запускается MOCK в отдельном дочернем процессе. Нативный движок,
аккаунт и модель не используются. Негативные сценарии и ownership проверяются
через `go test ./...` из папки `go/`.

В live `all` проверяет каталог, sign-in, text, JSON Schema с `$ref`, vision на
четырёхцветной PNG, concurrent catalog, отмену и следующий успешный запрос.
`image` запускается отдельно. PASS vision означает валидный finish + media
receipt; правильность описания проверяйте по `vision-input.png` в `report.json`.

Каждый запуск создаёт новый `go/reports/go-<mode>-<timestamp>/`: `report.json`,
vision PNG и image artifacts. Стенд пишет PASS/FAIL и возвращает ненулевой код
при ошибке. В отчёте есть raw finish, trajectory, usage и длительности;
при ошибке — последний доступный trajectory snapshot. Cancelled usage — UNKNOWN.

Флаги: `go run ./testbench --help` из `go/`.
`--case catalog|text|plain|schema|vision|cancel|image|all`, `--model`,
`--expected-image-model`, `--timeout 180s`, `--executable`, `--output <new-dir>`.

Сборка самостоятельного Windows exe:

```powershell
.\go\testbench\build.ps1
.\go\testbench\bin\antigravity-testbench.exe
.\go\testbench\bin\antigravity-testbench.exe --live --case catalog
```

EXE не требует Go/Python. Для live нужен Antigravity 2.x с выполненным входом.
Прямой запуск exe сохраняет `reports/` относительно текущей папки;
`run.ps1` запускает из папки `go/`.

Проверка tab-моделей через CLI из папки `go/`:

```powershell
go run ./testbench --live --case plain --model tab_flash_lite_preview
go run ./testbench --live --case plain --model tab_jump_flash_lite_preview
```
