"""DeepSeek Harness headless adapter for agent.* workflow nodes."""

import json
import os
import subprocess
from pathlib import Path


class DeepSeekHeadlessExecutor:
    def __init__(self, executable: Path, workspace: Path, dsh_home: Path,
                 *, key_env: str = "HPX_DEEPSEEK_KEY", timeout_sec: int = 300):
        self.executable = executable
        self.workspace = workspace
        self.dsh_home = dsh_home
        self.key_env = key_env
        self.timeout_sec = timeout_sec

    def __call__(self, node: dict, context: dict) -> dict:
        if not node.get("uses", "").startswith("agent."):
            raise ValueError(f"DeepSeek Harness 适配器只执行 agent.* 节点：{node.get('uses')}")
        task = node.get("config", {}).get("task")
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"agent 节点 {node['id']} 缺少 config.task")
        key = os.environ.get(self.key_env)
        if not key:
            raise RuntimeError(f"缺少 DeepSeek 凭据变量 {self.key_env}")
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.dsh_home.mkdir(parents=True, exist_ok=True)
        allowed = {"PATH", "HOME", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy",
                   "https_proxy", "no_proxy", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE"}
        env = {name: value for name, value in os.environ.items() if name in allowed}
        env["DSH_HOME"] = str(self.dsh_home)
        env["DEEPSEEK_API_KEY"] = key
        prompt = json.dumps({"task": task, "inputs": context.get("input", {}),
                             "evidence": context.get("evidence", {}),
                             "required_output": "Return only a JSON object."}, ensure_ascii=False)
        try:
            result = subprocess.run([str(self.executable), "--profile", "headless",
                                     "--permission-mode", "read-only", "--approval-policy", "never", "-"],
                                    input=prompt, text=True, capture_output=True,
                                    cwd=self.workspace, env=env, timeout=self.timeout_sec, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError(f"找不到 DeepSeek Harness 命令：{self.executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"DeepSeek Harness 节点 {node['id']} 超时") from exc
        if result.returncode != 0:
            raise RuntimeError(f"DeepSeek Harness 节点 {node['id']} 失败（退出码 {result.returncode}）")
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"DeepSeek Harness 节点 {node['id']} 未返回 JSON 对象") from exc
        if not isinstance(output, dict):
            raise RuntimeError(f"DeepSeek Harness 节点 {node['id']} 必须返回 JSON 对象")
        return output
