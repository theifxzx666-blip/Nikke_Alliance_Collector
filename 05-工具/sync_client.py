# -*- coding: utf-8 -*-
"""同步接口（预留）：把采集结果排队、落盘，网络可用时再发出去。

为什么先做这一层而不是直接写 HTTP：用户还没定后端要什么字段，但**采集数据的
结构早晚要往外走**。所以这里只定三件事，其余全部留给配置：

1. 一次采集产生一条"待同步"记录，先写进 `outbox/` 目录，天然幂等——
   发失败就留在原地，下次启动重投，不会因为断网丢数据。
2. 端点路径、鉴权头、超时全在 `同步.json` 里，改配置不改代码。
3. 失败要留**诊断**（状态码 / 异常类型 / 原因 / 端点 / 方法），照 ArcanaHelper
   的 `请求失败诊断` 记法；只说"同步失败"等于什么都没说。

默认 `enabled=false`：没接通后端之前，它只做本地留档，不产生任何网络请求。
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ENDPOINTS = {
    # 采集完一轮联盟名单后同步整份名单
    "members": "/third/bot/nikke/v1/syncAllianceMembers",
}

DEFAULT_CFG = {
    "enabled": False,          # 默认关闭：没有后端时只留档、不联网
    "base_url": "",            # 例：https://api.example.com
    "api_key": "",             # 与后端约定的密钥
    "auth_header": "Authorization",
    "auth_prefix": "Bearer ",
    "timeout": 8,
    "endpoints": DEFAULT_ENDPOINTS,
}


class SyncConfig:
    """配置对象。缺字段一律回落到 DEFAULT_CFG，不让半截配置把工具弄崩。"""

    def __init__(self, data=None):
        d = dict(DEFAULT_CFG)
        d.update(data or {})
        ep = dict(DEFAULT_ENDPOINTS)
        ep.update(d.get("endpoints") or {})
        d["endpoints"] = ep
        self.__dict__.update(d)

    def url(self, key_or_path: str) -> str:
        """端点可以是别名（members），也可以是完整路径/URL。"""
        path = self.endpoints.get(key_or_path, key_or_path)
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return (self.base_url or "").rstrip("/") + path

    def headers(self):
        h = {"Content-Type": "application/json; charset=utf-8",
             "User-Agent": "NikkeAllianceOCR/2.0"}
        if self.api_key:
            h[self.auth_header] = f"{self.auth_prefix}{self.api_key}"
        return h

    def as_dict(self):
        return {k: getattr(self, k) for k in DEFAULT_CFG}


def load_config(path: Path) -> SyncConfig:
    try:
        if path.exists():
            return SyncConfig(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        pass
    return SyncConfig()


class Outbox:
    """落盘队列：一条记录一个 json，成功移到 done/，失败原地不动。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.pending = self.root / "outbox"
        self.done = self.root / "outbox_done"
        self.pending.mkdir(parents=True, exist_ok=True)
        self.done.mkdir(parents=True, exist_ok=True)

    def put(self, item) -> Path:
        name = f"{time.strftime('%Y%m%d_%H%M%S')}_{item['id']}.json"
        p = self.pending / name
        p.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def items(self):
        return sorted(self.pending.glob("*.json"))

    def finish(self, path: Path):
        try:
            path.rename(self.done / path.name)
        except Exception:
            pass


class SyncClient:
    """对外的唯一入口。主程序只调 `queue()`，不需要知道 HTTP 细节。"""

    def __init__(self, cfg: SyncConfig, outbox: Outbox, logger=None):
        self.cfg = cfg
        self.outbox = outbox
        self.log = logger or (lambda *_a, **_k: None)
        self._seq = 0

    def queue(self, endpoint: str, purpose: str, payload=None, files=None) -> Path:
        """记一条待同步。**先落盘、再发**——发失败也不丢，这是顺序上的硬要求。"""
        self._seq += 1
        item = {
            "id": f"{int(time.time())}_{self._seq:03d}",
            "endpoint": endpoint,
            "purpose": purpose,
            "payload": payload or {},
            "files": [str(f) for f in (files or [])],
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "attempts": 0,
        }
        p = self.outbox.put(item)
        self.log("sync", f"待上传 endpoint={self.cfg.url(endpoint)} 用途={purpose} "
                         f"文件={item['files'] or '无'} 记录={p.name}")
        if self.cfg.enabled:
            self._send(p, item)
        return p

    def flush(self, limit=20) -> dict:
        """把积压的待同步全部重投一次。返回 (成功数, 失败数)。"""
        ok = bad = 0
        if not self.cfg.enabled:
            return {"sent": 0, "failed": 0, "skipped": True}
        for p in self.outbox.items()[:limit]:
            try:
                item = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if self._send(p, item):
                ok += 1
            else:
                bad += 1
        return {"sent": ok, "failed": bad, "skipped": False}

    # ---------------- 内部

    def _send(self, path: Path, item) -> bool:
        url = self.cfg.url(item["endpoint"])
        body = json.dumps({"purpose": item["purpose"], "payload": item["payload"]},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=self.cfg.headers(),
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
                code = getattr(r, "status", 200)
            self.outbox.finish(path)
            self.log("sync", f"同步成功 endpoint={url} 用途={item['purpose']} "
                             f"http={code} · 已归档")
            return True
        except urllib.error.HTTPError as exc:
            return self._fail(path, item, url, {
                "status": exc.code, "error_type": "HTTPError",
                "reason": str(exc.reason)})
        except Exception as exc:
            return self._fail(path, item, url, {
                "status": None, "error_type": type(exc).__name__, "reason": str(exc)})

    def _fail(self, path: Path, item, url, diag) -> bool:
        item["attempts"] = int(item.get("attempts", 0)) + 1
        item["last_error"] = diag
        try:
            path.write_text(json.dumps(item, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except Exception:
            pass
        self.log("sync", "请求失败诊断 " + json.dumps({
            **diag, "endpoint": url, "method": "POST",
            "attempts": item["attempts"]}, ensure_ascii=False), "WARN")
        return False
