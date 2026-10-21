# Проверка выделения SDK

Дата: 2026-10-21.

Версия пакета: **0.1.0**. Среда: Windows, Python 3.12, установленный
Antigravity **2.24.0**. Исходный адаптер VEIL ранее проверялся на 2.23.1;
в этом документе приведены новые результаты выделенного SDK.

## Локальная проверка

- 43 теста: настоящий дочерний процесс и HTTP-сервер вместо native executable.
  Сам SDK, супервизор, HTTP client, media serialization и JSON Schema validation
  работают без подмены.
- Проверены: точный catalog/native model ID, длинный ввод, media и receipt,
  thinking override, вложенная схема с локальным `$ref`, нарушения схемы,
  planner prose без finish, provider block, отсутствие входа, авария процесса,
  startup timeout, активная отмена, очередь запросов, ошибка consumer callback,
  неудавшийся cancel RPC, закрытие во время запроса и во время запуска процесса,
  независимость клиентов, перезапуск движка, inline/file image artifacts,
  model mismatch и отказ читать произвольный внешний URI.
- После каждого boundary-теста принадлежащие SDK процессы и временные
  каталоги проверяются на отсутствие.
- Ruff lint и format check проходят. `pip check` не обнаруживает повреждённых
  зависимостей в отдельной `.venv` SDK.

## Настоящий движок

Запуск:

```powershell
python examples/smoke.py --live --model gemini-3.8-flash-low --image --output reports/native-2.24.0-live-01
```

| Проверка | Результат |
| --- | --- |
| Вход и каталог | Существующий native sign-in; 27 catalog IDs, без hardcoded policy VEIL |
| Object JSON Schema | `17 * 23 = 391`, валидный результат; 4 359 ms |
| Vision | Все 4 цветных квадранта синтетического PNG названы правильно; 3 766 ms |
| Отмена | `cancelled` после отправки запроса; usage отменённого запроса UNKNOWN |
| Повторное использование | Следующий запрос вернул `sdk-ready` в том же процессе; 2 703 ms |
| Image tool | Реальный JPEG 1024 × 1024; native model `gemini-3.1-flash-image`; 16 141 ms |
| Закрытие | Принадлежащий клиенту native process завершён |

Текстовый planner: `gemini-3.8-flash-low`, нативный ID `MODEL_PLACEHOLDER_M320`.
Изображение визуально проверено: синяя керамическая чашка с жёлтым треугольником
на белом столе. Получено 404 729 байт; SHA-256:
`0e569588207afa60475618bc6ee7e1ced9332f7452ce490e62d3dfabb946f045`.

Локальные артефакты находятся в игнорируемой папке
`reports/native-2.24.0-live-01/`: `result.json` и `generated-0.jpg`.
Входные данные синтетические; файлы проектов для генерации не использовались.
Native auth также вернул eligibility warning; он сохранён отдельно от факта
успешного входа и успешных inference-запросов.

Приведённые времена относятся к отдельным уже прогретым вызовам, а не к полному
холодному запуску или бенчмарку. Счётчики usage взяты из нативных model steps:
structured 2 700 input / 274 output; vision 3 958 / 229; запрос после отмены
2 678 / 33. Image route сообщил два шага текстового planner — это не полный
учёт image generation и не стоимость. Background/helper и account totals UNKNOWN.

## Установка и упаковка

Проверены editable install с зависимостями в отдельной `.venv`, сборка sdist и
универсального wheel, установка wheel и импорт с Python `-I` из `site-packages`.
Из установленного wheel успешно прочитаны 27 моделей настоящего Antigravity 2.24.0;
процесс завершён после закрытия клиента. Это не импорт исходников через sys.path.

Артефакты сборки:

- `dist/antigravity_engine_sdk-0.1.0-py3-none-any.whl`
- `dist/antigravity_engine_sdk-0.1.0.tar.gz`

## Границы результата

SDK не подключён обратно к VEIL; исходный checkout не изменён. Полная матрица
моделей, все reasoning overrides, non-Windows платформы, image editing fidelity,
multi-view и произвольные внутренние RPC в этой задаче не проверялись. Raw RPC
доступен для расширения, но наличие метода не означает автоматическую acceptance
всех возможностей движка. Совместимость будущих сборок Antigravity требует
повторной native проверки.
