# Native Supercomplete / Tab-to-Jump

Дата: 2026-10-21. Go SDK 0.3.0, установленный native 2.24.0.
Рабочая ветка: `codex/go-sdk`.

Продолжение: [независимый Tab-to-Jump в Go 0.4.0](INDEPENDENT-JUMP-2026-10-21.md).
Ниже сохранена проверка первоначального автоматического пути Go 0.3.0.

## Найденный протокол

1. Отдельный owned runtime и синтетический workspace.
2. `AddTrackedWorkspace`, исходный `Document` через
   `RefreshContextForIdeAction(STARTUP, blocking=true)`.
3. Фактическое изменение текста, новый snapshot через
   `RefreshContextForIdeAction(SELECTION_CHANGED, blocking=true)`.
4. `HandleStreamingCommand` с source `SUPERCOMPLETE`, diff type
   `TMP_SUPERCOMPLETE`, trigger **TYPING**. Этот путь создаёт granular history.
   Первоначальный FORCED не выполнял этот шаг; LSP сам по себе не помогал.
5. `HandleStreamingCommand` с source `TAB_JUMP`, diff type `TMP_TAB_JUMP`.

Входной cursorOffset — UTF-8 byte offset. Connect stream содержит framed JSON
data messages и terminal frame. Ответы — растущие полные снимки результата,
их нельзя складывать как последовательные патчи. Сохраняются все кадры.
Структурированный ответ содержит `characterDiff`, `diff`, `comboDiff`,
`jumpPosition`, `filterReason`, completion/prompt/trace IDs.

Схемы восстановлены из embedded protobuf descriptors установленного binary;
семантика диапазона подтверждена кодом установленного IDE extension:
exclusive end line, одинаковые start/end обозначают одну строку. В `Preview`
сверяются SHA-256 всего снимка и буквальное содержимое удаляемого диапазона.
Полученные строки не исправляются, файлы проекта не перезаписываются.

## Живые результаты

Сценарий: в объявлении `tax_rate` заменён на `vat_rate`; использование осталось
на 29-й строке, на расстоянии 25 строк от курсора.

| Проба | Supercomplete | Tab-to-Jump |
|---|---|---|
| Python `editor-rename-2026-10-21-01` | native characterDiff | row=28, col=21; `tax_rate` → `vat_rate` |
| Go `editor-go-2026-10-21-01` | edit, 1100 ms, 3 data frames | jump, 1474 ms, 3 data frames; row=28, col=21 |
| Финальный Go с requestedModelId, `editor-go-2026-10-21-03` | filtered/no-op, 671 ms, 3 data frames | jump, 809 ms, 3 data frames; row=28, col=4 |
| Повторный Sync в Python | edit, 1063 ms | jump, 1093/922 ms; та же позиция |

Длительности — отдельные наблюдения, не benchmark или гарантия. Позиция с нуля:
в интерфейсе это строка 29, столбец 22. Go Preview подтвердил буквальный диапазон
и сохранил полный файл с заменой удалённого использования. Первая подсказка
Supercomplete может предлагать обратное переименование; это исходный ответ
модели. Синтаксическая применимость diff не подтверждает смысловую правильность.

Короткий пример честно возвращал `edit is no-op` и
`No changed lines found beyond minimum distance threshold`.
После 65 секунд без изменения кода native вернул terminal-only поток;
его log: **`no edits found in recent duration`**. Повторная передача того же
снимка не является новой правкой. Это учитывается в инструкции и статусе UI;
искусственная история изменений не создаётся.

`GetUserTrajectoryDebug` после первого FORCED может падать внутри RPC с nil
trajectory (`TruncateTrajectory`). Сам процесс остаётся работоспособным.
Production Go API этот диагностический метод не вызывает.

Go запрашивает соответствующие tab preview модели через requestedModelId,
разрешая enum по свежему каталогу перед каждым запросом. Результат сохраняет
запрошенные ID; routing остаётся под управлением native-конфигурации. Присутствие ID в каталоге
или requestedModelId не доказывает фактически выбранный модельный backend.
Точный backend и token usage данного ответа — **UNKNOWN**.

## Проверки и артефакты

Локальные регрессии: Connect framing, terminal error, truncated/compressed
frames, пустой поток, фильтрация, cancellation после data frame, отдельные
снимки истории, запрет Tab-to-Jump без bootstrap, zero position, UTF-8 cursor,
Unicode/CRLF Preview, запрет устаревшей и незавершённой правки.

Raw live-артефакты (не включены в Git):

- `../reports/editor-typing-2026-10-21-01/report.json`
- `../reports/editor-rename-2026-10-21-01/report.json`
- `../reports/editor-sync-repeat-2026-10-21-01/report.json`
- `../reports/editor-idle-2026-10-21-01/report.json`
- `reports/editor-go-2026-10-21-01/editor-report.json`
- `reports/editor-go-2026-10-21-03/editor-report.json`

Воспроизведение Python: `examples/probe_editor_native.py --live --scenario
rename --output <new-dir>`; `--include-debug` включает диагностические
траектории, `--pause-before-jump 65` проверяет idle condition.
Go: `go run ./testbench --live --case editor` из `go/`.

Ручная страница: `/editor` в live testbench. Новая сборка запущена отдельно на
8767; предыдущие 8765 и 8766 не изменялись. В SDK доступны `NewEditorSession`,
`Sync`, `Supercomplete`, `TabToJump`, `Preview`, `Close`. Каждая editor-сессия
владеет отдельным процессом; история чата/Complete с ней не смешивается.

Go-проверки `go test -race ./...` и `go vet ./...` прошли. В браузере проверены
реальные Supercomplete (edit, 796 ms) и Tab-to-Jump (jump, 1013 ms, row=28 col=4),
перемещение курсора, применение diff к текущему тексту, отмена (`cancelled`,
0 data frames, неприменимый результат) и дальнейшие успешные команды. Отмена
до получения кадров подтверждает клиентский lifecycle, но не token usage или
прекращение начисления на стороне провайдера.

Browser raw: `reports/go-live-20261021-190337.111253800/editor-*.json`;
скриншот: `reports/editor-ui-2026-10-21.jpg`. При нажатии Apply обновился только
textarea стенда; рабочие файлы проекта не изменялись. Широкая автоматическая
проверка качества кода модели не заявляется.
