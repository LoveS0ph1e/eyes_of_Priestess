"""Palimpsest — offline atomic rewrite for md-sourced memory (EverOS).

A palimpsest is a manuscript scraped clean and written over; so is this tool — it
rewrites episode / profile / foresight / atomic-fact memory *at rest*, atomically
and reversibly.

INVARIANTS (see PALIMPSEST.md for the full design)
  * md is the single source of truth; lancedb + sqlite index AND plugin caches are
    derived. We only ever write md (+ plugin caches), then rebuild the index.
  * Run with the EverOS service STOPPED. `apply` / `rollback` refuse otherwise.
    `plan` / `verify` are read-only and may run anytime. This holds for BOTH
    reindex modes below — incremental only narrows *what* gets re-embedded
    after restart, it never relaxes the stopped-during-apply requirement
    (the daemon's watcher stays live while running, so writing md through it
    would race; see PALIMPSEST.md for the full argument).
  * Declarative target state -> idempotent. Snapshot before write -> reversible.

Two reindex modes, chosen at apply time:
  * full (default)   — drop `.index`; next start cold-rebuilds everything.
    Simple, always correct, costs a full re-embed.
  * incremental (--keep-index) — leave `.index` alone; apply() just records
    which files changed. After restart, run `reindex-incremental <txn>` to
    force just those files through `everos cascade sync <path>` (safe to
    call against a live daemon — it shares the same process-wide singletons).
    Cheaper, but only as narrow as the changed-file list.

Operations: RedactSpan (span substitution, leaves entry_count alone) and
DeleteEntry (whole `<!-- entry:ID -->` block removal, decrements the file's
frontmatter entry_count). Both work over md kinds + plugin relationship
caches; tar snapshot; append-only journal. Pure stdlib, Python 3.12.

CLI (via ``python -m app.modules.palimpsest``, see ``__main__.py``)
  ... plan     <plan.json>          # dry-run: print the diff, write nothing
  ... apply    <plan.json> [--yes] [--keep-index]
  ... verify   <plan.json>          # scan md + plugin caches for residue
  ... reindex-incremental <txn_id>  # post-restart: sync just the changed files
  ... rollback <txn_id>             # restore a transaction's snapshot
  ... journal                       # print the audit trail

Consumed as a library by ``backend/app/modules/episode_editor`` (WebUI batch
routes) — see docs/06-phase2-plan.md §2/§5. This engine is intentionally
independent of ``everos_gateway``'s md read/write (kind+user_id+date, single
file, optimistic-lock) — this module operates on Selector-scoped *sets* of
files for offline batch rewrites, a different grain for a different caller.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# kind -> md glob (relative to an owner directory). Extend here to add a data type.
KIND_GLOBS: dict[str, str] = {
    "episode": "episodes/episode-*.md",
    "atomic_fact": ".atomic_facts/atomic_fact-*.md",
    "foresight": ".foresights/foresight-*.md",
    "profile": "user.md",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    """Timestamp-prefixed (readable, roughly time-sortable) + random-suffixed
    (collision-proof) id. Two `apply()` calls within the same wall-clock
    second — entirely possible from scripted/WebUI-driven callers, not just
    a human at a terminal — must never produce the same txn id; a naive
    `f"txn_{int(time.time())}"` does exactly that and silently corrupts
    journal lookups (`_find_journal_receipt` would resolve to whichever
    same-second txn happens to be scanned first)."""
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def _jsonable(op: Any) -> dict[str, Any]:
    """asdict(op), with frozenset fields (DeleteEntry.entry_ids) coerced to a
    sorted list so the result is always JSON-serialisable."""
    d = asdict(op)
    for k, v in d.items():
        if isinstance(v, frozenset):
            d[k] = sorted(v)
    return d


# ───────────────────────── core data structures ─────────────────────────


@dataclass
class Layout:
    """On-disk geography. The only place that knows EverOS's directory shape."""

    everos_root: Path
    plugin_relationships: Path | None = None  # plugin_data/<plugin>/relationships
    app_id: str = "astrbot"
    project_id: str = "default_project"

    def owner_dir(self, owner: str) -> Path:
        return self.everos_root / self.app_id / self.project_id / "users" / owner

    @property
    def index_dir(self) -> Path:
        return self.everos_root / ".index"

    @property
    def work_dir(self) -> Path:
        return self.everos_root / ".palimpsest"

    @property
    def journal_path(self) -> Path:
        return self.work_dir / "journal.ndjson"

    @property
    def backups_dir(self) -> Path:
        return self.work_dir / "backups"

    @classmethod
    def from_dict(cls, d: dict) -> Layout:
        pr = d.get("plugin_relationships")
        return cls(
            everos_root=Path(d["everos_root"]),
            plugin_relationships=Path(pr) if pr else None,
            app_id=d.get("app_id", "astrbot"),
            project_id=d.get("project_id", "default_project"),
        )


