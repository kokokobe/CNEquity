# CNEquity · 中国市场金融数据湖

把多源的 A 股行情、基本面、事件与宏观数据，落到一份可日更、可回查的本地 Parquet 数据湖。

![CI](https://github.com/rootSunc/CNEquity/actions/workflows/ci.yml/badge.svg)![PyPI version](https://img.shields.io/pypi/v/cnequity?logo=pypi&logoColor=white&color=orange)![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)![Docs site](https://img.shields.io/badge/docs-site-2f80ed.svg)![English](https://img.shields.io/badge/docs-English-lightgrey.svg)

**42 个数据集 · Python / DuckDB / Polars / MCP**

![cne serve 控制台：42 个注册数据集的分层、语义、粒度、水位、行数与体积](docs/assets/cne-serve-datasets.png)

`cne serve` 只读控制台 · 真实截图：每个数据集的分层、采集语义、分区粒度、水位、行数与体积

[快速开始](https://rootsunc.github.io/CNEquity/getting-started/quickstart/) · [数据集目录](https://rootsunc.github.io/CNEquity/datasets/catalog/) · [研究 Recipes](https://rootsunc.github.io/CNEquity/recipes/) · [接入 AI Agent](https://rootsunc.github.io/CNEquity/reference/mcp/)

CNEquity 开源、免注册、自托管。它不负责给出交易信号，而是把分散在不同来源、不同口径、不同更新节奏的数据，长期保存在自己的机器或服务器上，并且说明每一行从哪里来、何时采到、截至哪一天可用。

## 为什么要一个数据湖

发出一次 API 请求通常不是最费事的部分。真正困难的是长期保持研究口径一致：

- **多源一致性**：字段、代码体系、更新节奏和可回补范围并不相同；
- **历史可复现**：每次研究都现场拉取，结果会随上游变化而漂移；
- **Universe 完整性**：用今天的股票名单回看过去，会自动删掉后来退市的股票；
- **PIT 语义**：财报、公告和估值必须按当时实际可获得的日期查询；
- **复权与交易状态**：如果每个研究脚本各自处理，口径很快就会分叉。

幸存者偏差是一个直观例子。下面的实验使用同一个等权买入持有策略和同一段时间，唯一差别是历史股票池里是否保留后来退市的股票。只用今天仍在交易的股票时，2016–2021 年的收益从 **5.9%** 变成了 **12.0%**：

![使用当前股票名单会造成幸存者偏差](docs/assets/survivorship-gap.zh.svg)

那些股票不是收益为零，而是根本没有进入计算。CNEquity 因此把退市股、复权因子、历史成分和 PIT（按当时可获得的信息取数）放在数据层里处理，而不是交给每个下游脚本临时拼接。

## 数据范围

项目当前的主线是中国市场的 A 股研究，不追求把所有金融数据都收进来。已注册数据集覆盖：

- 证券主数据、交易日历和交易状态；
- 个股、指数、行业和板块的日线、分钟线、分笔与复权因子；
- 公司行为、公告索引和业绩披露预约；
- 财报、估值、股本、股东和分析师一致预期；
- 北向、融资融券、龙虎榜、大宗交易和资金流；
- 指数成分、行业分类、宏观指标和市场宽度；
- 新闻、快讯、情绪、轮动，以及解禁和监管事件。

当前注册表包含 **42 个数据集：39 个 curated + 3 个 derived**，按研究用途分为 L0–L8 九类。


| 层次  | 研究用途  | 代表数据集                  |
| --- | ----- | ---------------------- |
| L0  | 基础参考  | 证券主数据、交易日历、交易状态        |
| L1  | 行情    | 日线、指数、复权因子、分钟线、分笔、退市事件 |
| L2  | 公司事件  | 公司行为、公告索引、预约披露         |
| L3  | 基本面   | 财报、估值、股本、股东、一致预期       |
| L4  | 资金面   | 北向、融资融券、龙虎榜、大宗交易、资金流   |
| L5  | 结构行业  | 指数成分、行业与板块成分           |
| L6  | 宏观    | 宏观指标、市场宽度              |
| L7  | 舆情与轮动 | 新闻、情绪、人气、板块行情与资金流      |
| L8  | 风险合规  | 解禁日程、监管事件              |


所有 curated 行都带有 `source`、`data_version` 和 `fetched_at`，可以追到来源和采集批次。分钟线、5 分钟线和分笔默认关闭，按需启用；部分只能获取当日快照的数据集不会被伪造成历史序列。

完整字段、主键、历史模式和源端限制见[数据集目录](docs/datasets/catalog.md)。
`events:*` 是 7×24 事件流组：公告和资讯周末也发，由 `cne run events` 按自然日跑，
不受交易日门禁限制（见[配置 · 事件流调度组](docs/getting-started/configuration.md)）。

<details>
<summary><b>展开查看 42 个数据集及主备数据源</b></summary>

| 数据集                            | 说明           | 主源           | 备源           | 历史                      | 调度组                |
| ------------------------------ | ------------ | ------------ | ------------ | ----------------------- | ------------------ |
| **L0 · 基础参考**                  |              |              |              |                         |                    |
| `instruments`                  | 证券主数据        | tdx_protocol | baostock     | 回填 `baostock`（TDX 失败时 QMT fallback） | core |
| `trading_calendar`             | 交易日历         | qmt_bridge   | exchange     | 可回补                     | core               |
| `trading_status`               | 交易状态（停复牌/ST） | eastmoney    | exchange     | 回填 `baostock`           | core               |
| **L1 · 行情**                    |              |              |              |                         |                    |
| `adj_factors`                  | 复权因子         | sina         | baostock     | 可回补                     | —                  |
| `commodity_bars` ○             | 商品期货主连       | sina         | eastmoney    | 可回补                     | macro_risk         |
| `daily_bars`                   | 日线           | qmt_bridge   | eastmoney    | 可回补                     | core               |
| `delisting_events`             | 退市事件         | derived      | —            | 可回补                     | —                  |
| `index_bars`                   | 指数日线         | qmt_bridge   | eastmoney    | 可回补                     | core               |
| `minute_bars` ○                | 1 分钟线        | qmt_bridge   | —            | 可回补                     | intraday           |
| `minute_bars_5m` ○             | 5 分钟线        | qmt_bridge   | —            | 可回补                     | intraday           |
| `trade_ticks` ○                | 分笔快照         | tdx_protocol | —            | 可回补                     | ticks              |
| **L2 · 公司事件**                  |              |              |              |                         |                    |
| `announcement_index`           | 公告索引         | cninfo       | —            | 可回补                     | events:disclosures |
| `corporate_actions`            | 公司行为         | qmt_bridge   | eastmoney    | 可回补（回填走 QMT；日更东财日期快照） | core               |
| `earnings_disclosure_schedule` | 业绩披露预约       | eastmoney    | —            | 可回补                     | fundamentals       |
| **L3 · 基本面**                   |              |              |              |                         |                    |
| `analyst_consensus`            | 分析师一致预期      | eastmoney    | —            | 仅当日                     | research           |
| `financial_statement_items`    | 财务报表科目       | qmt_bridge   | eastmoney    | 可回补（QMT 需先在终端下载财务数据） | fundamentals       |
| `share_structure`              | 股本结构         | eastmoney    | —            | 可回补                     | fundamentals       |
| `shareholder_counts`           | 股东户数         | eastmoney    | —            | 可回补                     | fundamentals       |
| `top_holders`                  | 前十大股东 / 流通股东 | eastmoney    | —            | 可回补                     | 按需回填               |
| `valuation_metrics`            | 估值指标         | eastmoney    | —            | 回填 `baostock`           | capital            |
| **L4 · 资金面**                   |              |              |              |                         |                    |
| `block_trades`                 | 大宗交易         | eastmoney    | exchange     | 可回补                     | signals            |
| `dragon_tiger`                 | 龙虎榜          | eastmoney    | exchange     | 可回补                     | signals            |
| `fund_flow`                    | 个股资金流        | eastmoney    | —            | 仅当日                     | capital            |
| `institutional_holdings`       | 机构持股         | eastmoney    | —            | 可回补                     | research           |
| `margin_trading`               | 融资融券         | exchange     | —            | 可回补                     | capital            |
| `northbound_flows`             | 北向资金流向       | eastmoney    | —            | 可回补                     | capital            |
| `northbound_holdings`          | 北向持股         | eastmoney    | —            | 可回补                     | capital            |
| **L5 · 结构行业**                  |              |              |              |                         |                    |
| `index_constituents`           | 指数成分         | eastmoney    | —            | 回填 `cni`                | fundamentals       |
| `industry_index`               | 行业指数         | derived      | —            | 可回补                     | —                  |
| `industry_members`             | 行业分类成分       | eastmoney    | —            | 回填 `sw`                 | fundamentals       |
| `sector_members`               | 板块成分         | eastmoney    | —            | 仅当日                     | capital            |
| **L6 · 宏观**                    |              |              |              |                         |                    |
| `macro_indicators`             | 宏观指标         | eastmoney    | pboc         | 可回补                     | macro_risk         |
| `market_breadth`               | 市场宽度         | derived      | —            | 可回补                     | macro_risk         |
| **L7 · 舆情 / 轮动**               |              |              |              |                         |                    |
| `economic_calendar` ○          | 经济日历         | eastmoney    | —            | 仅当日（源已下线）               | —                  |
| `flash_news_wire`              | 7×24 快讯      | eastmoney    | —            | 仅当日                     | events:news_wire   |
| `hot_rank`                     | 人气榜          | eastmoney    | —            | 仅当日                     | research           |
| `news_headlines`               | 新闻标题         | eastmoney    | —            | 仅当日                     | events:news_wire   |
| `sector_bars`                  | 板块行情         | ths          | —            | 回填 `ths`                | research           |
| `sector_fund_flow`             | 板块资金流        | eastmoney    | —            | 仅当日                     | research           |
| `sentiment_scores`             | 情绪评分         | derived      | eastmoney    | 可回补                     | research           |
| **L8 · 风险合规**                  |              |              |              |                         |                    |
| `regulatory_events`            | 监管事件         | cninfo       | —            | 可回补                     | events:regulatory  |
| `share_unlock_schedule`        | 解禁日程         | eastmoney    | —            | 可回补                     | macro_risk         |

○ 表示可选数据集，空表不算异常。主源 / 备源与 `DatasetSpec.primary_source` / `backup_source` 一致；「历史」列里的回填源是 `backfill_source`。逐项说明见[数据集目录](docs/datasets/catalog.md)，源端限制见[数据源说明](docs/datasets/sources.md)。

</details>



## 与 AkShare、Tushare、Qlib 有什么不同

AkShare 和其它取数工具解决“怎样调用数据源”，Tushare 提供云端数据服务，Qlib / vn.py 更偏研究或交易平台。CNEquity 做的是中间的数据基础设施：把多源数据落成可日更、可复查、可溯源的本地 Parquet 湖。


| 你在意的能力          | **CNEquity**     | AkShare / efinance | Tushare Pro | Baostock | Qlib / vn.py |
| --------------- | ---------------- | ------------------ | ----------- | -------- | ------------ |
| 本地可续跑的数据底座      | **湖 + 日更编排**     | 拉到内存，编排自管          | 云端积分，非自建湖   | 会话拉数，无湖  | 绑在平台数据子系统    |
| 历史结果能否复查        | **行级溯源 + 写前校验**  | 无统一契约              | 平台字段        | 无湖契约     | 视模块          |
| 复权 / 历史成分 / PIT | **统一在** `load()` | 自己拼接               | 自己拼接        | 自己拼接     | 用平台口径        |
| 退市股是否保留         | **保留（防幸存者偏差）**   | 看调用方               | 看接口         | 看接口      | 看数据源         |
| 单一数据源故障         | **按批失败，可单独重试**   | 调用方处理              | 平台处理        | 平台处理     | 视模块          |
| 需要注册 / token    | **不需要**          | 不需要                | 需要积分        | 不需要      | 视数据源         |


更完整的逐项比较见[项目对比](docs/comparison.md)。

## 30 秒试玩

需要 Python 3.10+，无需 token、积分或账号：

```bash
pip install cnequity
cne init --profile demo
```

`cne init --profile demo` 默认拉取 5 只股票最近约 30 个交易日的真实数据，写入独立目录 `data/cnequity-demo/`，不会覆盖正式数据湖。实测大约 25 秒。需要能访问 TDX 行情主机（大陆出口更稳）；如果连接失败，可以先检查：

```bash
cne doctor                 # 环境体检：不需要配置，也不需要网络
cne sources probe --only tdx_protocol --config configs/cnequity.demo.toml
```

完全无法连接 TDX 时，运行 `cne init --profile sample`，可离线验证安装、Parquet 落盘和查询链路。合成行全部标记为 `source=mock`，不可用于研究。

## 能力一览


|          |                                                                           |
| -------- | ------------------------------------------------------------------------- |
| **采集**   | 42 个数据集 · 15 个上游端点（`cne sources probe` 逐一探测）· 主备路由 · 批次级重试、断点续跑与水位对账      |
| **研究口径** | 复权（hfq / qfq 查询侧换算）· 历史指数与行业成分 · PIT 财报 · **保留退市股**                       |
| **数据契约** | 写前 schema 校验 · 行级溯源（`source` / `data_version` / `fetched_at`）· 破坏性变更必须提版本 |
| **质量**   | 88 项审计检查 · 跨源比对 · 覆盖缺口与陈旧检测 · 可配置发布门禁                                     |
| **存储**   | 本地 Parquet + DuckDB · 按数据集选择分区粒度 · 原子写 · 不可变代与时间旅行                        |
| **消费**   | `load()` · DuckDB 视图 · Polars · MCP 6 个工具 · 只读运维控制台                       |
| **运维**   | 日更编排 · launchd / cron 模板 · 源健康探针 · 可移植快照与增量包                              |


全部本地运行，**不需要注册、token 或积分**。

![cne init --profile demo 分阶段采集真实日线并打印结果](docs/assets/cne-demo.png)

然后在 Python 中读取：

```python
from cnequity.query import load

bars = load("daily_bars", data_root="data/cnequity-demo")
print(bars.tail())
```

想直接比较原始价格与后复权口径：

```bash
cne init --profile demo --research --symbols 600519.SH
# 示例：raw return -24.25% → hfq return -14.39%（随 as-of 日期变化）
```



## 数据运维页面

建好湖之后，日常要确认的是覆盖有没有跟上、哪里断了、上次审计留下了什么。`cne serve` 打开只读控制台：

```bash
cne serve                 # http://127.0.0.1:8787
```

![cne serve 数据运维页面：湖状态、覆盖热力与行动项](docs/assets/cne-serve-hero-demo.png)

示意截图，图中标有 ILLUSTRATIVE DEMO；完整覆盖热力不是对当前生产湖的声明。

概览页给出健康状态、Fresh / Stale 计数、覆盖热力和行动项。另外三个页面分别看数据集契约与水位、跑批时间线，以及审计 findings、跨源比对和隔离区。控制台不写湖：采集、重试和清理仍走 CLI，页面只显示该复制的命令。非回环地址必须加 `--token`。

## 能回答哪些问题


| 研究问题                    | 推荐入口                                                          |
| ----------------------- | ------------------------------------------------------------- |
| 茅台过去五年复权后涨了多少           | `load("daily_bars", symbols=[...], adjust="hfq")`             |
| 茅台 PE 在自身五年历史中的分位数      | `valuation_metrics` + 窗口分位                                    |
| 2018 年财报因子的 IC，且不使用未来数据 | `load("financial_statement_items", as_of="2018-04-30")`       |
| 退市股退市前 60 天的价格形态        | `delisting_events` + `daily_bars`                             |
| 三年前的沪深 300 成分或申万行业      | `index_constituents` · `industry_members`                     |
| 今天的龙虎榜、未来解禁和板块资金流       | `dragon_tiger` · `share_unlock_schedule` · `sector_fund_flow` |


常用查询：

```python
from cnequity.query import load

bars = load(
    "daily_bars",
    start="2020-01-01",
    end="2025-12-31",
    symbols=["600519.SH"],
    adjust="hfq",
)

roe = load(
    "financial_statement_items",
    items=["roe"],
    as_of="2024-04-30",
)
```



## 5 分钟开始建湖

```bash
pip install cnequity
cne config create          # 生成 configs/cnequity.toml
cne init                   # 全市场标的，默认回溯最近 3 年
cne run daily --all-groups # 之后每个交易日执行（见下方「日常使用与运维」）
```

最后一条请带 `--all-groups`：不带的 `cne run daily` 只跑核心骨架，湖会停在 15/42 新鲜，而且不会报错。

### `init` 到底拉多少、要多久？

先说结论：默认 `cne init` 大约 **1 小时**，拉的是 **沪深京全市场**（5,000+ 只）最近 **3 年** 的证券、日历、日线、交易状态等主干。不是只拉 400 只股票。日频主干通常是 **几百 MB**；打开分钟线才会到 GB 级以上。

选哪条命令，其实只看两件事：

- **股票拉多少**：默认已经是全市场。`--profile full` 不会再多拉股票。
- **历史拉多长**：默认近 3 年；`--profile full` 把主干加深到各数据集自己的历史起点，其中日线从 2016-01-01 起。

想先看命令怎么跑，用 demo（约 25 秒，写到独立目录，不碰正式湖）。正式湖用默认 `cne init` 就可以开始用；需要 2016 年起的日线再加 `--profile full`。历史 ST 是另一趟更慢的扫描，要另外跑 `cne backfill trading_status`。


| 命令                              | 实际范围                                      | 参考耗时                                         | 磁盘量级                 |
| ------------------------------- | ----------------------------------------- | -------------------------------------------- | -------------------- |
| `cne init --profile demo`       | 5 只股票 × 最近约 30 个交易日，独立 demo 湖             | 约 25 秒                                       | 数 MB                 |
| `cne init`（即 `--profile quick`） | 沪深京全市场（5,000+ 只）× 最近 3 年；证券、日历、日线、交易状态等主干 | 通常约 1 小时                                     | 日频主干通常几百 MB          |
| `cne init --profile full`       | 还是全市场；主干按各数据集默认起点拉取，日线从 2016-01-01 起      | 通常约 3 小时，约为 quick 的 3 倍                      | 日频更长，仍是几百 MB 到约 1 GB |
| `cne backfill trading_status`   | 补齐全市场历史 ST 证据；约 5,500 只                   | 整轮约 10–11 小时；同范围 init 已扫 400 只后通常还需约 9–10 小时 | 增量很小                 |


这些是实测量级，不是时限承诺。TDX / Baostock 连不连得上、出口位置、上游限流、重试次数和机器配置都会改变耗时；以命令打印的批次进度和 ETA 为准。

全市场日频 2001–2026 合计约 **468 MB**，可当作日频主干的上限参照。运行中的 staging 和 revision 会再占一份；日积月累、又开了分钟线的生产湖可以到十几 GB。分钟线默认关闭：全市场 1 分钟线约 **8.4 GB/年**。详见[运行手册 · 日内数据](docs/operations/runbook.md#日内数据minute_bars--minute_bars_5m)。

> **进度里出现 400 只，并不表示 init 只拉了 400 只。** 日线、证券列表等主干仍然扫描全市场。`400` 只限制最慢的 **Baostock 历史 ST 状态**：首次 `init` 每轮先扫 400 **只证券**（不是 400 条数据）就暂停。要补完，运行 `cne backfill trading_status`（会自动取消上限）。同一历史起止日和 universe 会从 checkpoint 继续；改变范围会按新范围重新开一轮。

新湖如果希望主干数据和历史 ST 都达到项目约定的完整范围，按顺序跑：

```bash
cne init --profile full --config configs/cnequity.toml
cne backfill trading_status --config configs/cnequity.toml
cne run daily --all-groups --config configs/cnequity.toml
```

前两条最好同一天接着跑。中断续跑、怎样才算「完整」、`status` 如何核对截面，见[快速开始 · Init：范围、磁盘与续跑](docs/getting-started/quickstart.md#init-scope)。

详细安装说明见[快速开始](docs/getting-started/quickstart.md)和[安装指南](docs/getting-started/installation.md)。

## 适合什么场景

CNEquity 适合需要反复使用同一份历史数据的研究和数据工作：

- 多年行情回测，不想每次重新拉取、清洗和拼接复权；
- 需要把退市股、历史成分股和 PIT 纳入研究；
- 希望数据保存在本地或自己的服务器上，格式开放且来源可追溯；
- 想让 Python、DuckDB、Polars 和 AI agent 读取同一份数据。

如果只是查一只股票的最新价格，直接调用数据接口通常更轻。这个项目更适合需要持续积累、反复查询和复查结果的场景。

## 架构

![CNEquity 架构图](docs/assets/architecture-diagram-v3.png)

公开数据源 → 适配与编排 → 本地 Parquet 湖 → 质量、查询与只读服务

架构上的边界比较简单：适配器负责把多源数据取回来；编排层负责 DAG、批次和重试；数据先进入 staging，再压实为 curated 并计算 derived；质量层持续审计；查询和服务层只读消费。展开见[架构说明](docs/architecture/overview.md)。

## 日常使用与运维

```bash
cne run daily --all-groups    # 一条命令跑完当天全部调度组
cne run daily --group core    # 或只跑其中一个组（全部 6 个见下）
cne status                    # 查看 fresh / STALE / empty / no source
cne serve                     # 打开 http://127.0.0.1:8787
cne sources probe             # 检查上游数据源健康度
cne run retry --run-id RUN_ID # 只重试失败批次
cne run retry --failed-groups # 重试各 daily 分组最新的失败 run
```

日更按**调度组**执行，一天跑 6 个：`core`、`capital`、`signals`、`fundamentals`、
`macro_risk`、`research`。不带 `--group` 的 `cne run daily` 只跑 `[[job.daily.waves]]`
里的核心骨架（行情、日历、交易状态、公司行为、复权），**不包含**估值、财报、融资融券、
龙虎榜、北向、指数成分等其余数据集 —— 只跑这一条，湖会停在 15/42 新鲜。

单个 step 失败时，系统会记录 failed batch，其他步骤继续落盘；重试不会把整条任务重新跑一遍。覆盖和新鲜度也可以在上一节的数据运维页面里看。

挂入 crontab 即可自动日更。最省事的一条是 `--all-groups`（按配置顺序串行跑完全部组，
某个组失败不影响后面的组，退出码取最差的一个）：

```bash
 5 16 * * 1-5  cd /path/to/lake && cne run daily --all-groups >> logs/daily.log 2>&1
```

想给每个组留出更宽的窗口、错开打同一批上游时，也可以一组一条：

```bash
# 交易日收盘后依次执行；非交易日各组都会自动跳过
 5 16 * * 1-5  cd /path/to/lake && cne run daily --group core        >> logs/daily.log 2>&1
35 16 * * 1-5  cd /path/to/lake && cne run daily --group capital     >> logs/daily.log 2>&1
 5 17 * * 1-5  cd /path/to/lake && cne run daily --group signals     >> logs/daily.log 2>&1
35 17 * * 1-5  cd /path/to/lake && cne run daily --group fundamentals >> logs/daily.log 2>&1
 5 18 * * 1-5  cd /path/to/lake && cne run daily --group macro_risk  >> logs/daily.log 2>&1
35 18 * * 1-5  cd /path/to/lake && cne run daily --group research    >> logs/daily.log 2>&1
```

有仓库 checkout 时，`scripts/daily_pipeline.sh` 会按依赖顺序跑完全部分组，再做健康检查、
源探测和元数据备份，一条 cron 即可（该脚本不随 PyPI 包安装）。

更多运维方式见[运行手册](docs/operations/runbook.md)、[数据源健康检查](docs/operations/source-health.md)和[故障排查](docs/operations/troubleshooting.md)。

## 接给 AI agent

`cne mcp` 以只读方式把本地湖提供给模型；采集、重试和清理仍由 CLI 完成。

```bash
cne mcp --config "$(pwd)/configs/cnequity.toml"
```

把上面的命令作为 MCP server 注册到任意兼容客户端即可。大多数客户端使用等价的配置（客户端名称和界面可能不同）：

```json
{
  "mcpServers": {
    "cnequity": {
      "command": "cne",
      "args": ["mcp", "--config", "/abs/path/to/cnequity.toml"]
    }
  }
}
```

`--config` 必须使用绝对路径。接好后可以直接问：

- “茅台过去五年复权后涨了多少？”
- “茅台当前 PE 在自己五年历史里处于什么分位？”
- “计算 2018 年财报因子的 IC，不要使用未来数据。”
- “过去三年退市的股票，退市前 60 天有什么共同形态？”

还没有正式湖时，可以先运行 `cne init --profile demo`，再使用生成的 demo 配置。完整说明见[MCP 参考](docs/reference/mcp.md)。

## 常见问题

**初始化要多久、占多少磁盘？**

默认 `cne init` 大约 1 小时、日频主干几百 MB；`--profile full` 通常约 3 小时。两者都是全市场，不是 400 只。进度里的 400 只上限只作用于历史 ST 证据；要补完，运行 `cne backfill trading_status`。网络和数据源状态会影响结果，详见上方表格，以及[快速开始 · Init：范围、磁盘与续跑](docs/getting-started/quickstart.md#init-scope)。

再浅于 3 年几乎省不下时间：窗口一短，逐标的往返开销就占主导，1 年和 3 年耗时接近，但只有 3 年撑得起多年因子窗口。

没有 profile 默认拉到 2001：`full` 的日线从 2016-01-01 起。需要更早的日线时：

```bash
cne init --since 2001-01-01
# 或事后补深
cne backfill daily_bars --start 2001-01-01
```

**为什么落盘只存后复权因子？**

前复权价格会随“今天”变化。落盘只存 hfq，qfq 在 `load(adjust="qfq")` 时计算，详见 [ADR-0004](docs/adr/0004-store-hfq-derive-qfq-at-query.md)。

**东财返回 403 / 502 或连接被重置怎么办？**

这通常是非大陆出口被东财风控，大陆网络一般遇不到。先跑 `cne sources probe --only eastmoney_push2,eastmoney_push2his`。日线主路径走 TDX，core 行情一般还能跑；资金面、估值、公司行为等仍走东财。海外可给 `[sources.eastmoney] proxy` 配大陆出口（或设 `HTTPS_PROXY`），详见[排障](docs/operations/troubleshooting.md#东财-502--连接被重置海外出口)。

**为什么分钟线没有更早历史？**

源端当前大约保留 95 个交易日的 1 分钟线、491 个交易日的 5 分钟线（约两年）。这是上游滚动保留期，不是湖没回填完；更早的窗口 `cne backfill` 会直接拒绝。分钟线默认不在 `cne init` 和日更里，需要时再单独回填。

**数据可以商用或再分发吗？**

项目代码使用 Apache-2.0；落盘的行情、公告等数据不随代码授权。使用与分发前请阅读[法律与数据源说明](docs/legal-and-data-sources.md)。

## 文档与项目状态

- [快速开始](docs/getting-started/quickstart.md) · [CLI 参考](docs/reference/cli.md)（[19 个命令一览](docs/reference/cli.md#命令一览)）· [完整文档索引](docs/README.md)
- [数据集目录](docs/datasets/catalog.md) · [MCP 参考](docs/reference/mcp.md) · [运维手册](docs/operations/runbook.md)
- [CHANGELOG](CHANGELOG.md) · [安全策略](SECURITY.md)

这是个人维护的开源项目，issue 和 PR 都欢迎。用于论文或研究报告时，可引用仓库中的 [CITATION.cff](CITATION.cff)，并记录版本、覆盖范围及复权 / PIT 口径。

代码使用 [Apache-2.0](LICENSE)。仓库不附带数据湖，也不授予上游数据的再分发权。

---

如果 CNEquity 帮你省下了搭建数据底座的时间，欢迎点个 ⭐，让更多做 A 股研究的人看到它。
