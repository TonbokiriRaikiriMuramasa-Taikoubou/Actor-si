"""Webhook 送出（ルームに発言があったことを外部へ通知する）。

- 別スレッドのワーカーが順に送る（HTTP ハンドラをブロックしない）
- ``X-Actor-Signature: sha256=<hex>`` で署名する（``ACTOR_WEBHOOK_SECRET`` を設定した場合）
- 失敗しても会話は止めない。リトライは 3 回（指数バックオフ）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

__all__ = ["WebhookDispatcher", "sign_payload"]


def sign_payload(secret: str, body: bytes) -> str:
    """GitHub / Stripe 風の HMAC 署名（``sha256=...``）。"""
    if not secret:
        return ""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@dataclass
class WebhookDispatcher:
    """URL が設定されていれば、イベントを非同期に POST する。"""

    url: str = ""
    secret: str = ""
    events: Sequence[str] = ("message",)
    timeout: float = 10.0
    max_retries: int = 3
    on_log: Callable[[str], None] | None = None
    stats: dict[str, int] = field(default_factory=lambda: {"queued": 0, "sent": 0, "failed": 0, "skipped": 0})
    _queue: "queue.Queue[dict[str, Any] | None]" = field(default_factory=queue.Queue, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def __post_init__(self) -> None:
        if self.url:
            self._thread = threading.Thread(target=self._worker, name="actor-webhook", daemon=True)
            self._thread.start()

    # ---- 公開 API ----
    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def wants(self, event: str) -> bool:
        if not self.enabled:
            return False
        events = list(self.events or [])
        return "*" in events or event in events

    def emit(self, event: str, payload: dict[str, Any]) -> bool:
        """イベントをキューへ積む（送信はバックグラウンド）。"""
        if not self.wants(event):
            self.stats["skipped"] += 1
            return False
        body = {"event": event, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **payload}
        self._queue.put(body)
        self.stats["queued"] += 1
        return True

    def shutdown(self, wait: bool = True) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread and wait:
            self._thread.join(timeout=3.0)

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        """テスト用: キューが空になるまで待つ。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.unfinished_tasks == 0 if hasattr(self._queue, "unfinished_tasks") else True:
                if self.stats["queued"] == self.stats["sent"] + self.stats["failed"] + self.stats["skipped"]:
                    return True
            time.sleep(0.05)
        return False

    # ---- ワーカー ----
    def _worker(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            try:
                if item is None:
                    return
                self._deliver(item)
            finally:
                self._queue.task_done()

    def _deliver(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Actor-Webhook/0.1",
            "X-Actor-Event": str(payload.get("event", "")),
        }
        signature = sign_payload(self.secret, body)
        if signature:
            headers["X-Actor-Signature"] = signature

        for attempt in range(self.max_retries):
            request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    response.read(1024)
                self.stats["sent"] += 1
                self._log(f"Sent event={payload.get('event')} ({response.status})")
                return
            except urllib.error.HTTPError as exc:
                if exc.code < 500 and exc.code != 429:
                    break
            except Exception:
                pass
            time.sleep(min(4.0, 0.5 * (2 ** attempt)))
        self.stats["failed"] += 1
        self._log(f"Failed to send event={payload.get('event')} url={self.url}")

    def _log(self, message: str) -> None:
        if self.on_log:
            self.on_log(message)
