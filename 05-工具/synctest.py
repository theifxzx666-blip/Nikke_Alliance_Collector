# -*- coding: utf-8 -*-
"""同步层单测：只验证"不丢数据"和"失败留证据"两件事。

    .venv\\Scripts\\python.exe synctest.py

跑的是真 HTTP（本地起一个最小服务器），不 mock urllib——这层最容易出错的地方
恰好是"网络不对时会发生什么"，mock 掉就什么都测不到了。
"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sync_client as S                                        # noqa: E402

fail = []


def check(name, cond, extra=""):
    print(f"{'✓' if cond else '✗'} {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        fail.append(name)


class Echo(BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Echo.got.append({"path": self.path, "auth": self.headers.get("Authorization"),
                         "body": json.loads(self.rfile.read(n) or b"{}")})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *_a):
        pass


tmp = Path(tempfile.mkdtemp(prefix="synctest_"))

print("=== 1. 默认配置：只留档，不联网 ===")
cfg = S.SyncConfig()
box = S.Outbox(tmp / "run1" / "sync")
cli = S.SyncClient(cfg, box)
p = cli.queue("members", "联盟名单", {"count": 32, "members": [{"name": "HANSON"}]})
check("默认 enabled=False", cfg.enabled is False)
check("队列文件已落盘", p.exists(), p.name)
check("flush 在未启用时跳过", cli.flush().get("skipped") is True)
item = json.loads(p.read_text(encoding="utf-8"))
check("记录带 endpoint/用途/attempts",
      item["endpoint"] == "members" and item["purpose"] == "联盟名单"
      and item["attempts"] == 0)
check("端点别名能拼成路径",
      cfg.url("members") == S.DEFAULT_ENDPOINTS["members"], cfg.url("members"))

print("\n=== 2. 启用了但地址不可达：不许丢，要留诊断 ===")
cfg2 = S.SyncConfig({"enabled": True, "base_url": "http://127.0.0.1:1",
                     "timeout": 2, "api_key": "sk_test"})
box2 = S.Outbox(tmp / "run2" / "sync")
logs = []
cli2 = S.SyncClient(cfg2, box2, logger=lambda t, m, lv="INFO": logs.append((lv, t, m)))
cli2.queue("members", "联盟名单", {"count": 1})
r = cli2.flush()
check("失败被计入", r["failed"] == 1 and r["sent"] == 0, str(r))
left = list(box2.items())
check("失败记录仍留在 outbox", len(left) == 1)
bad = json.loads(left[0].read_text(encoding="utf-8"))
check("attempts 已累加", bad["attempts"] >= 1, str(bad.get("attempts")))
check("留了诊断 JSON（含 error_type）",
      isinstance(bad.get("last_error"), dict) and "error_type" in bad["last_error"],
      str(bad.get("last_error"))[:60])
check("诊断进了日志", any("请求失败诊断" in m for _l, _t, m in logs))

print("\n=== 3. 地址可达：成功后归档到 done/ ===")
srv = HTTPServer(("127.0.0.1", 0), Echo)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
cfg3 = S.SyncConfig({"enabled": True, "base_url": f"http://127.0.0.1:{port}",
                     "api_key": "sk_test"})
box3 = S.Outbox(tmp / "run3" / "sync")
cli3 = S.SyncClient(cfg3, box3)
cli3.queue("members", "联盟名单", {"count": 3})
check("请求真的发出去了", len(Echo.got) == 1)
check("鉴权头带上了 key", Echo.got and Echo.got[0]["auth"] == "Bearer sk_test")
check("走的是配置里的端点", Echo.got and Echo.got[0]["path"] == S.DEFAULT_ENDPOINTS["members"],
      Echo.got[0]["path"] if Echo.got else "")
check("载荷被原样送达", Echo.got and Echo.got[0]["body"]["payload"]["count"] == 3)
check("成功后 outbox 清空", len(list(box3.items())) == 0)
check("归档到 done/", len(list(box3.done.glob("*.json"))) == 1)

print("\n=== 4. 幂等：同一条不会被发两次 ===")
box4 = S.Outbox(tmp / "run4" / "sync")
cli4 = S.SyncClient(cfg3, box4)
before = len(Echo.got)
cli4.queue("members", "联盟名单", {"count": 1})
cli4.flush()
check("一次入队只发一次", len(Echo.got) - before == 1, f"多发 {len(Echo.got) - before - 1} 次")

srv.shutdown()
print("\n=== 结果 ===")
print("全部通过" if not fail else f"失败项：{fail}")
raise SystemExit(0 if not fail else 1)
