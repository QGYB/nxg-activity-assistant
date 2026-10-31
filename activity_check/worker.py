# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 QGYB and contributors

"""One owner thread for login browsers, network requests and registration scheduling."""

import hashlib
import json
import queue
import threading
import time
import traceback
from urllib.parse import quote, urlsplit

from .engine import Engine
from .models import LIST_URL, LOGIN_URL, ORIGIN, Activity, parse_time
from .session import SessionError, import_session, login_page_location, school_domain, storage_key_names
from .site import SiteClient, SiteError, LoginExpired
from .store import IdentityMismatch
from .vault import Vault
from .campus import matches_campus


class WorkerUnavailable(RuntimeError):
    pass


class Worker(threading.Thread):
    def __init__(self, store, vault):
        super().__init__(name="activity-worker", daemon=False)
        self.store, self.vault = store, vault
        self.pending_vault = Vault(vault.directory.parent / "pending-sessions")
        self.commands, self.events = queue.Queue(), queue.Queue()
        self.stopping = threading.Event()
        self.clients, self.next_poll, self.login_windows = {}, {}, {}
        self.next_detail_at = 0
        self.detail_retry = {}
        self.playwright = self.browser = None
        self.engine = Engine(store)

    def send(self, kind, account, **payload):
        if kind == "import":
            session = import_session(payload["raw"], payload.get("token", ""), payload.get("student_id", ""))
            self.pending_vault.save(account, session.to_json())
            self.store.configure(account, enabled=False, status="等待登录校验")
            payload = {}
        if self.ident is not None and not self.is_alive():
            raise WorkerUnavailable("后台已停止。已输入的登录数据会加密保留，重开助手后继续校验。")
        self.commands.put((kind, account, payload))

    def emit(self, kind, account, **payload):
        self.events.put((kind, account, payload))

    def stop(self):
        self.stopping.set()

    def run(self):
        try:
            for account in self.store.accounts():
                if self.pending_vault.path(account["id"]).exists():
                    self.commands.put(("import", account["id"], {}))
                elif self.vault.path(account["id"]).exists():
                    self.commands.put(("refresh", account["id"], {}))
            while not self.stopping.is_set():
                try:
                    kind, account, payload = self.commands.get(timeout=0.25)
                    self._dispatch_command(kind, account, payload)
                except queue.Empty:
                    pass
                self._check_login_windows()
                for account in self.store.accounts():
                    if self.stopping.is_set():
                        break
                    if account["enabled"] and time.monotonic() >= self.next_poll.get(account["id"], 0):
                        self._refresh(account["id"], execute=True)
                self._hydrate_next_detail()
        except Exception as error:
            message = "后台处理已停止，监控已暂停。登录输入已加密保留，请重开助手继续检查。"
            frames = traceback.extract_tb(error.__traceback__)
            report = (
                type(error).__name__
                + "\n"
                + "\n".join(f"{frame.filename}:{frame.lineno} {frame.name}" for frame in frames)
            )
            try:
                (self.vault.directory.parent / "worker-error.log").write_text(report, encoding="utf-8")
                for owner in self.store.accounts():
                    self.store.configure(owner["id"], enabled=False, status="后台已停止")
                    self.store.log(owner["id"], message)
            finally:
                self.emit("worker_failed", None, message=message)
        finally:
            for data in list(self.login_windows.values()):
                try:
                    data["context"].close()
                except Exception:
                    pass
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()

    def _dispatch_command(self, kind, account, payload):
        try:
            if kind == "import":
                if not self.pending_vault.path(account).exists():
                    return
                self.store.configure(account, enabled=False, status="正在检查登录")
                session = import_session(self.pending_vault.load(account))
                self._accept(account, session)
            elif kind == "login":
                self._open_browser(account)
            elif kind == "open":
                self._open_browser(account, activity=payload.get("activity"), save=False)
            elif kind == "refresh":
                owner = self.store.account(account)
                self._refresh(account, include_ended=True, execute=bool(owner and owner["enabled"]))
            elif kind == "monitor":
                self.next_poll[account] = 0
            elif kind == "cancel":
                self.engine.cancel(account, payload["activity"], self._client(account))
                self.emit("changed", account)
            elif kind == "remove":
                data = self.login_windows.pop(account, None)
                if data:
                    data["context"].close()
                self.clients.pop(account, None)
                self.vault.remove(account)
                self.pending_vault.remove(account)
                self.store.remove(account)
                self.emit("changed", account)
        except (SessionError, IdentityMismatch, SiteError, OSError) as error:
            self._error(account, error)
        except Exception:
            self._error(account, RuntimeError("操作未完成，请重试或重新打开网页登录。"))

    def _client(self, account):
        if account not in self.clients:
            session = import_session(self.vault.load(account))
            owner = self.store.account(account)
            if not owner or owner["student_id"] != session.student_id:
                raise IdentityMismatch("保存的登录态与账号不一致，请重新导入。")
            self.clients[account] = SiteClient(session)
        return self.clients[account]

    def _accept(self, account, session):
        existing = self.store.account(account)
        if not existing:
            return
        if existing["student_id"] and existing["student_id"] != session.student_id:
            raise IdentityMismatch("该登录态属于另一账号，请新增个人 Cookie。")
        client = SiteClient(session)
        terms = client.terms()
        term = existing["term"] or self._current_term(terms)
        rows = client.activities(term, include_ended=True)
        self.store.bind_identity(account, session.student_id)
        self.vault.save(account, session.to_json())
        self.clients[account] = client
        self.store.configure(account, term=term, status="已登录")
        self.store.save_activities(account, [row | {"_term": term} for row in rows], from_list=True)
        self.store.log(account, "已验证并加密保存登录态，活动列表已更新。")
        if self.pending_vault.path(account).exists() and self.pending_vault.load(account) == session.to_json():
            self.pending_vault.remove(account)
        self.emit("terms", account, terms=terms)
        self.emit("changed", account)
        self.next_poll[account] = time.monotonic() + existing["interval"]

    @staticmethod
    def _current_term(terms):
        return str(next((item.get("school_term", "") for item in terms if str(item.get("current")) == "1"), ""))

    def _refresh(self, account, *, include_ended=False, execute=False):
        owner = self.store.account(account)
        if not owner:
            return
        self.emit("busy", account)
        try:
            client = self._client(account)
            terms = client.terms()
            term = owner["term"] or self._current_term(terms)
            self.emit("terms", account, terms=terms)
            rows = client.activities(term, include_ended=include_ended or (execute and not owner["baseline_ready"]))
            visible = {str(row["id"]): row | {"_term": term, "_missing": False} for row in rows}
            for previous in self.store.activities(account):
                if previous["id"] not in visible and previous["raw"].get("_term", term) == term:
                    status = Activity.from_row(previous["raw"]).eligibility()
                    if status in {"ready", "pending", "full", "unknown"}:
                        self.store.save_activities(account, [previous["raw"] | {"_missing": True}])
            if execute:
                discovered = self.store.record_snapshot(
                    account, list(visible.values()), generation=owner["watch_generation"]
                )
                current = self.store.account(account)
                if (
                    discovered
                    and current
                    and current["enabled"]
                    and current["watch_generation"] == owner["watch_generation"]
                ):
                    mode = current["mode"]
                    message = f"发现 {len(discovered)} 条新活动（{current['campus'] or '全部校区'}），" + (
                        "已加入自动报名检查。" if mode == "auto" else "仅提示，不提交报名。"
                    )
                    self.store.log(account, message)
                    self.emit(
                        "new_activity",
                        account,
                        message=message,
                        count=len(discovered),
                        watch_generation=owner["watch_generation"],
                    )
            else:
                self.store.save_activities(account, list(visible.values()), from_list=True)
            self.store.configure(account, status="已登录")
            current = self.store.account(account)
            if execute and current and current["enabled"] and current["watch_generation"] == owner["watch_generation"]:
                targets = [
                    r
                    for r in self.store.activities(account)
                    if not r["excluded"]
                    and r["auto_target"]
                    and (r["attempt_status"] == "uncertain" or matches_campus(r["raw"], current["campus"]))
                ]
                # Include existing and new activities in this scope; earlier opening first.
                targets.sort(key=lambda r: str(r["raw"].get("bmStart") or "9999"))
                for item in targets:
                    current = self.store.account(account)
                    if (
                        self.stopping.is_set()
                        or not current
                        or not current["enabled"]
                        or current["watch_generation"] != owner["watch_generation"]
                    ):
                        break
                    status = Activity.from_row(item["raw"]).eligibility()
                    if item["attempt_status"] == "uncertain":
                        self.engine.process(account, item["raw"], client)
                    elif item["id"] in visible and status in {"ready", "registered"}:
                        self.engine.process(account, visible[item["id"]], client)
            self.emit("changed", account)
        except (OSError, SiteError, SessionError, IdentityMismatch) as error:
            self._error(account, error, pause=not isinstance(error, SiteError) or isinstance(error, LoginExpired))
        except Exception:
            self._error(account, RuntimeError("检查未完成，请手动刷新或重新保存登录态。"))
        finally:
            owner = self.store.account(account)
            if owner:
                delay = max(owner["interval"], 60) if owner["status"].startswith("连接") else owner["interval"]
                self.next_poll[account] = time.monotonic() + delay
            self.emit("idle", account)

    def _error(self, account, error, *, pause=True):
        if not self.store.account(account):
            return
        if isinstance(error, FileNotFoundError):
            text = "尚未保存登录态，请导入 Cookie 或网页登录。"
        elif isinstance(error, (SiteError, SessionError, IdentityMismatch)):
            text = str(error)
        elif isinstance(error, OSError):
            text = "登录态暂时无法读取，请重新保存。"
        else:
            text = "操作未完成，请手动刷新检查。"
        if isinstance(error, LoginExpired):
            status = "登录已失效"
            self.clients.pop(account, None)
        else:
            status = "未确认" if pause else "连接待恢复"
        self.store.configure(account, enabled=False if pause else None, status=status)
        self.store.log(account, text)
        self.emit("error", account, message=text)

    def _hydrate_next_detail(self):
        """Read one cached activity's display fields between commands and monitor polls."""
        now = time.monotonic()
        if self.stopping.is_set() or not self.commands.empty() or now < self.next_detail_at:
            return False
        owners = self.store.accounts()
        if any(owner["enabled"] and self.next_poll.get(owner["id"], 0) <= now + 2 for owner in owners):
            return False
        candidates = []
        for owner in owners:
            if owner["status"] != "已登录":
                continue
            account = owner["id"]
            for item in self.store.activities(account):
                raw = item["raw"]
                if owner["term"] and raw.get("_term", owner["term"]) != owner["term"]:
                    continue
                if self.detail_retry.get((account, item["id"]), 0) > now:
                    continue
                status = Activity.from_row(raw).eligibility()
                ttl = 86400 if status == "ended" else 600
                checked = raw.get("_detail_checked_at", 0)
                if isinstance(checked, (int, float)) and time.time() - checked < ttl:
                    continue
                start = parse_time(raw.get("startTime") or raw.get("bmStart"))
                priority = (status != "ready", status == "ended", -start.timestamp() if start else 0)
                candidates.append((priority, account, item["id"], raw))
        if not candidates:
            self.next_detail_at = now + 1
            return False
        _priority, account, identity, cached = min(candidates, key=lambda item: item[:3])
        try:
            detail = self._client(account).detail(identity)
            # Details may fill absent constraints; explicit list values, including 0,
            # remain authoritative. Submission still fetches and validates fresh details.
            fields = {key: detail.get(key) for key in ("startTime", "endTime", "address")}
            for key in ("bmStart", "bmEnd", "state", "bmState", "rz", "maxNum", "currentNum"):
                if cached.get(key) in (None, "") and detail.get(key) not in (None, ""):
                    fields[key] = detail[key]
            self.store.save_activities(
                account, [fields | {"id": identity, "_detail_checked_at": time.time(), "_detail_error": False}]
            )
            self.detail_retry.pop((account, identity), None)
        except LoginExpired as error:
            self._error(account, error)
        except SiteError:
            self.detail_retry[(account, identity)] = time.monotonic() + 60
            self.store.save_activities(account, [{"id": identity, "_detail_error": True}])
            self.store.log(account, "活动详情暂时未读取，稍后自动重试；已有时间和地点保留。")
        except (OSError, SessionError, IdentityMismatch) as error:
            self._error(account, error)
        finally:
            self.next_detail_at = time.monotonic() + 0.5
            self.emit("changed", account)
        return True

    def _ensure_browser(self):
        if not self.playwright:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
        if not self.browser or not self.browser.is_connected():
            self.browser = self.playwright.chromium.launch(channel="msedge", headless=False, args=["--disable-gpu"])

    def _open_browser(self, account, activity=None, save=True):
        self._ensure_browser()
        previous = self.login_windows.pop(account, None)
        if previous:
            previous["context"].close()
        state = None
        try:
            state = json.loads(self.vault.load(account))
        except (OSError, ValueError):
            pass
        context = self.browser.new_context(storage_state=state, locale="zh-CN", timezone_id="Asia/Shanghai")
        page = context.new_page()
        url = LIST_URL if state else LOGIN_URL
        if activity is not None:
            url = ORIGIN + "/zhxg/#/lifePractice/signup/detail?id=" + quote(str(activity), safe="")
        self.login_windows[account] = {"context": context, "save": save, "last": 0, "signature": "", "retry_at": 0}
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        if save:
            self.store.configure(account, enabled=False, status="等待网页登录")
            self.store.log(account, "请在新开的学校官方页面完成登录，验证成功后会自动保存。")
        self.emit("changed", account)

    def _check_login_windows(self):
        for account, data in list(self.login_windows.items()):
            if time.monotonic() - data["last"] < 2:
                continue
            data["last"] = time.monotonic()
            context = data["context"]
            pages = [p for p in context.pages if not p.is_closed()]
            if not pages:
                self.login_windows.pop(account, None)
                if data["save"] and self.store.account(account):
                    self.store.configure(account, status="登录窗口已关闭")
                    self.emit("changed", account)
                context.close()
                continue
            if not data["save"]:
                continue
            for page in pages:
                values = {}
                try:
                    parts = urlsplit(page.url)
                    origin = f"{parts.scheme}://{parts.netloc}".lower()
                    if origin not in {ORIGIN, LOGIN_URL.rstrip("/")}:
                        self._login_wait(account, data, page.url, values, "请从学校门户进入智慧思政活动报名。")
                        continue
                    values = page.evaluate("() => Object.fromEntries(Object.entries(localStorage))")
                    if not isinstance(values, dict):
                        values = {}
                    if origin != ORIGIN:
                        self._login_wait(account, data, page.url, values, "当前仍在学校门户，请进入活动列表。")
                        continue
                    try:
                        preview = import_session(json.dumps({"localStorage": values}))
                    except SessionError as error:
                        self._login_wait(account, data, page.url, values, str(error).split("；当前存储键", 1)[0])
                        continue
                    signature = hashlib.sha256(json.dumps([preview.token, preview.student_id]).encode()).hexdigest()
                    if signature == data["signature"] and time.monotonic() < data.get("retry_at", 0):
                        continue
                    data["signature"] = signature
                    data["retry_at"] = time.monotonic() + 30
                    state = context.storage_state(indexed_db=True)
                    state["cookies"] = [c for c in state["cookies"] if school_domain(c["domain"])]
                    state["origins"] = [o for o in state["origins"] if o["origin"] == ORIGIN]
                    session = import_session(json.dumps(state))
                    self._accept(account, session)
                    context.close()
                    self.login_windows.pop(account, None)
                    break
                except (SessionError, IdentityMismatch, SiteError, OSError) as error:
                    self._error(account, error)
                except Exception:
                    # Navigation during OAuth is normal; poll the owned page again.
                    self._login_wait(account, data, page.url, values, "页面正在跳转或存储暂不可读，稍后继续检查。")
                    continue

    def _login_wait(self, account, data, url, storage, reason):
        now = time.monotonic()
        if now < data.get("notice_at", float("-inf")):
            return
        data["notice_at"] = now + 30
        self.store.log(
            account,
            f"网页登录等待：{login_page_location(url, storage)}；存储键：{storage_key_names(storage)}。{reason}",
        )
        self.emit("changed", account)
