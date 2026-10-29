// Inventory reads manifests only; verification and restore use the job workflow.
import { dsWithCode, tr } from "./i18n.js";
export async function renderBackups(ctx, home, alive, root = "") {
  const { api, esc } = ctx;
  const host = document.getElementById("ops-backups");
  if (!host) return;
  const revision = (host.backupRevision || 0) + 1;
  host.backupRevision = revision;
  const current = () => alive() && host.backupRevision === revision;
  let inventory;
  try {
    inventory = await api(`/api/ops/backups${root ? `?root=${encodeURIComponent(root)}` : ""}`);
  } catch (err) {
    if (current()) host.innerHTML = `<p class="panel-note err">${esc(err.message)}</p><button id="backup-retry" class="button" type="button">${tr("返回默认备份目录", "Use the default backup directory")}</button>`;
    if (current()) document.getElementById("backup-retry").onclick = () => renderBackups(ctx, home, alive);
    return;
  }
  if (!current()) return;
  const link = (op, name = "") => `#/ops?${new URLSearchParams({ op, name, snapshot_root: inventory.root }).toString()}`;
  const rows = inventory.snapshots.map((item) => `<li><div><strong>${esc(item.name)}</strong>${item.error ? ` <span class="err">${esc(item.error)}</span>` : `<span class="meta-row"><span>${esc(item.created_at || "")}</span><span>${esc(item.datasets.map(dsWithCode).join(", "))}</span><span>${esc(item.files)} ${tr("文件", "files")}</span><span>${esc((item.bytes / 1024 / 1024).toFixed(1))} MiB</span><span>${tr("待校验", "Not verified")}</span></span>`}</div>${home.mode.ops_enabled && !item.error ? `<span class="cell-actions"><a href="${esc(link("snapshot.verify", item.name))}">${tr("校验", "Verify")}</a><a href="${esc(link("snapshot.restore", item.name))}">${tr("恢复到新目录", "Restore to a new directory")}</a></span>` : ""}</li>`).join("");
  host.innerHTML = `<div class="panel-header"><h2>${tr("数据备份", "Backups")}</h2>${home.mode.ops_enabled ? `<a class="button button-ghost" href="${esc(link("snapshot.create"))}">${tr("创建备份", "Create backup")}</a>` : ""}</div>
    <p class="panel-note">${tr("恢复前会校验全部文件，并写到新的或空目录。", "Restore verifies every file, then writes to a new or empty directory.")}</p>
    <form id="backup-directory" class="ops-fields"><label>${tr("备份目录（绝对路径）", "Backup directory (absolute path)")}<input id="backup-root" value="${esc(inventory.root)}" required></label><div class="action-row"><button class="button" type="submit">${tr("读取 / 刷新", "Load / refresh")}</button></div></form>
    ${rows ? `<ul class="ops-jobs">${rows}</ul>` : `<p class="muted">${tr("该目录中还没有数据备份。", "No backups in this directory yet.")}</p>`}`;
  document.getElementById("backup-directory").onsubmit = (event) => {
    event.preventDefault();
    return renderBackups(ctx, home, alive, document.getElementById("backup-root").value.trim());
  };
}
