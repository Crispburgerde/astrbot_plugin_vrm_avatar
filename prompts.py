"""
此文件包含预设的提示词
"""

from .performance import EMOTION_WORDS

DIRECTOR_PROMPT = """你是一个虚拟形象表演导演。用户会给你本轮的用户消息（用于理解对话场景）以及角色对该消息的原始回复文本，你需要在回复文本中插入情绪标签，标注每段文本说话时的情绪与语气，直接输出标注后的文本。

### 标签格式
标签形如 `[happy]`，插在它所作用的文本之前，作用范围直到下一个标签；可用情绪枚举：{emotions}

### 规则
1. **不拆句、不改写**：保持原文的文字、标点与分段完全不变，只在情绪或语气发生变化的位置插入标签；同一情绪的连续文本只在开头插一个标签，不要每句都插
2. **用户消息仅作场景参考**：用户消息只用于理解语境以选择更贴合的情绪，禁止把用户消息的内容编入回复或改变回复内容
3. **去除动作描写**：如原文中出现括号（`()`、`（）`、`【】`）或星号（`*`）括住的动作描写（如 `(微笑)`、`*叹气*`），删去这些描写，其情绪改用标签表达
4. **枚举值逐字选取（硬性约束）**：标签内容**只能从情绪枚举中逐字选取**，禁止创造枚举之外的任何新值；没有贴合的情绪时不插标签（沿用上一个标签的情绪）
5. **直接输出标注后的文本**：不要包含 Markdown 代码块标记、注释或任何额外说明；文本开头若没有标签则默认为 neutral
"""

IDLE_PROMPT_TEMPLATE = (
    "### 待机姿态\n\n"
    "待机姿态是**持续状态**，不属于回复内容，禁止用文字表达。当角色的情绪或状态发生持久变化、"
    "希望形象静止时以不同姿态待机时，调用 `set_idle` 工具切换；同一姿态会保持到下一次切换，"
    "不要频繁调用。可用值：{idle}"
)

DIRECTOR_USER_TEMPLATE = """### 用户消息
{user_message}

### 角色回复（待编排的原始文本，只编排此段文本）
{reply}"""


def _join_names(names: list[str], fallback: str) -> str:
    """Render a name list as a quoted, bar-separated string.

    When the list is empty, ``fallback`` is returned so the prompt still
    reads naturally (e.g., a lone ``"neutral"`` union member).
    """
    return " | ".join(f'"{n}"' for n in names) if names else fallback


def build_director_prompt() -> str:
    """Build the system prompt for the director model that inserts inline
    emotion/tone tags into a raw reply.

    Returns:
        The formatted prompt string with the fixed 24-word emotion
        vocabulary inlined.
    """
    return DIRECTOR_PROMPT.replace(
        "{emotions}", _join_names(list(EMOTION_WORDS), '"neutral"')
    )


def build_director_user_prompt(user_message: str, reply: str) -> str:
    """Build the user prompt for the director model.

    Bundles the triggering user message (context for picking fitting
    emotions) with the raw reply to orchestrate. Designed so more context
    sections (e.g., character state) can be added as siblings later.

    Args:
        user_message: The user's message text of this turn; may be empty.
        reply: The raw reply text to split into performances.

    Returns:
        The formatted user prompt string.
    """
    return DIRECTOR_USER_TEMPLATE.format(
        user_message=user_message.strip() or "（无）",
        reply=reply,
    )


def build_idle_prompt(idle: list[str]) -> str:
    """Build the system prompt fragment teaching the main LLM to switch
    standby poses via the ``set_idle`` tool.

    Args:
        idle: The allowed idle variant names.

    Returns:
        The formatted prompt fragment.
    """
    return IDLE_PROMPT_TEMPLATE.format(idle=_join_names(idle, ""))
