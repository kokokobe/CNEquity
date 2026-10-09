"""Durable lake switches the operations page may write.

The page previews a diff, then writes only the whitelisted keys back into the
existing TOML. Comments, secrets, intervals and every other key stay. A
timestamped backup is kept beside the file, the same way ``cne config upgrade``
does. Saving settings does not start a fetch.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cnequity.config import load_config
from cnequity.config.loader import push2_paused_by_env
from cnequity.config.upgrade import _table_span, tomllib
from cnequity.file_lock import LockUnavailable, exclusive_lock
from cnequity.serve.labels_en import en
from cnequity.serve.ops.catalog import OpsError, config_file
from cnequity.serve.ops.scheduler import replace_config, state_dir

HOME_NOTE = (
    "这些开关会改之后的每一次取数，包括定时日更和收尾补抓。"
    "保存设置不会启动取数。标的列表、频率和交易所仍在配置文件里。"
)
NOTE_CHANGED = "将写入当前配置，并先备份原文件。这次不会启动取数。"
NOTE_UNCHANGED = "没有需要保存的改动。"
ACKNOWLEDGEMENT = "我确认保存这些设置。定时日更、收尾补抓和之后的命令都会按新值执行。"
PUSH2_ENV_NOTE = (
    "本机环境变量 CNE_PUSH2_PAUSED 开着，关掉配置里的暂停无效，push2 仍然不会发出请求。"
)
STRUCTURE = "配置结构不支持自动修改这些开关，请使用标准 TOML 表。"

_PREVIEW_TTL = 600
_VALUE = (
    r"(?:true|false|\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|"
    r"[+-]?\d+(?:\.\d+)?|[A-Za-z_][\w-]*)"
)


@dataclass(frozen=True)
class Setting:
    id: str
    group: str
    table: str
    key: str
    kind: str  # bool, choice
    label: str
    help: str
    default: bool | str
    choices: tuple[tuple[str, str], ...] = ()
    scope: Callable[[Any], str] | None = None


def _listed(items, *, empty: str) -> str:
    names = [str(item) for item in items if str(item).strip()]
    if not names:
        return empty
    shown = "、".join(names[:8])
    if len(names) > 8:
        shown += f" 等 {len(names)} 个"
    return shown


def _minute_scope(config) -> str:
    freqs = _listed(config.minute_bars_frequencies, empty="未指定频率")
    symbols = _listed(config.minute_bars_symbols, empty="")
    extra = f"，标的 {symbols}" if symbols else ""
    return f"范围仍在配置文件里：{config.minute_bars_scope}，频率 {freqs}{extra}。"


def _ticks_scope(config) -> str:
    symbols = _listed(config.trade_ticks_symbols, empty="未列标的")
    return (
        f"范围仍在配置文件里：{config.trade_ticks_scope}，"
        f"上限 {config.trade_ticks_max_symbols}，标的 {symbols}。"
    )


def _futures_scope(config) -> str:
    exchanges = _listed(config.futures_exchanges, empty="全部已支持的路由")
    options = "含期权" if config.futures_options else "不含期权"
    return (
        f"范围仍在配置文件里：交易所 {exchanges}，{options}，"
        f"大商所路由 {config.futures_dce_route}。"
    )


def _futures_minute_scope(config) -> str:
    products = _listed(config.futures_minute_products, empty="未列品种")
    contracts = _listed(config.futures_minute_contracts, empty="未列合约")
    return (
        f"范围仍在配置文件里：品种 {products}，合约 {contracts}，"
        f"上限 {config.futures_minute_max_contracts}。"
    )


def _listed_en(items, *, empty: str) -> str:
    names = [str(item) for item in items if str(item).strip()]
    if not names:
        return empty
    shown = ", ".join(names[:8])
    if len(names) > 8:
        shown += f" and {len(names) - 8} more"
    return shown


def _minute_scope_en(config) -> str:
    freqs = _listed_en(config.minute_bars_frequencies, empty="no frequency set")
    symbols = _listed_en(config.minute_bars_symbols, empty="")
    extra = f", symbols {symbols}" if symbols else ""
    return f"The scope stays in the config file: {config.minute_bars_scope}, frequencies {freqs}{extra}."


def _ticks_scope_en(config) -> str:
    symbols = _listed_en(config.trade_ticks_symbols, empty="no symbols listed")
    return (
        f"The scope stays in the config file: {config.trade_ticks_scope}, "
        f"limit {config.trade_ticks_max_symbols}, symbols {symbols}."
    )


def _futures_scope_en(config) -> str:
    exchanges = _listed_en(config.futures_exchanges, empty="every supported route")
    options = "with options" if config.futures_options else "without options"
    return (
        f"The scope stays in the config file: exchanges {exchanges}, {options}, "
        f"DCE route {config.futures_dce_route}."
    )


def _futures_minute_scope_en(config) -> str:
    products = _listed_en(config.futures_minute_products, empty="no products listed")
    contracts = _listed_en(config.futures_minute_contracts, empty="no contracts listed")
    return (
        f"The scope stays in the config file: products {products}, contracts {contracts}, "
        f"limit {config.futures_minute_max_contracts}."
    )


_SCOPE_EN = {
    "_minute_scope": _minute_scope_en,
    "_ticks_scope": _ticks_scope_en,
    "_futures_scope": _futures_scope_en,
    "_futures_minute_scope": _futures_minute_scope_en,
}


_INGEST = (
    ("all_a", "全部 A 股"),
    ("all_a_sh_sz", "不含北交所"),
    ("all_instruments", "全部代码（含 ETF/LOF）"),
)

SETTINGS: tuple[Setting, ...] = (
    Setting(
        "push2_paused",
        "sources",
        "sources.eastmoney",
        "push2_paused",
        "bool",
        "暂停 push2",
        "停掉 push2、push2his 和 push2delay，请求不会发出。datacenter 和其他东财主机继续。"
        "这和下面的东财总闸是两个开关。",
        False,
    ),
    Setting(
        "tdx",
        "sources",
        "tdx_protocol",
        "enabled",
        "bool",
        "通达信",
        "日线主源。关掉后日线不再向通达信取数。",
        True,
    ),
    Setting(
        "qmt_bridge",
        "sources",
        "qmt_bridge",
        "enabled",
        "bool",
        "QMT 本地桥",
        "启用本地 BigQMT 终端桥。"
        "日线、指数、分钟线、交易日历、财务、公司行动和复权因子优先走 QMT；"
        "逐笔成交、指数成分和股东户数只在原源失败或关闭时用 QMT 补位。"
        "BigQMT 离线时自动回落，桥路径和终端参数仍在配置文件里。",
        False,
    ),
    Setting(
        "eastmoney",
        "sources",
        "sources.eastmoney",
        "enabled",
        "bool",
        "东财",
        "公告、财务和资金流。关掉后不再请求东财。",
        True,
    ),
    Setting(
        "sina_bars",
        "sources",
        "sources.sina_bars",
        "enabled",
        "bool",
        "新浪日线",
        "日线兜底。关掉后不再用新浪补日线。",
        True,
    ),
    Setting(
        "baostock",
        "sources",
        "sources.baostock",
        "enabled",
        "bool",
        "Baostock",
        "历史行情兜底。",
        True,
    ),
    Setting(
        "bse",
        "sources",
        "sources.bse",
        "enabled",
        "bool",
        "北交所官网",
        "当日北交所行情。它不是历史源。",
        True,
    ),
    Setting(
        "ths",
        "sources",
        "sources.ths",
        "enabled",
        "bool",
        "同花顺页面",
        "板块行情。关掉后不再请求同花顺公开页。",
        True,
    ),
    Setting(
        "cninfo",
        "sources",
        "sources.cninfo",
        "enabled",
        "bool",
        "巨潮",
        "公告和监管。关掉后不再请求巨潮。",
        True,
    ),
    Setting(
        "minute_bars",
        "capture",
        "minute_bars",
        "enabled",
        "bool",
        "分钟线",
        "默认关闭。打开后日更会按当前范围持续抓取。",
        False,
        scope=_minute_scope,
    ),
    Setting(
        "trade_ticks",
        "capture",
        "trade_ticks",
        "enabled",
        "bool",
        "分笔",
        "默认关闭。打开后日更会按当前范围持续抓取。",
        False,
        scope=_ticks_scope,
    ),
    Setting(
        "futures",
        "capture",
        "futures",
        "enabled",
        "bool",
        "期货与期权",
        "默认关闭。打开后日更会抓取期货和期权。",
        False,
        scope=_futures_scope,
    ),
    Setting(
        "futures_minute",
        "capture",
        "futures",
        "minute_enabled",
        "bool",
        "期货分钟线",
        "默认关闭。打开后日更会抓取期货分钟线。",
        False,
        scope=_futures_minute_scope,
    ),
    Setting(
        "ingest",
        "universe",
        "universe",
        "ingest",
        "choice",
        "日更覆盖的证券",
        "决定日更抓哪些证券。ST、停牌和已退市名称都会保留。",
        "all_a",
        choices=_INGEST,
    ),
    Setting(
        "ingest_eligible_etfs",
        "universe",
        "universe",
        "ingest_eligible_etfs",
        "bool",
        "同时抓取符合条件的 ETF 日线",
        "额外抓取最近可回放、且交易所目录判定合格的 ETF 日线。默认关闭。",
        False,
    ),
)

_BY_ID = {spec.id: spec for spec in SETTINGS}
GROUPS = (("sources", "数据源"), ("capture", "可选采集"), ("universe", "日更覆盖"))


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeError as exc:
        raise OpsError("配置文件不是 UTF-8，无法在页面上修改。") from exc


def _lookup(raw: dict, spec: Setting):
    node: Any = raw
    for part in spec.table.split("."):
        if not isinstance(node, dict) or part not in node:
            return spec.default
        node = node[part]
    if isinstance(node, dict):
        if spec.key not in node:
            return spec.default
        return node[spec.key]
    # ``eastmoney = false`` is the enabled flag, not a table.
    if isinstance(node, bool) and spec.key == "enabled":
        return node
    return spec.default


def read_values(text: str) -> dict[str, Any]:
    try:
        raw = tomllib.loads(text)
    except ValueError as exc:
        raise OpsError("配置文件无法解析，请先修正 TOML。") from exc
    found: dict[str, Any] = {}
    for spec in SETTINGS:
        value = _lookup(raw, spec)
        if spec.kind == "bool" and not isinstance(value, bool):
            raise OpsError(f"{spec.label} 的配置不是 true 或 false。")
        if spec.kind == "choice" and value not in {item[0] for item in spec.choices}:
            raise OpsError(f"{spec.label} 的当前取值不在可选范围内。")
        found[spec.id] = value
    return found


def normalize(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise OpsError("设置不完整。")
    unknown = set(raw) - set(_BY_ID)
    if unknown:
        raise OpsError("有页面不接受的设置。")
    missing = [spec.label for spec in SETTINGS if spec.id not in raw]
    if missing:
        raise OpsError("设置不完整。")
    out: dict[str, Any] = {}
    for spec in SETTINGS:
        value = raw[spec.id]
        if spec.kind == "bool":
            if not isinstance(value, bool):
                raise OpsError(f"{spec.label} 需要是开关。")
        elif not isinstance(value, str) or value not in {item[0] for item in spec.choices}:
            raise OpsError(f"{spec.label} 的取值无效。")
        out[spec.id] = value
    return out


def _display(spec: Setting, value: Any) -> str:
    if spec.kind == "bool":
        return "开" if value else "关"
    for choice, label in spec.choices:
        if choice == value:
            return label
    return str(value)


def _literal(spec: Setting, value: Any) -> str:
    if spec.kind == "bool":
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def _assignment(key: str) -> re.Pattern[str]:
    ident = rf'(?:{re.escape(key)}|"{re.escape(key)}"|\'{re.escape(key)}\')'
    return re.compile(rf"^(\s*{ident}\s*=\s*)({_VALUE})(\s*(?:#.*)?)(\r?\n)?$")


def _assign_expected(tree: dict, spec: Setting, value: Any) -> None:
    node = tree
    parts = spec.table.split(".")
    for part in parts[:-1]:
        current = node.get(part)
        if current is None:
            node[part] = {}
            current = node[part]
        if not isinstance(current, dict):
            raise OpsError(STRUCTURE)
        node = current
    leaf_name = parts[-1]
    leaf = node.get(leaf_name)
    if leaf is None:
        node[leaf_name] = {}
        leaf = node[leaf_name]
    if not isinstance(leaf, dict):
        raise OpsError(STRUCTURE)
    leaf[spec.key] = value


def _set_assignment(text: str, table: str, key: str, literal: str) -> str:
    lines = text.splitlines(keepends=True)
    span = _table_span(lines, table)
    pattern = _assignment(key)
    if span is None:
        newline = "\r\n" if "\r\n" in text else "\n"
        return (
            text.rstrip("\r\n") + f"{newline}{newline}[{table}]{newline}{key} = {literal}{newline}"
        )
    start, end = span
    for index in range(start + 1, end):
        match = pattern.match(lines[index])
        if match is None:
            continue
        ending = match.group(4)
        if ending is None:
            ending = "" if index == len(lines) - 1 else "\n"
        lines[index] = f"{match.group(1)}{literal}{match.group(3)}{ending}"
        return "".join(lines)
    newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
    lines.insert(start + 1, f"{key} = {literal}{newline}")
    return "".join(lines)


def revise(text: str, desired: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Return edited TOML and the public change list. The file is not touched."""
    current = read_values(text)
    changes = [
        {
            "id": spec.id,
            "label": spec.label,
            "before": current[spec.id],
            "after": desired[spec.id],
            "before_label": _display(spec, current[spec.id]),
            "after_label": _display(spec, desired[spec.id]),
        }
        for spec in SETTINGS
        if current[spec.id] != desired[spec.id]
    ]
    if not changes:
        return text, []
    try:
        raw = tomllib.loads(text)
    except ValueError as exc:
        raise OpsError("配置文件无法解析，请先修正 TOML。") from exc
    expected = copy.deepcopy(raw)
    for spec in SETTINGS:
        if current[spec.id] != desired[spec.id]:
            _assign_expected(expected, spec, desired[spec.id])
    edited = text
    for spec in SETTINGS:
        if current[spec.id] == desired[spec.id]:
            continue
        edited = _set_assignment(edited, spec.table, spec.key, _literal(spec, desired[spec.id]))
    try:
        parsed = tomllib.loads(edited)
    except ValueError as exc:
        raise OpsError(STRUCTURE) from exc
    if parsed != expected:
        raise OpsError(STRUCTURE)
    return edited, changes


