#!/usr/bin/env python3
"""Mock of the GitHub release endpoints used by packaging/gh_release.sh.

Run:  python3 mock_gh_api.py [port]
Then: GITHUB_API_URL=http://127.0.0.1:<port>/api GITHUB_REPOSITORY=o/r \\
      GITHUB_REF_NAME=v9.9.9 GH_TOKEN=test ./packaging/gh_release.sh upload file name

Keeps state in memory so the script's get-or-create, replace-asset-by-name and
upload retry paths can be exercised without touching github.com.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
STATE = {
    "release": None,          # dict or None
    "assets": [],             # [{id,name,size}]
    "next_id": 500,
    "requests": [],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _base(self):
        return "http://127.0.0.1:%d/api" % PORT

    def _release_payload(self):
        r = dict(STATE["release"])
        r["upload_url"] = "%s/repos/o/r/releases/%s/assets{?name,label}" % (
            self._base(), STATE["release"]["id"])
        return r

    def do_GET(self):
        STATE["requests"].append(("GET", self.path))
        if self.path == "/api/state":
            return self._send(200, STATE)
        if self.path.startswith("/api/repos/o/r/releases/tags/"):
            if STATE["release"]:
                return self._send(200, self._release_payload())
            return self._send(404, {"message": "Not Found"})
        if "/releases/" in self.path and self.path.rstrip().endswith("assets?per_page=100"):
            head = self.path.split("?")[0]
            rid = head.split("/releases/")[1].split("/")[0]
            if not rid.isdigit():
                return self._send(404, {"message": "Not Found"})
            return self._send(200, {"assets": [a for a in STATE["assets"]]})
        return self._send(404, {"message": "Not Found"})

    def do_POST(self):
        STATE["requests"].append(("POST", self.path))
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        if self.path.startswith("/api/repos/o/r/releases") and "/assets" not in self.path:
            data = json.loads(body.decode() or "{}")
            STATE["release"] = {"id": 4242, "tag_name": data.get("tag_name"),
                                "name": data.get("name"), "body": data.get("body", "")}
            return self._send(201, self._release_payload())
        if "/assets" in self.path:
            name = self.path.split("name=")[-1].split("&")[0]
            STATE["next_id"] += 1
            STATE["assets"].append({"id": STATE["next_id"], "name": name,
                                    "size": len(body)})
            return self._send(201, {"id": STATE["next_id"], "name": name})
        return self._send(404, {"message": "Not Found"})

    def do_DELETE(self):
        STATE["requests"].append(("DELETE", self.path))
        if "/releases/assets/" in self.path:
            aid = int(self.path.rsplit("/", 1)[1])
            before = len(STATE["assets"])
            STATE["assets"] = [a for a in STATE["assets"] if a["id"] != aid]
            if len(STATE["assets"]) == before:
                return self._send(404, {"message": "Not Found"})
            return self._send(204, raw=b"")
        return self._send(404, {"message": "Not Found"})


if __name__ == "__main__":
    print("mock api on %d" % PORT, flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
