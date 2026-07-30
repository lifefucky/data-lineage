# data-lineage

Локальный property graph для triage Greenplum-пайплайна: топология таблиц (STG → ODS → DDS → DM) и row counts хранятся **раздельно** в SQLite; анализ — NetworkX; UI — Streamlit + Pyvis.

Версия пакета: `0.1.0` (`__init__.py`). Лицензия: Apache 2.0.

Идея проекта и roadmap: [`docs/PROJECT.md`](docs/PROJECT.md).

## О проекте

Инструмент собирает граф физических таблиц из DDL-экспорта и meta-источников, подтягивает оценки числа строк из GP (read-only SSH/SQL) и показывает статусы узлов в UI:


| Цвет    | Смысл (`graph/status.py`)                                             |
| ------- | --------------------------------------------------------------------- |
| red     | `row_count == 0`                                                      |
| yellow  | у родителя > 0, у ребёнка 0 (слои gp/inc/snp/ods); приоритет выше red |
| blue    | DM без входящих рёбер                                                 |
| neutral | норма                                                                 |
| unknown | нет метрик / view                                                     |


Слои узлов (`models/enums.py`): `gp`, `inc`, `snp`, `ods`, `dds`, `dm`, `dm_view`.  
Схемы узлов: `stg_ods`, `ods`, `dds`, `dm`.

Правила рёбер (`models/edge.py`): `naming`, `metadata`, `graph_node`, `sql_parse`.

## Возможности

- **Сборка топологии** (`builder/`): сканирование DDL → рёбра STG по naming → ODS/DDS/DM из meta → SQL_PARSE из `dm/functions` (views только как индекс источников, не узлы графа)
- **Counts отдельно от схемы** (`counter/`): `fast` (reltuples + partition ladder) / `exact` (`count(*)`), кэш в `counts_cache.db`
- **Live или offline meta**: `LiveSQLMetaProvider` / `FixtureMetaProvider`
- **Анализ путей** (`graph/lineage_graph.py`): NetworkX digraph, upstream/downstream, ego hops при больших ветках
- **UI** (`visualizer/`): фильтры `src_code`, таблица, направление ветки, полосы схем; фоновый exact-recount
- **Probe-утилита** (`tools/probe_gp_counts.py`): сравнение cache vs partition/exact (read-only)

## Установка

```bash
pip install -r requirements-entities.txt
```

Зависимости: `pydantic`, `networkx`, `streamlit`, `pyvis`, `pytest`, `psycopg2-binary`, `sshtunnel`.

Рабочая директория — корень этого пакета (рядом с `cli.py`).

## CLI

Входная точка: `cli.py` (prog: `entities_lineage`).

```bash
python cli.py build-schema [options]
python cli.py update-counts [options]
python cli.py run-ui [options]
```

### `build-schema`

Полный rebuild топологии → `data/schema_cache.db` (по умолчанию).


| Флаг                  | Назначение                                                         |
| --------------------- | ------------------------------------------------------------------ |
| `--export-root`       | Корень DDL-экспорта (default: `../gp_metadata/gp_metadata_export`) |
| `--schema-db`         | Путь к SQLite схемы                                                |
| `--meta-fixture PATH` | Meta из JSON (offline)                                             |
| `--live-meta`         | Meta SELECT из GP через SSH                                        |
| `--stg-ods-only`      | Без DDS/DM и без SQL_PARSE                                         |
| `--no-sql-parse`      | Без рёбер `dds|ods → dm` из functions                              |


Оркестрация: `builder/schema_builder.py` → `NodeScanner` → edge builders → `SchemaStore.replace_all`.

### `update-counts`

Обновляет только `data/counts_cache.db`; схему не трогает.


| Флаг                 | Назначение                      |
| -------------------- | ------------------------------- |
| `--mode fast|exact`  | reltuples-ladder или `count(*)` |
| `--scope all|empty`  | все узлы или только с нулём     |
| `--mock-counts PATH` | JSON `fqn → count` без GP       |


### `run-ui`

Запускает `streamlit run visualizer/app.py` с `--schema-db` / `--counts-db`.

## Структура

