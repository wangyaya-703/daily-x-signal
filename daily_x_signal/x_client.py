from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class XReachError(RuntimeError):
    pass


class XReachClient:
    def __init__(self, binary: str = "xreach", workdir: str | Path = ".") -> None:
        self.binary = self._resolve_binary(binary)
        self.workdir = Path(workdir)

    def run_json(self, *args: str) -> Any:
        cmd = [self.binary, *args, "--json"]
        proc = subprocess.run(
            cmd,
            cwd=self.workdir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise XReachError(proc.stderr.strip() or proc.stdout.strip() or "xreach 执行失败")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise XReachError(f"xreach JSON 解析失败：{exc}") from exc

    def user(self, handle: str) -> dict[str, Any]:
        return self.run_json("user", handle)

    def home(self) -> dict[str, Any]:
        return self.run_json("home")

    def following(self, handle: str, *, max_pages: int = 1, count: int = 20) -> dict[str, Any]:
        return self.run_json("following", handle, "--count", str(count), "--max-pages", str(max_pages))

    def following_by_user_id(self, user_id: str, *, max_pages: int = 1, count: int = 20) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        has_more = False
        module_root = self._resolve_xreach_cli_root()
        for _ in range(max_pages):
            payload = self._run_node_json(
                """
import { pathToFileURL } from 'node:url';

const [moduleRoot, userId, countArg, cursorArg] = process.argv.slice(1);
const { XClient } = await import(pathToFileURL(`${moduleRoot}/dist/lib/client/index.js`).href);
const { SessionManager } = await import(pathToFileURL(`${moduleRoot}/dist/lib/auth/session.js`).href);
const session = new SessionManager().load();
if (!session?.authToken || !session?.ct0) {
  throw new Error('Not authenticated');
}
const client = new XClient(session, {});
const result = await client.getFollowing(userId, parseInt(countArg, 10), cursorArg || undefined);
console.log(JSON.stringify(result));
                """,
                str(module_root),
                user_id,
                str(count),
                cursor or "",
            )
            items.extend(payload.get("items", []))
            cursor = payload.get("cursor")
            has_more = bool(payload.get("hasMore"))
            if not cursor or not has_more:
                break
        return {"items": items, "cursor": cursor, "hasMore": has_more}

    def tweets(self, handle: str, *, replies: bool = False, max_pages: int = 1, count: int = 20) -> dict[str, Any]:
        args = ["tweets", handle, "--count", str(count), "--max-pages", str(max_pages)]
        if replies:
            args.append("--replies")
        return self.run_json(*args)

    def thread(self, tweet_id_or_url: str) -> list[dict[str, Any]]:
        return self.run_json("thread", tweet_id_or_url)

    def _run_node_json(self, script: str, *args: str) -> Any:
        cmd = ["node", "--input-type=module", "-e", script, *args]
        proc = subprocess.run(
            cmd,
            cwd=self.workdir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise XReachError(proc.stderr.strip() or proc.stdout.strip() or "Node 桥接执行失败")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise XReachError(f"Node 桥接 JSON 解析失败：{exc}") from exc

    def _resolve_xreach_cli_root(self) -> Path:
        env_root = os.getenv("XREACH_CLI_ROOT")
        if env_root:
            candidate = Path(env_root).expanduser()
            if (candidate / "dist" / "lib" / "client" / "index.js").exists():
                return candidate

        npm = shutil.which("npm")
        if npm:
            proc = subprocess.run(
                [npm, "root", "-g"],
                cwd=self.workdir,
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                candidate = Path(proc.stdout.strip()) / "xreach-cli"
                if (candidate / "dist" / "lib" / "client" / "index.js").exists():
                    return candidate

        raise XReachError("未找到 xreach-cli 的 Node 模块目录，请确认已全局安装 xreach-cli，或设置 XREACH_CLI_ROOT。")

    def _resolve_binary(self, binary: str) -> str:
        if Path(binary).expanduser().exists():
            return str(Path(binary).expanduser())

        resolved = shutil.which(binary)
        if resolved:
            return resolved

        fallback = Path.home() / ".npm-global" / "bin" / binary
        if fallback.exists():
            return str(fallback)

        return binary
