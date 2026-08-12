"""All-flags-on mock of the disco feature-flag service (local eval testing only).

POST /api/feature-flags/ -> {"values": {<every FeatureFlag value>: true}}

Requires PLANE_EE_API_DIR (path to plane-ee apps/api, or the monorepo root
containing apps/api/plane/payment/flags/flag.py).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _resolve_flag_module_path() -> Path:
    base = os.environ.get("PLANE_EE_API_DIR", "").strip()
    if not base:
        raise SystemExit(
            "error: PLANE_EE_API_DIR is required (path to plane-ee apps/api, or monorepo root with apps/api/...)"
        )
    root = Path(base).expanduser().resolve()
    candidates = [
        root / "plane" / "payment" / "flags" / "flag.py",
        root / "apps" / "api" / "plane" / "payment" / "flags" / "flag.py",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise SystemExit(
        f"error: FeatureFlag module not found under PLANE_EE_API_DIR={root}; "
        f"tried: {', '.join(str(c) for c in candidates)}"
    )


def load_feature_flag_values() -> dict[str, bool]:
    """Load FeatureFlag enum values and return {value: True} for every flag."""
    flag_path = _resolve_flag_module_path()
    # Put the API package root on sys.path so relative imports inside flag.py work
    # when the module itself only needs the enum.
    api_root = flag_path.parents[3]  # .../plane
    package_root = flag_path.parents[4]  # .../apps/api or similar
    for p in (str(package_root), str(api_root.parent)):
        if p not in sys.path:
            sys.path.insert(0, p)

    spec = importlib.util.spec_from_file_location("plane_eval_flag", flag_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"error: cannot load flag module from {flag_path}")
    flag_mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(flag_mod)
    except Exception as exc:
        # Fallback: read the file and eval only the enum body if full import fails
        # (Django settings). Try a minimal AST-free scrape of string values.
        text = flag_path.read_text(encoding="utf-8")
        values: dict[str, bool] = {}
        for line in text.splitlines():
            line = line.strip()
            # e.g.  FOO = "FOO"  or FOO = "some-flag"
            if "=" in line and not line.startswith("#") and not line.startswith("class"):
                _, _, rhs = line.partition("=")
                rhs = rhs.strip().rstrip(",")
                if len(rhs) >= 2 and rhs[0] in "\"'" and rhs[-1] == rhs[0]:
                    values[rhs[1:-1]] = True
        if values:
            print(
                f"mock flag server: loaded {len(values)} flags via scrape (import failed: {exc})",
                flush=True,
            )
            return values
        raise SystemExit(f"error: failed to import FeatureFlag from {flag_path}: {exc}") from exc

    FeatureFlag = getattr(flag_mod, "FeatureFlag", None)
    if FeatureFlag is None:
        raise SystemExit(f"error: FeatureFlag not found in {flag_path}")
    return {f.value: True for f in FeatureFlag}


def main(argv: list[str] | None = None) -> int:
    host = "127.0.0.1"
    port = 9911
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) >= 1:
        port = int(argv[0])

    values = load_feature_flag_values()
    print(f"mock flag server: {len(values)} flags all-on on {host}:{port}", flush=True)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.dumps({"values": values}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"values": values, "ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    # Threaded: Django dev server is multi-threaded and fans out flag lookups.
    ThreadingHTTPServer((host, port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