```text
.
├── cli.py
├── __init__.py                 # __version__ = 0.1.0
├── requirements-entities.txt
├── pytest.ini                  # testpaths=tests, pythonpath=.
├── LICENSE                     # Apache 2.0
├── QUICKSTART.md
├── README.md
├── data/.gitkeep               # runtime DB игнорируются
│
├── models/                     # Pydantic-контракты
│   ├── enums.py                # Layer, CountMode, StatusColor
│   ├── table.py                # TableNode (schema, name, layer, src_code, fqn)
│   ├── edge.py                 # FlowEdge, EdgeRule
│   ├── metrics.py              # TableMetrics, NodeView
│   └── meta_rows.py            # DTO meta.*
│
├── builder/                    # offline/live сборка топологии
│   ├── node_scanner.py         # DDL → TableNode
│   ├── edges_stg.py            # gp → inc → snp (naming)
│   ├── edges_ods.py            # snp → ods (metadata_tables)
│   ├── edges_dds.py            # ods → dds (view_mapping_tables)
│   ├── edges_dm.py             # dm ↔ dm (graph_node)
│   ├── edges_dm_views.py       # ViewSourceIndex (не узлы)
│   ├── edges_dm_functions.py   # SQL_PARSE → dds|ods → dm
│   ├── sql_refs.py             # regex schema.ident из SQL
│   ├── meta_provider.py        # Fixture | LiveSQL
│   ├── schema_store.py         # schema_cache.db
│   ├── schema_builder.py       # full rebuild
│   └── prc_utils.py            # mart_name_from_prc
│
├── counter/
│   ├── gp_connector.py         # SSH + SELECT whitelist
│   ├── count_service.py        # fast / exact
│   ├── counts_store.py         # counts_cache.db
│   └── logging_setup.py
│
├── graph/
│   ├── lineage_graph.py        # NetworkX, subgraph, layout
│   └── status.py               # red / yellow / blue / neutral
│
├── visualizer/
│   ├── app.py                  # Streamlit
│   └── graph_view.py           # подготовка Pyvis (без браузера)
│
├── lib/                        # vendored vis-network, tom-select
├── tools/probe_gp_counts.py
└── tests/
    ├── __init__.py
    └── with_fixtures/.gitkeep  # сами тесты/фикстуры в .gitignore
```

## Пайплайн данных (по коду)

```text
DDL export (tables/)
        │
        ▼
  NodeScanner ──► TableNode[]
        │
        ├─ naming ──────────────► stg: gp → inc → snp
        ├─ meta.metadata_tables ► snp → ods
        ├─ meta.view_mapping ───► ods → dds
        ├─ meta.graph_node ─────► dm ↔ dm
        └─ dm/functions + views ► SQL_PARSE: dds|ods → dm
                │
                ▼
         SchemaStore (SQLite)
                │
    CountService (optional) ──► CountsStore (SQLite)
                │
                ▼
     LineageGraph + status colors ──► Streamlit / Pyvis
```

## Конфигурация / окружение

- Runtime-кэши пишутся в `data/` (`schema_cache.db`, `counts_cache.db`) — содержимое каталога в `.gitignore`.
- Live GP: `counter/gp_connector.py` (`default_ssh_factory`); ожидается доступ через конфиг соседнего `gp_metadata` (см. импорты в connector).
- `.env` игнорируется.

## Тесты

`pytest.ini` задаёт `testpaths = tests`. В неотмеченном дереве есть только `tests/__init__.py` и `tests/with_fixtures/.gitkeep`: сами тесты и фикстуры лежат под `tests/with_fixtures/*` и **игнорируются git**. Для локального прогона они могут присутствовать на диске, но не входят в этот срез документации.

## Technology stack


| Компонент | Технология                                                  |
| --------- | ----------------------------------------------------------- |
| Модели    | Pydantic v2                                                 |
| Граф      | NetworkX                                                    |
| Persist   | SQLite (`schema_store`, `counts_store`)                     |
| GP access | psycopg2 + sshtunnel (read-only SELECT)                     |
| UI        | Streamlit + Pyvis (+ vendored vis.js / tom-select в `lib/`) |
| Тесты     | pytest                                                      |


## Связанные файлы

- Идея и функционал: [`docs/PROJECT.md`](docs/PROJECT.md)
- Практические команды: [`QUICKSTART.md`](QUICKSTART.md)

