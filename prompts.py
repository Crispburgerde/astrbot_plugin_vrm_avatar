"""
此文件包含预设的提示词
"""

FORMAT_PROMPT = """
## 输出格式要求

你必须严格按照以下 TypeScript 类型定义输出 JSON 格式的回复。不要输出任何其他内容，只输出 JSON。

```typescript
interface Segment {
  /** 该段对话的文本内容 */
  dialogue: string;
  /** 该段的表情，必须是允许值之一 */
  expression: {expressions};
  /** 该段的肢体动作，必须是允许值之一；不需要动作时设为 null（表示维持待机） */
  action: {actions} | null;
}

// 输出为 Segment 数组，可在对话过程中切换表情与动作
type Response = Segment[];
```

### 输出示例：
```json
[
  {
    "dialogue": "嗨，你好呀！",
    "expression": "happy",
    "action": "wave"
  },
  {
    "dialogue": "很高兴见到你。",
    "expression": "happy",
    "action": null
  },
  {
    "dialogue": "让我想想……",
    "expression": "neutral",
    "action": null
  }
]
```

### 约束条件：
1. **仅输出 JSON**：不要包含任何 Markdown 代码块标记、注释或额外文字
2. **类型安全**：
   - `dialogue` 必须是有效的字符串
   - `expression` 必须是允许的表情值之一
   - `action` 必须是允许的动作值之一，或为 `null`
3. **格式有效**：输出的 JSON 必须可以被正确解析
4. **禁止动作描写**：`dialogue` 中禁止使用括号（如 `()`、`（）`、`[]`、`【】`、`{}` 等）或星号（`*`）括住的动作描写。例如禁止出现 `(微笑)`、`*叹气*`、`【思考】` 等内容。对话内容应只包含实际说出的文字
5. **分段策略**：根据语义和情感自然分段，每段对应一个表情。
6. **段落数量限制（重要）**：输出的 Segment 数量**最多不超过 4 个**。若内容较多，请合并或精简。
7. **单句原则（重要）**：每个 `dialogue` 必须只包含一句话，禁止将多句话塞进同一段。判断"一句话"的标准是：只包含一个完整的语意单元，以句号、问号、感叹号或省略号结尾。若想表达多句话的内容，必须拆分成多个 Segment。
8. **动作使用原则（重要）**：
   - `action` 是**必填字段**，不需要动作时设为 `null`（表示维持待机）
   - 仅在剧情或情绪确实需要时才指定非空的 `action`（如打招呼挥手、思考、惊讶回头、生气跺脚等）
   - 不要输出 `"idle"`，待机由 `null` 表示
"""


def build_performance_prompt(expressions: list[str], actions: list[str]) -> str:
    """Build the system prompt fragment that instructs the LLM how to format
    its output, using the given list of allowed expressions and actions.

    Args:
        expressions: The allowed expression names.
        actions: The allowed action names (typically derived from VRMA
            filenames, with ``idle`` excluded). When empty, the LLM is told
            there are no available actions and should omit the field.

    Returns:
        The formatted prompt string.
    """
    expressions_str = " | ".join(f'"{e}"' for e in expressions) if expressions else '"neutral"'
    actions_str = " | ".join(f'"{a}"' for a in actions) if actions else "never"
    return FORMAT_PROMPT.replace("{expressions}", expressions_str).replace(
        "{actions}", actions_str
    )
