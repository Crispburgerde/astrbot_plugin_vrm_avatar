"""
此文件包含预设的提示词
"""

from .performance import EMOTION_WORDS

EXTRACTOR_PROMPT = """你是一个虚拟形象表演编排器。用户会给你本轮的用户消息（用于理解对话场景）以及角色对该消息的原始回复文本，你需要把回复拆分成一句一句的台词，并结合场景为每句台词标注情绪及其强度，输出为 JSON 数组。

### 结构定义
以下 TypeScript 类型仅用于说明输出结构，实际输出的是纯 JSON 数组：
```ts
type Emotion = {emotions};
interface Performance {
  text: string;             // 必填：该句台词
  emotion?: Emotion;        // 可选：说这句话时的情绪，默认 neutral
  intensity?: number;       // 可选：情绪强度 0~1 的小数，默认 0.85
}
type Result = Performance[];
```
### 规则
1. **按情绪变化点拆分**：拆分的基本单位是**情绪/表现变化点**——同一情绪的连续台词并入同一个对象，只有当情绪（或表现强度）发生明显变化时才拆分出一个新对象，保持原文顺序；只做拆分，不增删改写。避免产出大量只有几个字的碎片台词，短回复通常一个对象即可
2. **用户消息仅作场景参考**：用户消息只用于理解语境以选择更贴合的情绪与强度，禁止把用户消息的内容编排进台词或改变台词内容
3. **去除动作描写**：`text` 中禁止出现括号（如 `()`、`（）`、`【】`）或星号（`*`）括住的动作描写（如 `(微笑)`、`*叹气*`），其情绪应体现在 `emotion` 与 `intensity` 的选择中
4. **枚举值逐字选取（硬性约束）**：`emotion` 的值**只能从类型定义的字符串枚举中选取**，禁止创造枚举之外的任何新值；没有贴合的情绪时使用 `neutral`
5. **intensity 取值**：0~1 之间的小数，表示情绪强度；平淡陈述用较低值，强烈情绪用较高值
6. **仅输出 JSON 数组**：纯 JSON 文本，第一个字符必须是 `[`，最后一个字符必须是 ]，不要包含 Markdown 代码块标记、注释或任何额外说明
7. **text 禁双引号、不换行**：一句台词一行写完，值内禁止出现双引号与换行
"""

IDLE_PROMPT_TEMPLATE = (
    "### 待机姿态\n\n"
    "待机姿态是**持续状态**，不属于回复内容，禁止用文字表达。当角色的情绪或状态发生持久变化、"
    "希望形象静止时以不同姿态待机时，调用 `set_idle` 工具切换；同一姿态会保持到下一次切换，"
    "不要频繁调用。可用值：{idle}"
)

EXTRACTOR_USER_TEMPLATE = """### 用户消息
{user_message}

### 角色回复（待编排的原始文本，只编排此段文本）
{reply}"""


def _join_names(names: list[str], fallback: str) -> str:
    """Render a name list as a quoted, bar-separated string.

    When the list is empty, ``fallback`` is returned so the prompt still
    reads naturally (e.g., a lone ``"neutral"`` union member).
    """
    return " | ".join(f'"{n}"' for n in names) if names else fallback


def build_extractor_prompt() -> str:
    """Build the system prompt for the extractor model that splits a raw
    reply into dialogue lines and picks an emotion plus intensity per line.

    Returns:
        The formatted prompt string with the fixed 24-word emotion
        vocabulary inlined.
    """
    return EXTRACTOR_PROMPT.replace(
        "{emotions}", _join_names(list(EMOTION_WORDS), '"neutral"')
    )


def build_extractor_user_prompt(user_message: str, reply: str) -> str:
    """Build the user prompt for the extractor model.

    Bundles the triggering user message (context for picking fitting
    emotions) with the raw reply to orchestrate. Designed so more context
    sections (e.g., character state) can be added as siblings later.

    Args:
        user_message: The user's message text of this turn; may be empty.
        reply: The raw reply text to split into performances.

    Returns:
        The formatted user prompt string.
    """
    return EXTRACTOR_USER_TEMPLATE.format(
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
