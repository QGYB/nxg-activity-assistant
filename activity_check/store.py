# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .campus import CAMPUSES, campuses_for_address


class IdentityMismatch(ValueError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0, interval INTEGER NOT NULL DEFAULT 10,
                    status TEXT NOT NULL DEFAULT '未登录', checked TEXT NOT NULL DEFAULT '',
                    term TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'observe', campus TEXT NOT NULL DEFAULT '',
                    baseline_ready INTEGER NOT NULL DEFAULT 0, watch_generation INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS activities (
                    account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    id TEXT NOT NULL, payload TEXT NOT NULL, selected INTEGER NOT NULL DEFAULT 0,
                    is_new INTEGER NOT NULL DEFAULT 0, auto_target INTEGER NOT NULL DEFAULT 0,
                    excluded INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(account,id));
                CREATE TABLE IF NOT EXISTS attempts (
                    account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    activity TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL,
                    updated TEXT NOT NULL, PRIMARY KEY(account,activity));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    time TEXT NOT NULL, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cancellations (
                    account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    activity TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL,
                    updated TEXT NOT NULL, PRIMARY KEY(account,activity));
                CREATE TABLE IF NOT EXISTS wechat_notifications (
                    account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    activity TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL,
                    detail TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
                    PRIMARY KEY(account,activity));
                CREATE TABLE IF NOT EXISTS seen_activities (
                    account TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    activity TEXT NOT NULL, first_seen TEXT NOT NULL, PRIMARY KEY(account,activity));
                CREATE UNIQUE INDEX IF NOT EXISTS one_student_account ON accounts(student_id) WHERE student_id<>'';
            """)
            additions = {
                "accounts": {
                    "mode": "TEXT NOT NULL DEFAULT 'observe'",
                    "baseline_ready": "INTEGER NOT NULL DEFAULT 0",
                    "watch_generation": "INTEGER NOT NULL DEFAULT 0",
                    "campus": "TEXT NOT NULL DEFAULT ''",
                },
                "activities": {
                    "is_new": "INTEGER NOT NULL DEFAULT 0",
                    "auto_target": "INTEGER NOT NULL DEFAULT 0",
                    "excluded": "INTEGER NOT NULL DEFAULT 0",
                },
            }
            for table, columns in additions.items():
                existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                for column, definition in columns.items():
                    if column not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_account(self, name):
        account = uuid4().hex
        with self.connection() as db:
            db.execute(
                "INSERT INTO accounts(id,name,interval) VALUES (?,?,?)",
                (account, str(name).strip()[:60] or "我的账号", 10),
            )
        return account

    def accounts(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM accounts ORDER BY rowid")]

    def rename_account(self, account, name):
        name = str(name).strip()
        if not name or len(name) > 60:
            raise ValueError("账号备注请输入 1–60 个字符。")
        with self.connection() as db:
            db.execute("UPDATE accounts SET name=? WHERE id=?", (name, account))

    def account(self, account):
        with self.connection() as db:
            row = db.execute("SELECT * FROM accounts WHERE id=?", (account,)).fetchone()
            return dict(row) if row else None

    def bind_identity(self, account, identity):
        identity = str(identity)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT student_id FROM accounts WHERE id=?", (account,)).fetchone()
            if row is None or (row[0] and row[0] != identity):
                raise IdentityMismatch("登录账号与此卡片绑定的人不一致，请新增一个个人 Cookie 账号。")
            duplicate = db.execute(
                "SELECT 1 FROM accounts WHERE student_id=? AND id<>?", (identity, account)
            ).fetchone()
            if duplicate:
                raise IdentityMismatch("这个学生账号已保存，请更新原来的个人 Cookie，避免重复报名。")
            db.execute("UPDATE accounts SET student_id=? WHERE id=?", (identity, account))

    def configure(self, account, *, enabled=None, interval=None, status=None, term=None):
        updates, values = [], []
        for key, value in {"enabled": enabled, "interval": interval, "status": status, "term": term}.items():
            if value is not None:
                if key == "interval":
                    value = max(5, min(3600, int(value)))
                updates.append(f"{key}=?")
                values.append(value)
        if status is not None:
            updates.append("checked=?")
            values.append(stamp())
        if updates:
            with self.connection() as db:
                db.execute("UPDATE accounts SET " + ",".join(updates) + " WHERE id=?", values + [account])

    @staticmethod
    def _reset_tracking(db, account):
        db.execute("DELETE FROM seen_activities WHERE account=?", (account,))
        db.execute("UPDATE activities SET is_new=0,auto_target=0 WHERE account=?", (account,))
        db.execute("UPDATE accounts SET baseline_ready=0,watch_generation=watch_generation+1 WHERE id=?", (account,))

    def set_mode(self, account, mode):
        if mode not in {"observe", "auto"}:
            raise ValueError("请选择有效的监测模式。")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT mode FROM accounts WHERE id=?", (account,)).fetchone()
            if not row or row[0] == mode:
                return False
            db.execute("UPDATE accounts SET mode=?,enabled=0 WHERE id=?", (mode, account))
            self._reset_tracking(db, account)
            return True

    def start_monitor(self, account, interval):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reset_tracking(db, account)
            db.execute(
                "UPDATE accounts SET enabled=1,interval=? WHERE id=?", (max(5, min(3600, int(interval))), account)
            )

    def change_term(self, account, term):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE accounts SET term=?,enabled=0 WHERE id=?", (term, account))
            self._reset_tracking(db, account)

    def set_campus(self, account, campus):
        if campus not in ("", *CAMPUSES):
            raise ValueError("请选择全部校区、回龙或钱湖。")
        with self.connection() as db:
            db.execute(
                "UPDATE accounts SET campus=?,watch_generation=watch_generation+1 WHERE id=? AND campus<>?",
                (campus, account, campus),
            )

    def save_activities(self, account, rows):
        with self.connection() as db:
            self._save_rows(db, account, rows)

    @staticmethod
    def _save_rows(db, account, rows):
        for row in rows:
            if row.get("id") is None or str(row["id"]) == "":
                continue
            identity = str(row["id"])
            previous = db.execute(
                "SELECT payload FROM activities WHERE account=? AND id=?", (account, identity)
            ).fetchone()
            merged = (json.loads(previous[0]) if previous else {}) | row
            merged["_campuses"] = campuses_for_address(merged.get("address"))
            db.execute(
                "INSERT INTO activities(account,id,payload) VALUES (?,?,?) ON CONFLICT(account,id) DO UPDATE SET payload=excluded.payload",
                (account, identity, json.dumps(merged, ensure_ascii=False)),
            )

    def record_snapshot(self, account, rows, *, generation=None):
        rows = list(rows)
        identities = list(dict.fromkeys(str(row["id"]) for row in rows if row.get("id") is not None and str(row["id"])))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._save_rows(db, account, rows)
            owner = db.execute(
                "SELECT enabled,mode,baseline_ready,watch_generation FROM accounts WHERE id=?", (account,)
            ).fetchone()
            if (
                not owner
                or not owner["enabled"]
                or (generation is not None and generation != owner["watch_generation"])
            ):
                return []
            seen = {r[0] for r in db.execute("SELECT activity FROM seen_activities WHERE account=?", (account,))}
            fresh = [identity for identity in identities if identity not in seen] if owner["baseline_ready"] else []
            db.executemany(
                "INSERT OR IGNORE INTO seen_activities VALUES (?,?,?)",
                [(account, identity, stamp()) for identity in identities],
            )
            db.execute("UPDATE accounts SET baseline_ready=1 WHERE id=?", (account,))
            automatic = owner["mode"] == "auto"
            if automatic:
                db.executemany(
                    "UPDATE activities SET auto_target=1 WHERE account=? AND id=?",
                    [(account, identity) for identity in identities],
                )
            for identity in fresh:
                db.execute(
                    "UPDATE activities SET is_new=1 WHERE account=? AND id=?",
                    (account, identity),
                )
            return fresh

    def activities(self, account):
        with self.connection() as db:
            rows = db.execute(
                "SELECT a.*,t.status AS attempt_status,t.message AS attempt_message,c.status AS cancel_status,c.message AS cancel_message FROM activities a LEFT JOIN attempts t ON t.account=a.account AND t.activity=a.id LEFT JOIN cancellations c ON c.account=a.account AND c.activity=a.id WHERE a.account=?",
                (account,),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "raw": json.loads(r["payload"]),
                    "excluded": bool(r["excluded"]),
                    "is_new": bool(r["is_new"]),
                    "auto_target": bool(r["auto_target"]),
                    "attempt_status": r["attempt_status"],
                    "attempt_message": r["attempt_message"] or "",
                    "cancel_status": r["cancel_status"],
                    "cancel_message": r["cancel_message"] or "",
                }
                for r in rows
            ]

    def exclude(self, account, activity, value):
        with self.connection() as db:
            db.execute(
                "UPDATE activities SET excluded=? WHERE account=? AND id=?", (bool(value), account, str(activity))
            )

    def is_excluded(self, account, activity):
        with self.connection() as db:
            row = db.execute(
                "SELECT excluded FROM activities WHERE account=? AND id=?", (account, str(activity))
            ).fetchone()
            return bool(row and row[0])

    def is_auto_target(self, account, activity):
        with self.connection() as db:
            row = db.execute(
                "SELECT auto_target FROM activities WHERE account=? AND id=?", (account, str(activity))
            ).fetchone()
            return bool(row and row[0])

    def claim(self, account, activity):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            permitted = db.execute(
                "SELECT 1 FROM activities a JOIN accounts p ON p.id=a.account WHERE a.account=? AND a.id=? AND a.excluded=0 AND a.auto_target=1 AND p.enabled=1 AND p.mode='auto' AND p.baseline_ready=1 AND (p.campus='' OR EXISTS (SELECT 1 FROM json_each(a.payload,'$._campuses') WHERE value=p.campus)) AND NOT EXISTS (SELECT 1 FROM cancellations c WHERE c.account=a.account AND c.activity=a.id AND c.status IN ('cancelling','cancelled','cancel_uncertain'))",
                (account, str(activity)),
            ).fetchone()
            if not permitted:
                return False
            result = db.execute(
                "INSERT OR IGNORE INTO attempts VALUES (?,?,?,?,?)",
                (account, str(activity), "submitting", "请求发送前已登记", stamp()),
            )
            return result.rowcount == 1

    def cancellation(self, account, activity):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM cancellations WHERE account=? AND activity=?", (account, str(activity))
            ).fetchone()
            return dict(row) if row else None

    def claim_cancel(self, account, activity):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM cancellations WHERE account=? AND activity=?", (account, str(activity))
            ).fetchone()
            if row and row[0] not in {"cancel_rejected", "not_registered"}:
                return False
            if not db.execute("SELECT 1 FROM activities WHERE account=? AND id=?", (account, str(activity))).fetchone():
                return False
            db.execute("UPDATE activities SET excluded=1 WHERE account=? AND id=?", (account, str(activity)))
            db.execute(
                "INSERT INTO cancellations VALUES (?,?,?,?,?) ON CONFLICT(account,activity) DO UPDATE SET status=excluded.status,message=excluded.message,updated=excluded.updated",
                (account, str(activity), "cancelling", "取消请求发送前已登记", stamp()),
            )
            return True

    def finish_cancel(self, account, activity, status, message):
        with self.connection() as db:
            db.execute(
                "INSERT INTO cancellations VALUES (?,?,?,?,?) ON CONFLICT(account,activity) DO UPDATE SET status=excluded.status,message=excluded.message,updated=excluded.updated",
                (account, str(activity), status, message[:500], stamp()),
            )

    def attempt(self, account, activity):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM attempts WHERE account=? AND activity=?", (account, str(activity))
            ).fetchone()
            return dict(row) if row else None

    def finish(self, account, activity, status, message, *, notification=None):
        with self.connection() as db:
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?) ON CONFLICT(account,activity) DO UPDATE SET status=excluded.status,message=excluded.message,updated=excluded.updated",
                (account, str(activity), status, message[:500], stamp()),
            )
            if status == "success" and notification:
                db.execute(
                    "INSERT OR IGNORE INTO wechat_notifications VALUES (?,?,?,?,?,?,?)",
                    (account, str(activity), notification[:1000], "queued", "", stamp(), stamp()),
                )

    def notification(self, account, activity):
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM wechat_notifications WHERE account=? AND activity=?", (account, str(activity))
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _next_notification(db):
        db.execute(
            "UPDATE wechat_notifications SET state='skipped',detail='活动已取消或取消结果待确认',updated=? WHERE state='queued' AND EXISTS (SELECT 1 FROM cancellations c WHERE c.account=wechat_notifications.account AND c.activity=wechat_notifications.activity AND c.status IN ('cancelling','cancelled','cancel_uncertain','not_registered'))",
            (stamp(),),
        )
        return db.execute(
            "SELECT n.* FROM wechat_notifications n JOIN attempts a ON a.account=n.account AND a.activity=n.activity WHERE n.state='queued' AND a.status='success' ORDER BY n.created,n.rowid LIMIT 1"
        ).fetchone()

    def next_notification(self):
        with self.connection() as db:
            row = self._next_notification(db)
            return dict(row) if row else None

    def claim_notification(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._next_notification(db)
            if not row:
                return None
            db.execute(
                "UPDATE wechat_notifications SET state='sending',updated=? WHERE account=? AND activity=?",
                (stamp(), row["account"], row["activity"]),
            )
            return dict(row)

    def finish_notification(self, account, activity, state, detail):
        with self.connection() as db:
            db.execute(
                "UPDATE wechat_notifications SET state=?,detail=?,updated=? WHERE account=? AND activity=?",
                (state, detail[:500], stamp(), account, str(activity)),
            )

    def reset_rejected(self, account, activity):
        with self.connection() as db:
            return (
                db.execute(
                    "DELETE FROM attempts WHERE account=? AND activity=? AND status IN ('blocked','rejected')",
                    (account, str(activity)),
                ).rowcount
                > 0
            )

    def recover_interrupted(self):
        with self.connection() as db:
            db.execute(
                "UPDATE attempts SET status='uncertain',message='上次提交被中断，请在学校网站确认结果',updated=? WHERE status='submitting'",
                (stamp(),),
            )
            db.execute("UPDATE accounts SET enabled=0,status=CASE WHEN student_id<>'' THEN '未确认' ELSE '未登录' END")
            db.execute(
                "UPDATE cancellations SET status='cancel_uncertain',message='上次取消请求被中断，请在学校网站确认结果',updated=? WHERE status='cancelling'",
                (stamp(),),
            )
            db.execute(
                "UPDATE wechat_notifications SET state='uncertain',detail='上次微信发送被中断，请到文件传输助手核对；不会自动重发',updated=? WHERE state='sending'",
                (stamp(),),
            )

    def log(self, account, message):
        with self.connection() as db:
            db.execute(
                "INSERT INTO events(account,time,message) VALUES (?,?,?)", (account, stamp(), str(message)[:500])
            )
            db.execute("DELETE FROM events WHERE seq NOT IN (SELECT seq FROM events ORDER BY seq DESC LIMIT 1000)")

    def events(self, account):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT time,message FROM events WHERE account=? ORDER BY seq DESC LIMIT 100", (account,)
                )
            ]

    def remove(self, account):
        with self.connection() as db:
            db.execute("DELETE FROM accounts WHERE id=?", (account,))
