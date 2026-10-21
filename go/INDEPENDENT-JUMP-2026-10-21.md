# Независимый Tab-to-Jump

Дата: 2026-10-21. Go SDK 0.4.0, native 2.24.0.

## Поведение

`EditorSession.TabToJump` больше не требует предварительного Supercomplete
или немедленного нажатия после изменения текста. Кнопка в стенде доступна
сразу после инициализации. `Sync` хранит два последних различных реальных
снимка и времена их наблюдения. Повторный Sync того же текста не стирает
предыдущий снимок и не обновляет время наблюдения изменения.

При явном запросе Tab-to-Jump SDK запускает отдельный owned native процесс,
загружает сохранённые снимки и получает настоящий native `jumpPosition`/diff.
Промежуточные и окончательный кадры имеют прежний нативный формат.
Автоматического применения кода нет; Preview проверяет точный исходник.

## Подготовка контекста без генерации Supercomplete

В native 2.24.0 `HandleTab` обновляет granular trajectory в ветке
SUPERCOMPLETE/TYPING до проверки возраста правки. В `buildTabRequestData`
`ConstructSupercompleteTrajectoryPromptParams` задаёт фиксированные 30 секунд;
после сравнения с `FileStateTracker.GetLastUpdateTime` запрос завершается
`no edits found in recent duration` до `getCompletionsForTab` и model inference.
`UpdateFileStateFromDocument` использует предоставленный Document.timestamp.

SDK создаёт в своём временном workspace отдельный **пустой служебный файл**,
фактически устанавливает его mtime в Unix epoch и передаёт именно этот mtime
в Document.timestamp. Пользовательский текст в нём отсутствует. Запрос с
source SUPERCOMPLETE/TYPING создаёт granular context и завершается только
пустым terminal frame, без генерации подсказки. Затем SDK загружает сохранённые
пользовательские снимки и выполняет один model request TAB_JUMP.

Это технический bootstrap через Supercomplete RPC, **не скрытая генерация
Supercomplete**. Временная метка служебного файла не применяется к данным
пользователя. Исторические ObservedAt и ReplayAt разделены в отчёте; SDK не
утверждает, что пользователь только что внёс воспроизводимую правку.

Для другого native version путь отклоняется `unsupported_editor_version`
до bootstrap. Если подготовка неожиданно вернёт data frame или terminal error,
операция останавливается; Tab-to-Jump не отправляется. Это ограничение намеренно
сохраняет границу проверенного поведения. Token usage RPC не сообщает (UNKNOWN),
запрошенные model ID не являются подтверждением фактического backend.

## Проверка

Живая Go-проба: синхронизированы baseline/current, реальное ожидание **65 000 ms**
без редактирования и без генерации Supercomplete, затем первый Tab-to-Jump.

- Результат: `jump`, `row=28`, `col=21` (строка 29, столбец 22 в UI).
- Три нативных data frames и применимый CharacterDiff `tax_rate` → `vat_rate`.
- Подготовка: 5 ms, **0 data frames**, terminal `{}`.
- Весь вызов с запуском/закрытием временного процесса: 5821 ms.
- ObservedAt сохранён до ожидания; ReplayAt относится к новой загрузке контекста.
- Raw: `reports/jump-independent-go-2026-10-21-01/editor-report.json` (вне Git).

Это отдельное наблюдение, не гарантия длительности или качества подсказки.
Filtered/no-op остаётся нормальным возможным результатом модели.

В браузере 8768 проверены доступность кнопки сразу после setup, самостоятельный
вызов, нормальный filtered-ответ, отмена (incomplete, 0 data frames) и
восстановление после неё. Повторный запрос с возрастом последнего сохранённого
изменения **118.743 s** вернул `jump row=28 col=21` за 5417 ms; bootstrap 3 ms,
0 data frames. Нажатие перехода переместило реальный курсор на строку 29,
столбец 22. Supercomplete в интерфейсе не запускался.

Browser raw: `reports/go-live-20261021-193138.286551200/editor-*.json`;
скриншот: `reports/independent-jump-ui-2026-10-21.jpg` (вне Git).
`go test -race ./...` и `go vet ./...` прошли, включая Close во время работы
временного дочернего движка и запрет применения незавершённого результата.

Офлайн-регрессии проверяют replay реальных снимков, отсутствие предварительного
Supercomplete, старые ObservedAt, повторный Sync, первый снимок, изоляцию
служебного документа, неизвестный native version, неожиданный bootstrap data
frame, пустой/отфильтрованный ответ, отмену и восстановление.

Ранее проверенный автоматический путь сохранён как `TabToJumpNative`.
`Supercomplete` не меняет своё поведение. Native/IDE binaries, профиль и
credentials не изменялись. Предыдущие UI 8765/8766/8767 сохранены.

## Воспроизведение

Из папки `go/`:

```powershell
go test -race ./...
go vet ./...
go run ./testbench --live --case jump --editor-idle 65s
go run ./testbench --live --ui --listen 127.0.0.1:8768
```

В браузере открыть `/editor`, загрузить пример, запомнить исходную версию,
нажать Tab-to-Jump. Supercomplete для этого не нужен.
