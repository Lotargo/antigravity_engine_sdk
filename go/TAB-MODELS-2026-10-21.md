# Использование tab preview моделей

Дата: 2026-10-21. Antigravity 2.24.0, Go SDK 0.2.0.

Продолжение исследования в Go 0.3.0: [EDITOR-VALIDATION-2026-10-21.md](EDITOR-VALIDATION-2026-10-21.md).
Ниже сохранён результат первоначального этапа; новый этап получил native diff
и jumpPosition после bootstrap через событие TYPING.

## Рабочий путь

Обе модели доступны для обычного текстового ответа через native cascade:

1. Взять точный ID и native enum из свежего каталога.
2. `StartCascade`: `customAgentSpec.customAgent.toolNames=[]`.
3. `SendUserCascadeMessage`: без toolConfig.finish, `requireFinishTool=false`,
   `maxGeneratorInvocations=1`. В prompt нет инструкции вызвать finish.
4. Дождаться `CASCADE_RUN_STATUS_IDLE`.
5. Читать завершённые `CORTEX_STEP_TYPE_PLANNER_RESPONSE` через
   `plannerResponse.response`, сохраняя literal text и native usage.

В Go это `Client.Complete(ctx, Request{Prompt: ..., Model: ...})`.
`Result.OutputMode=planner_text`, `Output["text"]`/`Text()` содержат ответ,
`RawOutput` и SHA-256 относятся к этому тексту. Schema запрещена.
Прогресс, дедлайн, отмена, fresh catalog, media preflight и ошибки используют
прежний механизм SDK. `Chat`/`Generate` сохраняют строгий native finish contract;
fallback после ошибки не выполняется.

В интерфейсе нужно явно выбрать «Обычный текст». Для CLI:

```powershell
cd F:\projects\antigravity_engine_sdk\go
go run ./testbench --live --case plain --model tab_flash_lite_preview
go run ./testbench --live --case plain --model tab_jump_flash_lite_preview
```

## Проверка

| Модель | Native enum | Ответ на 17 × 23 | Go Result latency, ms | Native input/output tokens |
| --- | --- | --- | ---: | --- |
| tab_flash_lite_preview | MODEL_PLACEHOLDER_M19 | 391 | 1062 | 149 / 3 |
| tab_jump_flash_lite_preview | MODEL_PLACEHOLDER_M28 | 391 | 860 | 149 / 3 |

Это отдельные наблюдения, не устойчивый benchmark. На Python raw RPC обе
модели также дописали функцию сложения, но tab_jump добавила лишние escapes
и кавычки. Такой ответ сохраняется буквально; SDK не гарантирует корректность
кода и не выполняет его. В браузере tab_flash через новый Go Complete вернула:

```python
def square(n):
    return n*n
```

Syntax parsing через `ast.parse` прошёл; сгенерированный код не исполнялся.
Обе модели в каталоге не поддерживают изображения. Каталог указывает
maxTokens=16384 и maxOutputTokens=4096, но доступный budget зависит от native
контекста. Для них уместны компактные текстовые/code задачи.

Регрессии `go test -race ./... -count=1` и `go vet ./...` прошли.
Новые tests проверяют tool-less payload, отсутствие finish hint, неизменность
строгого Chat/Generate, provider/block/retired ошибки, pending steps, literal
escapes, отмену с CancelCascadeInvocation и recovery, явный UI response mode.
Ранее известный symlink test остаётся SKIP из-за прав Windows.

## Отдельный редакторский протокол

[Официальное описание Tab](https://antigravity.google/docs/ide/tab) различает
Supercomplete для правок и Tab-to-Jump для перехода к следующему месту правки.
В установленном IDE extension найдены реальные вызовы:

- `HandleStreamingCommand`, `requestSource=COMMAND_REQUEST_SOURCE_SUPERCOMPLETE`,
  `diffType=DIFF_TYPE_TMP_SUPERCOMPLETE`.
- Тот же RPC с `COMMAND_REQUEST_SOURCE_TAB_JUMP` и `DIFF_TYPE_TMP_TAB_JUMP`.
- Вход: document.text, absoluteUri, workspaceUri, editorLanguage,
  cursorOffset (UTF-8 byte offset), editorOptions; позиционные значения protobuf
  uint64 сериализуются строками.
- Ответ: diff/characterDiff/comboDiff, jumpPosition, rawText, filterReason.
- Транспорт — server streaming с framed `application/connect+json`, согласно
  [Connect Protocol Reference](https://connectrpc.com/docs/protocol/).

В standalone runtime проверены оба source: HTTP 200, только завершающий пустой
Connect frame, без data frames. В native log: `granular trajectory is not yet
available`. Дополнительно включён LSP в отдельном owned runtime, выполнены
initialize/initialized/didOpen на синтетическом workspace: поток остался пустым.
`GetCompletions` вернул 404; DEFAULT source — unexpected request source.

Следовательно, editor diff/jump путь требует состояния, которое эти пробы
не инициализировали. Пустой поток не принят за успешную подсказку. Полноценные
Supercomplete/Tab-to-Jump этим изменением не реализованы и не подтверждены;
обычный текст через Complete не подменяет их результат.

## Артефакты и воспроизводимость

Исследовательские процессы закрыты; рабочий UI на 8765 не изменялся и не
перезапускался. Новая сборка UI 0.2.0 запущена отдельно на 8766:
`testbench/bin/antigravity-testbench-0.2.0.exe --ui --live --listen 127.0.0.1:8766`.

Локальные raw артефакты (исключены из Git):

- `../reports/tab-native-2026-10-21-01/report.json` — первые streaming probes.
- `../reports/tab-native-2026-10-21-02/report.json` — metadata / DEFAULT / GetCompletions.
- `../reports/tab-native-lsp-2026-10-21-01/report.json` — LSP initialization probe.
- `../reports/tab-planner-2026-10-21-01/report.json` — raw text/code probes.
- `reports/go-live-20261021-182519.186741700/report.json` — Go tab_flash.
- `reports/go-live-20261021-182525.743843100/report.json` — Go tab_jump.
- `reports/go-live-20261021-182630.414318400/` — browser request/result.
- `reports/tab-model-ui-2026-10-21.jpg` — проверенный интерфейс с результатом.

`../examples/inspect_native_protocol.py` читает выбранные embedded protobuf
descriptors из установленного native binary. Не запускает движок и не делает
model calls; зависимости protobuf нет. `--types` выбирает сообщения/enums.
Файлы установки, профиль и credentials не изменялись.
