# tool_doc_hints.py — prompt-side tool-doc enhancement (decision gate, 2026-07-06)
#
# 背景:工具有效性研究(b2_think_base 5 重复/448 次调用)定位失败全在参数语义层:
#   find_and_replace 0/23(正则过度转义→"0 replacements"仍报 success/正则乱猜)、
#   纯只读轨迹 0/39、convert_to_docx 0/16 等。基建无罪(exec_ok 98-100%)。
# 本模块把"正确用法/常见坑"以文档形式注入工具描述——把'正确调用'的采样概率
# 从 ≈0 抬到 RL 可见的量级(探索种子走 prompt 侧,不碰 SFT)。
# 仅当 OSWORLD_TOOL_DOC_ENHANCE=1 时由 hybrid_agent_local 调用;默认零影响。
# ⚠️ 不改 MCP 服务端(DON'T)——增强只发生在 agent 侧注入的 <tools> 文本上。

from typing import Any, Dict, List

# key = tool 名的最后一段(suffix match);value = 追加到 description 的 USAGE NOTES
TOOL_HINTS: Dict[str, str] = {
    "find_and_replace": (
        " USAGE NOTES: 'pattern' is a REGULAR EXPRESSION, not plain text. Plain "
        "words need no escaping — e.g. to replace every 'colour' with 'color' "
        "across the whole document call "
        '{"pattern": "colour", "replacement": "color"} '
        "and OMIT paragraph_indices entirely (omitting = all paragraphs; do NOT "
        "pass an empty list). To match a regex special character (. * + ? ( ) [ ]) "
        "literally, escape it with a backslash (doubled inside JSON). Prefer the "
        "simplest pattern that matches. ALWAYS read the returned replacement "
        "count: '0 replacements' means the document was NOT changed and your "
        "pattern is wrong — fix it or switch to the GUI."
    ),
    "env_info": (
        " USAGE NOTES: read-only. Call at most once or twice to inspect state, then "
        "act with an effectful tool or the GUI — repeated info calls make no progress."
    ),
    "get_workbook_info": (
        " USAGE NOTES: read-only. Inspect once, then act — this call by itself never "
        "completes any task."
    ),
    "convert_to_docx": (
        " USAGE NOTES: you MUST pass output_path (full absolute path ending in "
        ".docx, e.g. /home/user/xxx.docx) — if omitted, the converted document is "
        "kept in memory and NOTHING is saved to disk. Verify afterwards that the "
        "file exists where the task requires it."
    ),
    "sort_column": (
        " USAGE NOTES: column_name is a letter ('A','B',...). start_index defaults "
        "to 2 (data starts at row 2, header row untouched) — pass start_index=1 "
        "ONLY if there is no header. Verify the visible result on the next "
        "screenshot before moving on."
    ),
    "reorder_columns": (
        " USAGE NOTES: list ALL columns in the desired final order, not just the ones "
        "that move. Verify the visible result on the next screenshot before moving on."
    ),
    "set_slide_background": (
        " USAGE NOTES: omit slide_index only when the task wants ALL slides changed; "
        "otherwise pass the exact 1-based slide index. Verify visually afterwards."
    ),
}

# 追加在 mcp_hint 之后的通用纪律段(仅工具可用时)
GENERIC_DISCIPLINE = (
    "\n**Tool usage discipline**:\n"
    "1. A tool result reports EXECUTION success, not task progress. Read the result "
    "text: counts like '0 replacements' or an unchanged value mean nothing was "
    "modified — your parameters were wrong.\n"
    "2. After any state-changing MCP call, verify the effect on the next screenshot "
    "before proceeding.\n"
    "3. Read-only tools (env_info / get_*) only gather information; they never "
    "complete a task by themselves.\n"
    "4. If a tool call did not visibly achieve the goal after one retry, switch to "
    "GUI actions instead of repeating the call.\n"
)


def enhance_tool_defs(all_tool_defs: List[Dict[str, Any]]) -> int:
    """Append USAGE NOTES to matching tool descriptions in-place.

    all_tool_defs: the qwen-format list built in hybrid_agent_local (computer_use
    first, then MCP tools). Matching is by the last dot-segment of the tool name.
    Returns the number of tools enhanced (for logging).
    """
    n = 0
    for td in all_tool_defs:
        fn = td.get("function") or {}
        name = fn.get("name") or ""
        suffix = name.rsplit(".", 1)[-1]
        hint = TOOL_HINTS.get(suffix)
        if hint and isinstance(fn.get("description"), str):
            if "USAGE NOTES:" not in fn["description"]:
                fn["description"] = fn["description"].rstrip() + hint
                n += 1
    return n


def zero_effect_warning(feedback: str) -> str:
    """Detect silent no-op results in MCP feedback and append an explicit warning.

    工具有效性研究实录:find_and_replace 返回 '"success":true' + 'Successfully made
    0 replacements' — 模型把执行成功当目标达成。此处在 agent 侧反馈文本尾部补一行
    显式警告(不改服务端)。非 no-op 反馈原样返回。
    """
    if not feedback:
        return feedback
    low = feedback.lower()
    if "0 replacements" in low or "made 0 " in low or "0 matches" in low:
        return (
            feedback
            + "\n[WARNING] The call executed but changed NOTHING (0 replacements/"
            "matches). Do not proceed as if it worked — fix the parameters or use the GUI."
        )
    return feedback
