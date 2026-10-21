# Приёмка релизных архивов

Дата: 2026-10-21.

Подготовлены Python **0.2.0** (`v0.2.0`) и Go **0.5.0** (`go/v0.5.0`).
Версии SDK сохранены: изменения этого этапа касаются документации, упаковки,
уведомлений и публикации. Native executable Antigravity не распространяется.

## Проверки

- 100 существующих Python SDK/stand тестов прошли; 4 новых теста защиты исходного
  архива от локальных run artifacts прошли отдельно. Ruff lint/format успешны.
- Go `go test ./...` и `go vet ./...` успешны.
- Wheel и sdist собраны через `build --no-isolation`. METADATA содержит версию
  0.2.0 и project URLs; License-Expression/License-File и SDK LICENSE отсутствуют
  по прямому решению владельца. Уведомления зависимостей сохранены отдельно.
- Проверены SHA-256 manifest, состав обоих ZIP, исходный архив и отсутствие
  `reports`, `.venv`, `node_modules`, `.git`, bytecode и native Antigravity binary.
  Исходный архив включает документацию, скриншоты и примеры.
- Из wheel выполнена установка с зависимостями в новую venv; импорт с `-I`
  подтвердил `site-packages`, без SDK source tree.
- Общий ZIP запущен из свежей распаковки с установленным wheel: Python и Go
  вернули каталоги по 27 моделей. Проверены API identity, token guard, страницы
  и style nonce; принадлежащие процессам ресурсы закрыты. Это metadata smoke,
  новых запросов генерации на данном этапе не выполнялось.
- Распакованный Go executable прошёл mock `all`: catalog/authentication,
  text/schema/vision, cancel/concurrent catalog, recovery и image artifact.
- Проверены относительные Markdown-ссылки; README преобразован GitHub Markdown
  API и визуально просмотрен. Все 3 изображения загружаются; входные данные
  синтетические, токены и приватный код проектов на скриншотах отсутствуют.

## Защита состава пакета

При предварительной сборке `recursive-include go` захватил игнорируемые локальные
отчёты. До публикации добавлены явные prune-правила и проверка всех членов sdist,
а также 4 регрессионных теста. Предварительные архивы не используются как релизные.
Окончательные сборки проходят эту проверку и получают отдельные SHA256SUMS.

Сборщик перечисляет фактически компилируемые Go dependencies, сохраняет их LICENSE,
доступные PATENTS/NOTICE, уведомления Go runtime и CodeMirror/Lezer. Локальные
пути module cache не попадают в архив; Windows executable собран с `-trimpath`.

## Поставка

Python release содержит wheel, sdist, общий Windows/amd64 ZIP и SHA256SUMS.
Go release содержит самостоятельный Windows/amd64 ZIP и SHA256SUMS.
Проверенная native-среда — Windows / Antigravity 2.24.0; независимая подготовка
редактора сохраняет version guard. Отчёты native приёмки функций SDK находятся
в [индексе](README.md#validation-records).

Исходники опубликованы через GitHub, а не через PyPI. Видимость репозитория
и доступ к аккаунтам не расширяются публикацией релиза.