@dataclass
class Selector:
    """Declarative locator for the records to touch."""

    owner_ids: list[str]
    kinds: list[str]
    include_plugin_caches: bool = True
    content_regex: str | None = None  # cheap pre-filter: skip files that never match

    @classmethod
    def from_dict(cls, d: dict) -> Selector:
        return cls(
            owner_ids=list(d["owner_ids"]),
            kinds=list(d["kinds"]),
            include_plugin_caches=d.get("include_plugin_caches", True),
            content_regex=d.get("content_regex"),
        )


@dataclass
class RedactSpan:
    """Operation: delete every match of `pattern`, substituting `replacement`.

    Leaves frontmatter alone (only touches record bodies) — the simplest
    operation, safe for any kind without an entry_count invariant to keep.
    """

    pattern: str
    replacement: str = ""
    ignore_case: bool = True

    @property
    def flags(self) -> int:
        return re.IGNORECASE if self.ignore_case else 0

    @classmethod
    def from_dict(cls, d: dict) -> RedactSpan:
        assert d.get("type") == "redact_span", f"unsupported operation: {d.get('type')!r}"
        return cls(
            pattern=d["pattern"],
            replacement=d.get("replacement", ""),
            ignore_case=d.get("ignore_case", True),
        )


@dataclass
class DeleteEntry:
    """Operation: remove one or more whole `<!-- entry:ID -->...<!-- /entry:ID -->`
    blocks by their bare entry_id (the id as it appears in the md marker —
    NOT the API's composite `{owner_id}_{entry_id}` form; callers strip the
    owner prefix before building the Selector/Operation).

    Also decrements the file's frontmatter `entry_count` by the number of
    blocks actually removed from that file — the one invariant RedactSpan
    doesn't need to maintain, because RedactSpan never changes entry count.
    """

    entry_ids: frozenset[str]

    @classmethod
    def from_dict(cls, d: dict) -> DeleteEntry:
        assert d.get("type") == "delete_entry", f"unsupported operation: {d.get('type')!r}"
        ids = d["entry_ids"]
        assert isinstance(ids, list) and ids, "entry_ids must be a non-empty list"
        return cls(entry_ids=frozenset(ids))


_ENTRY_COUNT_RE = re.compile(r"^(entry_count:\s*)(\d+)\s*$", re.MULTILINE)


def _entry_block_re(entry_id: str) -> re.Pattern[str]:
    """Match one whole entry block, plus the blank line that follows it
    (so deleting doesn't leave a stray empty line between neighbours)."""
    eid = re.escape(entry_id)
    return re.compile(
        rf"<!-- entry:{eid} -->.*?<!-- /entry:{eid} -->\n?",
        re.DOTALL,
    )