def _backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    number = 2
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak-{stamp}-{number}")
        number += 1
    shutil.copy2(path, backup)
    return backup


def _public(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("id", "label", "before", "after", "before_label", "after_label")
    shown = []
    for change in changes:
        row = {key: change[key] for key in keys}
        for key in ("label", "before_label", "after_label"):
            row[f"{key}_en"] = en(change[key])
        shown.append(row)
    return shown


class SettingsService:
    def __init__(self, config):
        path = config_file(config)
        if path is None:
            raise OpsError("先生成配置，再修改取数设置。")
        self.config = config
        self.path = path
        self.previews: dict[str, dict] = {}

    def _fresh(self):
        config = load_config(self.path)
        if Path(config.meta_root) != Path(self.config.meta_root):
            raise OpsError("配置的数据目录已经变化，请重新启动 serve 后再改取数设置。")
        return config

    def home(self) -> dict:
        config = self._fresh()
        values = read_values(_read(self.path))
        sections = []
        for group_id, title in GROUPS:
            items = []
            for spec in SETTINGS:
                if spec.group != group_id:
                    continue
                item = {
                    "id": spec.id,
                    "kind": spec.kind,
                    "label": spec.label,
                    "label_en": en(spec.label),
                    "help": spec.help,
                    "help_en": en(spec.help),
                    "value": values[spec.id],
                    "scope": spec.scope(config) if spec.scope else None,
                    "scope_en": _SCOPE_EN[spec.scope.__name__](config) if spec.scope else None,
                }
                if spec.choices:
                    item["choices"] = [
                        {"value": value, "label": label, "label_en": en(label)}
                        for value, label in spec.choices
                    ]
                items.append(item)
            sections.append(
                {"id": group_id, "title": title, "title_en": en(title), "settings": items}
            )
        env = push2_paused_by_env()
        return {
            "note": HOME_NOTE,
            "note_en": en(HOME_NOTE),
            "push2_env_paused": env,
            "push2_env_note": PUSH2_ENV_NOTE if env else None,
            "push2_env_note_en": en(PUSH2_ENV_NOTE) if env else None,
            "sections": sections,
        }

    def preview(self, raw: dict[str, Any] | None) -> dict:
        desired = normalize(raw)
        file_bytes = self.path.read_bytes()
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeError as exc:
            raise OpsError("配置文件不是 UTF-8，无法在页面上修改。") from exc
        edited, changes = revise(text, desired)
        env = push2_paused_by_env()
        payload = {
            "token": None,
            "changes": _public(changes),
            "note": NOTE_CHANGED if changes else NOTE_UNCHANGED,
            "note_en": en(NOTE_CHANGED if changes else NOTE_UNCHANGED),
            "acknowledgement": ACKNOWLEDGEMENT if changes else None,
            "acknowledgement_en": en(ACKNOWLEDGEMENT) if changes else None,
            "push2_env_note": PUSH2_ENV_NOTE if env else None,
            "push2_env_note_en": en(PUSH2_ENV_NOTE) if env else None,
        }
        if not changes:
            return payload
        now = time.monotonic()
        self.previews = {
            key: value for key, value in self.previews.items() if value["expires"] > now
        }
        if len(self.previews) >= 16:
            self.previews.pop(next(iter(self.previews)))
        token = secrets.token_urlsafe(32)
        self.previews[token] = {
            "digest": hashlib.sha256(file_bytes).hexdigest(),
            "text": edited,
            "desired": desired,
            "public": payload["changes"],
            "expires": now + _PREVIEW_TTL,
        }
        payload["token"] = token
        return payload

    def apply(self, token: str, *, acknowledged: bool) -> dict:
        if not acknowledged:
            raise OpsError("请先确认这些设置会用于之后的取数。")
        try:
            with exclusive_lock(state_dir(self.config) / "control.lock", blocking=False):
                plan = self.previews.get(token)
                if plan is None or plan["expires"] <= time.monotonic():
                    self.previews.pop(token, None)
                    raise OpsError("预览已失效，请重新预览。")
                if hashlib.sha256(self.path.read_bytes()).hexdigest() != plan["digest"]:
                    self.previews.pop(token, None)
                    raise OpsError("配置文件已经变了，请重新预览。")
                backup = _backup(self.path)
                try:
                    replace_config(self.path, plan["text"])
                    written = read_values(_read(self.path))
                    if any(written[spec.id] != plan["desired"][spec.id] for spec in SETTINGS):
                        raise OpsError("写入后的配置和预览不一致。")
                    load_config(self.path)
                except Exception as exc:
                    shutil.copy2(backup, self.path)
                    if isinstance(exc, OpsError):
                        raise
                    raise OpsError("写入配置失败，已恢复原文件。") from exc
                self.previews.pop(token, None)
                return {
                    "saved": True,
                    "backup_name": backup.name,
                    "changes": plan["public"],
                }
        except LockUnavailable as exc:
            raise OpsError("配置正在被修改，请稍后重试。") from exc
