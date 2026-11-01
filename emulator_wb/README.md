# Эмулятор Wildberries «Цены и скидки»

Отдельный HTTP-сервис. С репрайсером общается только по HTTP, общего кода и общей базы нет.
Товары и задачи хранятся в памяти; при старте товары читаются из JSON.

## Запуск

```powershell
pip install -r emulator_wb/requirements.txt
uvicorn emulator_wb.app.main:app --port 8090
```

Переменные окружения:

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `WB_SEED_PATH` | `emulator_wb/data/goods.json` | файл начальных товаров |
| `WB_APPLY_DELAY_SECONDS` | `3` | через сколько секунд применяются цены |
| `WB_QUARANTINE_PCT` | `30` | порог карантина, % падения цены |
| `WB_RATE_LIMIT` | `0` | запросов за окно на токен; `0` — лимит выключен |
| `WB_RATE_LIMIT_WINDOW_SECONDS` | `1` | размер окна лимита, секунды |

## Ограничение запросов

Если `WB_RATE_LIMIT > 0`, запросы сверх лимита получают `429`:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 1
{"error": true, "errorText": "Слишком много запросов"}
```

Лимит считается отдельно для каждого токена `Authorization`; `/health` не учитывается. Репрайсер
повторяет запрос после паузы (по `Retry-After` или с экспоненциальной задержкой, до 3 попыток).

## Методы

### `GET /api/v2/list/goods/filter`

Заголовок `Authorization` обязателен, подходит любой непустой токен.
Query-параметры: `limit` (до 1000), `offset`, `filterNmID`, `filterVendorCode`.

```json
{
  "data": {
    "listGoods": [
      {
        "nmID": 100001,
        "vendorCode": "SKU-001",
        "sizes": [{"techSize": "0", "skus": [], "price": 150000, "discountedPrice": 135000}],
        "currencyIsoCode4217": "RUB",
        "discount": 10,
        "clubDiscount": 0,
        "editableSizePrice": false
      }
    ]
  },
  "error": false,
  "errorText": ""
}
```

`price` и `discountedPrice` — **в копейках**. Итоговая цена = `price × (1 − discount / 100)`.

### `POST /api/v2/upload/task`

Цена в теле — **целые рубли**, скидка — целые проценты. Пачка, а не по товару.

```json
{"data": [{"nmID": 100001, "price": 1706, "discount": 10}]}
```

Ответ: `{"data": {"id": 1, "alreadyExists": false}, "error": false, "errorText": ""}`.

Применение асинхронное: через `WB_APPLY_DELAY_SECONDS` секунд. Задача в обработке — `status = 1`,
обработана — `status = 3`. Если такая же пачка ещё в обработке, вернётся тот же `id` и
`alreadyExists: true` (защита от повторной отправки).

Ошибки и карантин по товару не мешают применить остальные товары пачки.

### `GET /api/v2/history/tasks?uploadID=1`

```json
{
  "data": {
    "uploadID": 1,
    "status": 3,
    "uploadDate": "2026-10-31T12:00:00+00:00",
    "activationDate": "2026-10-31T12:00:03+00:00",
    "overAllLimit": 0,
    "processed": "2026-10-31T12:00:03+00:00",
    "goods": [
      {"nmID": 100001, "vendorCode": "SKU-001", "price": 1706, "discount": 10, "errorText": ""}
    ]
  },
  "error": false,
  "errorText": ""
}
```

### `GET /api/v2/history/goods/task?uploadID=1&limit=1000&offset=0`

То же детализированно, по товарам. Дополнительно отдаётся `errorCode` (расширение эмулятора):
`unknown_nm`, `invalid_price`, `invalid_discount`, `quarantine` или пустая строка.

## Ошибки

- Нет заголовка `Authorization` → `401` с `{"error": true, "errorText": "..."}`.
- Некорректное тело → `400` в том же формате.
- Неизвестный `nmID`, цена ≤ 0, скидка вне 0…100 → ошибка в детализации задачи, остальные товары применяются.
- Карантин: новая цена со скидкой ниже старой **более чем** на `WB_QUARANTINE_PCT`% → товар не
  обновляется, в детализации ошибка карантина.

## Отличия от боевого WB

- Статусы задачи: `1` — в обработке, `3` — обработана (в WB используйте маппинг из своей версии API).
- `errorCode` — расширение эмулятора; в боевом API ориентируйтесь на `errorText`.
- Цена в `upload/task` — целые рубли; в `list/goods/filter` — копейки (как в документации WB).
- Одна цена на `nmID`; размеры не моделируются.