def _apply_delete_entry(text: str, entry_ids: frozenset[str]) -> tuple[str, int]:
    """Remove each matching entry block and decrement frontmatter entry_count
    by the number actually removed. Returns (new_text, n_removed)."""
    new = text
    removed = 0
    for eid in entry_ids:
        new2, n = _entry_block_re(eid).subn("", new)
        if n:
            new = new2
            removed += 1  # a block is removed at most once per well-formed file
    if removed:

        def _dec(m: re.Match[str]) -> str:
            return f"{m.group(1)}{max(0, int(m.group(2)) - removed)}"

        new = _ENTRY_COUNT_RE.sub(_dec, new, count=1)
    return new, removed


@dataclass
class FileChange:
    """A resolved, per-file change with before/after hashes (idempotency + drift guard)."""

    path: str
    kind: str
    owner_id: str
    pre_sha256: str
    post_sha256: str
    n_subs: int
    preview: list[str] = field(default_factory=list)


@dataclass
class Plan:
    plan_id: str
    selector: dict
    operation: dict
    changes: list[FileChange]
    skipped: list[dict] = field(default_factory=list)  # unreadable files (need elevated access)

    def is_empty(self) -> bool:
        return not self.changes

    def render(self) -> str:
        if not self.changes and not self.skipped:
            return "(no changes — target state already holds)"
        lines = [f"plan {self.plan_id}: {len(self.changes)} file(s) to rewrite\n"]
        for c in self.changes:
            lines.append(f"  [{c.kind}] {c.path}  (-{c.n_subs})")
            for pv in c.preview:
                lines.append(f"      {pv}")
        if self.skipped:
            lines.append(
                f"\n  ! {len(self.skipped)} file(s) skipped — unreadable, need elevated access:"
            )
            for s in self.skipped:
                lines.append(f"      [{s['kind']}] {s['path']}  ({s['reason']})")
        return "\n".join(lines)


def _preview(text: str, pattern: str, flags: int, ctx: int = 28, cap: int = 3) -> list[str]:
    """PII-safe: short windows around each match, not the whole record."""
    out: list[str] = []
    for m in re.finditer(pattern, text, flags):
        s, e = max(0, m.start() - ctx), min(len(text), m.end() + ctx)
        window = text[s:e].replace("\n", " ")
        out.append(f"...{window}...  -> drop [{m.group(0)}]")
        if len(out) >= cap:
            break
    return out


# Operation dispatch. Two implementations today (RedactSpan / DeleteEntry) —
# a free function keeps the branch explicit rather than reaching for a
# Protocol/ABC for a two-case match (karpathy: no premature abstraction).
Operation = RedactSpan | DeleteEntry


def _apply_op(op: Operation, text: str) -> tuple[str, int]:
    """Apply `op` to `text`. Returns (new_text, n_changes); n_changes==0 means
    no-op (target state already holds, or nothing in this file matched)."""
    if isinstance(op, RedactSpan):
        rx = re.compile(op.pattern, op.flags)
        return rx.subn(op.replacement, text)
    return _apply_delete_entry(text, op.entry_ids)


def _preview_op(op: Operation, text: str) -> list[str]:
    """PII-safe preview of what `op` would change in `text`."""
    if isinstance(op, RedactSpan):
        return _preview(text, op.pattern, op.flags)
    # DeleteEntry: header line only (subject/id), never full entry content.
    out: list[str] = []
    for eid in sorted(op.entry_ids):
        m = _entry_block_re(eid).search(text)
        if m:
            out.append(f"drop entry block [{eid}] ({len(m.group(0))} chars)")
    return out


# ───────────────────────────── the engine ──────────────────────────────


