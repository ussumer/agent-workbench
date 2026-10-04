"""诊断两个外部服务是否真的可用——不是"配置项存在"，而是"服务真的应答"。

`capability_report()` 只能判断环境变量**存在**，判断不了它**有效**：一个被撤销的 key 或抄错的
key 同样会让它显示 satisfied。这个脚本补上那一步，对两个服务各做一次真实调用：

* 智谱搜索：先用基础接口 `chat/completions` 探 key 是否有效，再用 `web_search` 探搜索是否开通。
  分开探是有意义的——"key 无效"和"没开通搜索"是两个问题，修法不同，而一个裸 401 分不出来。
* ModelScope 图表：SSE 握手并列出工具，确认传输形态与鉴权头都对。

不打印任何密钥内容。

用法：
    python scripts/check_external.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# The Windows console defaults to a legacy code page that cannot represent the marks this
# script prints; reporting is the whole point of it, so it must not die on a symbol.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

from agent.config import capability_report  # noqa: E402
from agent.env_utils import load_env  # noqa: E402

ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"

#: Endpoints that any valid Zhipu key can call, used to separate "bad key" from "no search".
BASIC_PATH = "/chat/completions"

SEARCH_ENGINE = "search_pro_sogou"


def describe_key(key: str) -> dict[str, object]:
    """Structural facts only — never the value."""
    return {
        "length": len(key),
        "has_dot": "." in key,
        "has_whitespace": key != key.strip() or " " in key,
        "ascii": key.isascii(),
    }


def check_search(env: dict[str, str]) -> list[str]:
    """Returns a list of problems; empty means the search side works."""
    key = env.get("ZHIPU_API_KEY", "")
    if not key:
        return ["ZHIPU_API_KEY 未设置"]

    problems: list[str] = []
    print(f"  智谱 key 结构: {json.dumps(describe_key(key), ensure_ascii=False)}")

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=40.0, trust_env=False) as client:
        basic = client.post(
            f"{ZHIPU_BASE}{BASIC_PATH}",
            headers=headers,
            json={"model": "glm-4-flash", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4},
        )
        print(f"  基础接口 {BASIC_PATH}: status={basic.status_code}")
        if basic.status_code == 401:
            problems.append(
                "key 未通过校验（连基础接口都 401）。智谱的 key 形如 `{id}.{secret}`（**含一个点**）；"
                "请确认复制的是完整 key 而不是 Key ID，且该 key 未被删除或禁用"
            )
            return problems
        if basic.status_code != 200:
            print(f"    非 401 的异常响应: {basic.text[:200]}")

        search = client.post(
            f"{ZHIPU_BASE}/web_search",
            headers=headers,
            json={"search_engine": SEARCH_ENGINE, "search_query": "刹车片 供应商", "count": 2},
        )
        print(f"  搜索接口 web_search: status={search.status_code}")
        if search.status_code != 200:
            problems.append(explain_search_failure(search))
            return problems

        payload = search.json()
        results = payload.get("search_result") or []
        print(f"  search_result: {len(results)} 条")
        if not results:
            problems.append("搜索返回 200 但没有结果——T15 要求结果可引用，需要换查询词复核")
            return problems
        first = results[0] if isinstance(results[0], dict) else {}
        print(f"    首条 keys: {sorted(first)}")
        for field in ("title", "link", "publish_date"):
            if field not in first:
                problems.append(f"结果缺少 {field!r} 字段，引用信息不完整")
    return problems


#: Zhipu error codes seen in practice, and what each one actually means for the fix. The
#: distinction matters: "bad key", "not activated" and "no balance" all look like a failed
#: call, and only one of them is fixed by topping the account up.
ZHIPU_ERROR_GUIDANCE: dict[str, str] = {
    "1113": "余额不足或无可用资源包——**去账户充值，或购买网络搜索资源包**。"
    "搜索是独立计费的接口（search_pro_sogou 约 0.05 元/次），"
    "对话模型的免费额度不覆盖它。",
    "1000": "认证失败——key 无效或已停用，请重新生成。",
    "1001": "认证失败——key 无效或已停用，请重新生成。",
    "1261": "API 调用异常，可稍后重试。",
    "1210": "参数错误——检查 search_engine 与 search_query。",
}


def explain_search_failure(response: httpx.Response) -> str:
    """Turn a failed search call into the action that actually fixes it."""
    code = ""
    message = ""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code", ""))
            message = str(error.get("message", ""))
    except ValueError:
        message = response.text[:200]

    guidance = ZHIPU_ERROR_GUIDANCE.get(code)
    prefix = f"搜索接口返回 {response.status_code}" + (f"（code={code}）" if code else "")
    if guidance:
        return f"{prefix}：{guidance}"
    return f"{prefix}：{message or response.text[:200]}"


async def check_chart(env: dict[str, str]) -> list[str]:
    url = env.get("MODELSCOPE_MCP_URL", "")
    token = env.get("MODELSCOPE_API_TOKEN", "")
    if not url or not token:
        return ["MODELSCOPE_MCP_URL 或 MODELSCOPE_API_TOKEN 未设置"]

    from mcp import ClientSession
    from mcp.client.sse import sse_client

    problems: list[str] = []
    try:
        async with sse_client(url, headers={"Authorization": f"Bearer {token}"}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
    except Exception as failure:  # noqa: BLE001 - reported
        return [f"SSE 握手失败：{type(failure).__name__}: {failure}（确认传输形态是 /sse 还是 /mcp）"]

    names = [tool.name for tool in listed.tools]
    print(f"  工具数量: {len(names)}")
    for required in ("generate_bar_chart", "generate_line_chart", "generate_pie_chart"):
        if required not in names:
            problems.append(f"缺少必需工具 {required}")
    print(f"  必需的三类: {'齐全' if not problems else '有缺失'}")
    return problems


def main() -> int:
    env = load_env()
    report = capability_report()
    print("=== 能力判定（只看配置项是否存在）===")
    for name, entry in report.items():
        print(f"  {name}: satisfied={entry['satisfied']} missing={entry['missing']}")

    print("\n=== 智谱搜索（真实调用）===")
    search_problems = check_search(env)

    print("\n=== ModelScope 图表（真实调用）===")
    chart_problems = asyncio.run(check_chart(env))

    print("\n=== 结论 ===")
    problems = [*search_problems, *chart_problems]
    if not problems:
        print("  两个外部服务都可用。")
        return 0
    for problem in problems:
        print(f"  ✗ {problem}")
    print("\n  注意：capability_report 可能显示 satisfied，但它只检查配置项是否存在。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
