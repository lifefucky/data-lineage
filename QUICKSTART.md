# Быстрый старт

Рабочая директория: `entities_lineage/`.

## 1. Установка

```bash
cd entities_lineage
pip install -r requirements-entities.txt
```

## 2. Тесты (без Greenplum)

```bash
python -m pytest -q
```

Ожидание: все тесты зелёные.

## 3. Offline: схема + counts + UI

Не нужен VPN/SSH к GP.

```bash
# топология из мини-DDL фикстуры или полного export
python cli.py build-schema ^
  --export-root tests/with_fixtures/fixtures/ddl_mini ^
  --schema-db data/schema_cache.db ^
  --meta-fixture tests/with_fixtures/fixtures/meta_hm_houses.json

# для полного графа из репозитория:
#   --export-root ../gp_metadata/gp_metadata_export

# синтетические counts (кейс обрыва snp)
python cli.py update-counts ^
  --schema-db data/schema_cache.db ^
  --counts-db data/counts_cache.db ^
  --mock-counts tests/with_fixtures/fixtures/counts_break.json

# UI в браузере
python cli.py run-ui
```

В UI: фильтр `src_code` (например `001`), selectbox **Table** → полная ветка (`both` / `upstream` / `downstream`). Ego hops — только fallback, если ветка > 100 узлов. Multiselect **Слой данных** (`stg_ods` / `ods` / `dds` / `dm`) режет subgraph по schema после `src_code`/Table.

> В PowerShell вместо `^` используйте `` ` `` или одну строку.

## 4. Live Greenplum (только чтение)

Нужны доступ по SSH и конфиг в `gp_metadata/gp_metadata.py`.

```bash
# рёбра ODS/DDS/DM из meta.*  (1 SSH-сессия на весь прогон)
python cli.py build-schema --live-meta --export-root ../gp_metadata/gp_metadata_export

# оценка строк: reltuples → SUM(pg_inherits) → count(*) на оставшихся нулях
python cli.py update-counts --mode fast --scope all

# пересчёт пустых: ladder (partition-sum + count(*) на остатке)
python cli.py update-counts --mode fast --scope empty

# полный exact только по пустым (дороже; без partition shortcut)
python cli.py update-counts --mode exact --scope empty
```

В логе: `ssh session open`, `fast batch i/n … part_fallback=… exact_fallback=…`, итоговый `done updated=…`. Не должно быть цикла из сотен `Authentication` / `Password is required for id_rsa`.

Подробности: [GREENPLUM.md](docs/GREENPLUM.md).

## 5. Полезные флаги

| Команда | Флаг | Смысл |
|---------|------|--------|
| `build-schema` | `--stg-ods-only` | Без рёбер DDS/DM и без SQL_PARSE |
| `build-schema` | `--no-sql-parse` | Без рёбер `dds|ods → dm` из `dm/functions` |
| `build-schema` | `--meta-fixture PATH` | Meta из JSON, без GP |
| `build-schema` | `--live-meta` | Meta SELECT из GP |
| `update-counts` | `--mock-counts PATH` | Counts из JSON, без GP |
| `update-counts` | `--mode fast\|exact` | reltuples или count(*) |
| `update-counts` | `--scope all\|empty` | Все узлы или только пустые |

Кэши по умолчанию: `data/schema_cache.db`, `data/counts_cache.db`.

**DM lineage (SQL_PARSE, default on):** offline разбор `dm/functions` → (если source = `*_pafo_v`/`*_v`) expand через `dm/views` → рёбра **`dds|ods → dm`**. View **не** узел графа. Orphan dm (синий) в UI — после counts (Stage 6), не на этапе build. После обновления парсера нужен явный `build-schema` (старый `schema_cache.db` без rebuild не получит новые рёбра).

## 6. Типичный порядок

1. `build-schema` (один раз или после обновления DDL/meta/functions)  
2. `update-counts` (часто, без пересборки схемы)  
3. `run-ui` или смотреть пути upstream в коде/тестах  

Пересчёт counts **не** пересобирает граф.