class Palimpsest:
    def __init__(self, layout: Layout, *, everos_bin: str = "everos") -> None:
        self.layout = layout
        # 真机坐实（生产验证）：`everos` 装在 venv 内
        # （~/everos/.venv/bin/everos），不在 systemd 服务的 PATH 里——裸命令
        # 会 subprocess FileNotFoundError。默认仍留裸名（兼容已把 venv 加进
        # PATH 的环境），同 everos_gateway.HTTPEverOSGateway 的 everos_bin 参数
        # 同一套解法，但这是两套独立实现（各自面向不同调用方，见模块头），
        # 配置项不共享，调用方（episode_editor）要把同一个值分别传给两边。
        self.everos_bin = everos_bin

    # -- scope resolution (Selector -> concrete files) --

    def _scope_files(self, sel: Selector):
        for owner in sel.owner_ids:
            od = self.layout.owner_dir(owner)
            for kind in sel.kinds:
                glob = KIND_GLOBS.get(kind)
                if not glob:
                    raise ValueError(f"unknown kind: {kind!r}")
                for p in sorted(od.glob(glob)):
                    yield owner, kind, p
            if sel.include_plugin_caches and self.layout.plugin_relationships:
                pc = self.layout.plugin_relationships / f"{owner}.md"
                if pc.exists():
                    yield owner, "plugin_relationship", pc

    # -- plan (pure, read-only) --

    def plan(self, sel: Selector, op: Operation) -> Plan:
        cre = re.compile(sel.content_regex, re.IGNORECASE) if sel.content_regex else None
        changes: list[FileChange] = []
        skipped: list[dict] = []
        for owner, kind, p in self._scope_files(sel):
            try:
                text = p.read_text(encoding="utf-8")
            except (PermissionError, OSError) as e:
                skipped.append({"path": str(p), "kind": kind, "reason": type(e).__name__})
                continue
            if cre and not cre.search(text):
                continue
            new, n = _apply_op(op, text)
            if n == 0 or new == text:
                continue
            changes.append(
                FileChange(
                    path=str(p),
                    kind=kind,
                    owner_id=owner,
                    pre_sha256=_sha(text),
                    post_sha256=_sha(new),
                    n_subs=n,
                    preview=_preview_op(op, text),
                )
            )
        pid = "plan_" + _sha(json.dumps([asdict(c) for c in changes], sort_keys=True))[:12]
        return Plan(pid, asdict(sel), _jsonable(op), changes, skipped)

    # -- apply (atomic, reversible; requires service stopped) --

    def apply(
        self,
        sel: Selector,
        op: Operation,
        *,
        yes: bool = False,
        drop_index: bool = True,
        actor: str = "ops",
    ) -> dict:
        plan = self.plan(sel, op)
        if plan.is_empty():
            print(plan.render())
            return {"status": "noop"}
        print(plan.render())
        if not yes:
            if input("\napply these changes? [y/N] ").strip().lower() != "y":
                return {"status": "aborted"}
        self._guard_stopped()
        txn = _new_id("txn")
        backup = self._snapshot(sel, txn)
        try:
            for c in plan.changes:
                p = Path(c.path)
                cur = p.read_text(encoding="utf-8")
                cur_h = _sha(cur)
                if cur_h == c.post_sha256:
                    continue  # idempotent: already in target state
                if cur_h != c.pre_sha256:
                    raise RuntimeError(f"drift on {p}: precondition hash mismatch, refusing")
                new, _ = _apply_op(op, cur)
                tmp = p.with_suffix(p.suffix + ".palimpsest.tmp")
                tmp.write_text(new, encoding="utf-8")
                tmp.replace(p)  # atomic per-file swap
            if drop_index and self.layout.index_dir.exists():
                shutil.rmtree(self.layout.index_dir)
        except Exception as e:  # noqa: BLE001 — any failure -> full restore
            self._restore(backup)
            self._journal(
                {
                    "txn": txn,
                    "ts": int(time.time()),
                    "actor": actor,
                    "plan": plan.plan_id,
                    "status": "rolled_back",
                    "error": str(e),
                }
            )
            raise
        # Reindex is always a two-step affair with the service restart in
        # between: `apply` (this method) runs during the stopped window and
        # only decides *what* the next start should rebuild. Full mode drops
        # `.index` above (cold rebuild picks up every md file). Incremental
        # mode leaves `.index` alone and instead records exactly which paths
        # changed, so `reindex-incremental` can force-enqueue just those
        # files via `everos cascade sync <path>` once the service is back up
        # — no code here ever calls `cascade sync` itself, since that
        # requires the now-restarted daemon's live sqlite/lancedb singletons.
        changed_paths = [c.path for c in plan.changes]
        receipt = {
            "txn": txn,
            "ts": int(time.time()),
            "actor": actor,
            "plan": plan.plan_id,
            "status": "applied",
            "backup": str(backup),
            "reindex": "index_dropped" if drop_index else "incremental_pending",
            "reindex_paths": [] if drop_index else changed_paths,
            "changes": [
                {
                    "path": c.path,
                    "kind": c.kind,
                    "owner": c.owner_id,
                    "n": c.n_subs,
                    "pre": c.pre_sha256[:12],
                    "post": c.post_sha256[:12],
                }
                for c in plan.changes
            ],
        }
        self._journal(receipt)
        if drop_index:
            print(
                "\n.index dropped — RESTART REQUIRED for cold rebuild: sudo systemctl start everos"
            )
        else:
            paths = "\n  ".join(changed_paths)
            print(
                "\nRESTART REQUIRED, then run reindex-incremental for this txn:\n"
                "  sudo systemctl start everos\n"
                f"  python3 -m app.modules.palimpsest reindex-incremental {txn}"
                " --everos-root <path>\n"
                f"(will force-enqueue {len(changed_paths)} changed file(s):\n  {paths})"
            )
        return receipt

    def reindex_incremental(self, txn: str) -> dict:
        """Force cascade to re-embed exactly the files a prior incremental
        apply changed, via `everos cascade sync <path>` per file.

        Must run AFTER the service has been restarted — `cascade sync`
        needs the live sqlite/lancedb singletons, which only exist once
        the daemon (or this CLI, standing them up itself) is running. Safe
        to call with the service active: the CLI is designed to piggyback
        on the same process-wide singletons rather than open a second,
        conflicting connection (see `everos cascade --help` design note).
        """
        rec = self._find_journal_receipt(txn)
        if rec is None:
            raise FileNotFoundError(f"no applied txn {txn} in journal")
        if rec.get("reindex") != "incremental_pending":
            raise ValueError(
                f"txn {txn} is not an incremental-pending apply (reindex={rec.get('reindex')!r})"
            )
        results = []
        for path in rec.get("reindex_paths", []):
            # 生产真机验证坐实：`everos cascade sync <path>` 自己会对
            # 传入的 path 做 `.expanduser().resolve()` 再算相对 memory root 的路径
            # （见 everos CLI _resolve_relative 源码）——传一个"已经算好的相对路径
            # 字符串"进去，会被当成相对当前工作目录解析，resolve 出来的绝对路径跟
            # 真正的 memory root 完全不沾边，报 "not under memory root"。必须传
            # 绝对路径，让 CLI 自己算相对路径，不能越权代它做这一步。
            rel = self._relative_to_root(Path(path))
            proc = subprocess.run(
                [self.everos_bin, "cascade", "sync", path],
                capture_output=True,
                text=True,
            )
            results.append(
                {
                    "path": path,
                    "rel": rel,
                    "ok": proc.returncode == 0,
                    "stdout": proc.stdout.strip(),
                    "stderr": proc.stderr.strip(),
                }
            )
        ok = all(r["ok"] for r in results)
        out = {"txn": txn, "synced": results, "ok": ok}
        self._journal(
            {
                "txn": _new_id("reindex"),
                "ts": int(time.time()),
                "status": "reindex_incremental",
                "of_txn": txn,
                "ok": ok,
                "n_files": len(results),
            }
        )
        return out

    def _relative_to_root(self, path: Path) -> str:
        """`cascade sync` wants a path relative to the memory root (the same
        convention md_change_state stores), not an absolute filesystem path."""
        return path.resolve().relative_to(self.layout.everos_root.resolve()).as_posix()

    def _find_journal_receipt(self, txn: str) -> dict | None:
        if not self.layout.journal_path.exists():
            return None
        for line in self.layout.journal_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("txn") == txn and rec.get("status") == "applied":
                return rec
        return None

    # -- verify (read-only; residue scan across md u plugin caches) --

    def verify(self, sel: Selector, term: str) -> dict:
        """RedactSpan-style verification: `term` (a regex) must no longer
        appear anywhere in scope. Use `verify_deleted` for DeleteEntry."""
        rx = re.compile(term, re.IGNORECASE)
        hits: list[str] = []
        skipped: list[str] = []
        for _owner, _kind, p in self._scope_files(sel):
            try:
                text = p.read_text(encoding="utf-8")
            except (PermissionError, OSError):
                skipped.append(str(p))
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{p}:{i}")
        return {"term": term, "residual_hits": hits, "skipped": skipped, "clean": not hits}

    def verify_deleted(self, sel: Selector, entry_ids: frozenset[str]) -> dict:
        """DeleteEntry-style verification: none of `entry_ids`' block markers
        (`<!-- entry:ID -->`) may still be present anywhere in scope."""
        hits: list[str] = []
        skipped: list[str] = []
        for _owner, _kind, p in self._scope_files(sel):
            try:
                text = p.read_text(encoding="utf-8")
            except (PermissionError, OSError):
                skipped.append(str(p))
                continue
            for eid in entry_ids:
                if _entry_block_re(eid).search(text):
                    hits.append(f"{p}: entry {eid} still present")
        return {
            "entry_ids": sorted(entry_ids),
            "residual_hits": hits,
            "skipped": skipped,
            "clean": not hits,
        }

    # -- rollback --

    def rollback(self, txn: str) -> dict:
        backup = self.layout.backups_dir / f"{txn}.tar.gz"
        if not backup.exists():
            raise FileNotFoundError(f"no snapshot for {txn}: {backup}")
        self._guard_stopped()
        self._restore(backup)
        rec = {
            "txn": _new_id("rollback"),
            "ts": int(time.time()),
            "status": "rolled_back",
            "rollback_of": txn,
            "from": str(backup),
        }
        self._journal(rec)
        print(f"restored {txn}; RESTART REQUIRED: sudo systemctl start everos")
        return rec

    # ----------------------------- internals -----------------------------

    def _guard_stopped(self) -> None:
        # `systemctl` absent (non-systemd host, or Windows dev machine where this
        # method is reachable via the WebUI route even though the route itself
        # already checked EverOSGateway.is_everos_stopped() — see that method's
        # sibling handling of the same FileNotFoundError) must not crash with a
        # raw traceback; conservatively treat "can't tell" as "still running".
        try:
            r = subprocess.run(["systemctl", "is-active", "everos"], capture_output=True, text=True)
        except (OSError, FileNotFoundError) as e:
            raise RuntimeError(
                "cannot determine everos status (systemctl unavailable): "
                f"{e}. Stop it first: sudo systemctl stop everos"
            ) from e
        if r.stdout.strip() == "active":
            raise RuntimeError("everos is active — stop it first: sudo systemctl stop everos")

    def _snapshot(self, sel: Selector, txn: str) -> Path:
        self.layout.backups_dir.mkdir(parents=True, exist_ok=True)
        out = self.layout.backups_dir / f"{txn}.tar.gz"

        def _safe_add(tar, path, arcname):
            try:
                tar.add(path, arcname=arcname)
            except (PermissionError, OSError) as e:
                print(f"  snapshot: skip {path} ({type(e).__name__})", file=sys.stderr)

        with tarfile.open(out, "w:gz") as tar:
            for owner in sel.owner_ids:
                od = self.layout.owner_dir(owner)
                if od.exists():
                    _safe_add(tar, od, f"owner/{owner}")
                if self.layout.plugin_relationships:
                    pc = self.layout.plugin_relationships / f"{owner}.md"
                    if pc.exists():
                        _safe_add(tar, pc, f"plugin_rel/{owner}.md")
            if self.layout.index_dir.exists():
                _safe_add(tar, self.layout.index_dir, "index")
        return out

    def _restore(self, backup: Path) -> None:
        tmp = self.layout.work_dir / "restore_tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)
        with tarfile.open(backup, "r:gz") as tar:
            tar.extractall(tmp, filter="data")
        # owners
        for od in (tmp / "owner").glob("*"):
            dest = self.layout.owner_dir(od.name)
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(od), str(dest))
        # plugin relationship caches
        pr = tmp / "plugin_rel"
        if pr.exists() and self.layout.plugin_relationships:
            for f in pr.glob("*.md"):
                shutil.copy2(f, self.layout.plugin_relationships / f.name)
        # index
        idx = tmp / "index"
        if idx.exists():
            if self.layout.index_dir.exists():
                shutil.rmtree(self.layout.index_dir)
            shutil.move(str(idx), str(self.layout.index_dir))
        shutil.rmtree(tmp, ignore_errors=True)

    def _journal(self, rec: dict) -> None:
        self.layout.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.layout.journal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ───────────────────────────────── CLI ─────────────────────────────────


_OPERATION_TYPES: dict[str, type] = {
    "redact_span": RedactSpan,
    "delete_entry": DeleteEntry,
}


def _load_plan_file(path: str):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    layout = Layout.from_dict(spec["layout"])
    selector = Selector.from_dict(spec["selector"])
    op_type = spec["operation"].get("type")
    op_cls = _OPERATION_TYPES.get(op_type)
    if op_cls is None:
        raise ValueError(f"unsupported operation type: {op_type!r}")
    operation = op_cls.from_dict(spec["operation"])
    verify_term = spec.get("verify_term")
    return Palimpsest(layout), selector, operation, verify_term


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="palimpsest", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "apply", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("plan_file")
        if name == "apply":
            sp.add_argument("--yes", action="store_true")
            sp.add_argument(
                "--keep-index",
                action="store_true",
                help="do not drop .index (use incremental rescan on restart)",
            )
    rb = sub.add_parser("rollback")
    rb.add_argument("txn")
    rb.add_argument("--everos-root", default="~")
    ri = sub.add_parser("reindex-incremental")
    ri.add_argument("txn")
    ri.add_argument("--everos-root", default="~")
    jn = sub.add_parser("journal")
    jn.add_argument("--everos-root", default="~")
    args = ap.parse_args(argv)

    if args.cmd in ("plan", "apply", "verify"):
        eng, sel, op, term = _load_plan_file(args.plan_file)
        if args.cmd == "plan":
            print(eng.plan(sel, op).render())
        elif args.cmd == "apply":
            r = eng.apply(sel, op, yes=args.yes, drop_index=not args.keep_index)
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:  # verify
            if isinstance(op, DeleteEntry):
                r = eng.verify_deleted(sel, op.entry_ids)
            elif term:
                r = eng.verify(sel, term)
            else:
                print("plan file has no verify_term (required for redact_span)", file=sys.stderr)
                return 2
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0 if r["clean"] else 1
        return 0

    root = Path(args.everos_root).expanduser() / ".everos"
    layout = Layout(everos_root=root)
    eng = Palimpsest(layout)
    if args.cmd == "rollback":
        print(json.dumps(eng.rollback(args.txn), ensure_ascii=False, indent=2))
    elif args.cmd == "reindex-incremental":
        r = eng.reindex_incremental(args.txn)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    elif args.cmd == "journal":
        if layout.journal_path.exists():
            print(layout.journal_path.read_text(encoding="utf-8"), end="")
        else:
            print("(empty journal)")
    return 0
