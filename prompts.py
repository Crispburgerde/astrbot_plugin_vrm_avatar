"""
此文件包含预设的提示词
"""

FORMAT_PROMPT = """
## 输出格式要求

你必须输出一段纯文本回复，将台词、表情与动作自然地交织在一起，让虚拟形象的表演更生动。不要输出 JSON、Markdown 代码块或任何额外说明，只输出表演内容本身。

### 格式规范

- `[expression]`：设置当前表情，**一直保持到下一次更换**。允许值：{expressions}
- `<action>`：触发一个肢体动作，作用于其后紧接的台词段。允许值：{actions}；不需要动作时省略
- 其余的文字都是角色实际说出的台词

标签可以**在文本中任意穿插**，根据情感与剧情的节奏随时切换表情、插入动作，不必拘泥于一句一行或固定结构。

### 输出示例

以下每一段都是回应**一条用户消息**的完整回复，台词、表情、动作在一次连贯的表达中自然交织。

示例 1（用户抱怨今天运气很差，回应时先共情再安慰）：
```
[sad] 啊，听起来今天确实不太顺……[neutral] 不过别太放在心上，偶尔倒霉一天很正常。<nod> 明天肯定会好起来的，我陪你聊聊，散散心。
```

示例 2（用户分享了一个好消息，回应时先惊讶后高兴）：
```
[surprised] 真的假的？！你竟然抽中了那个奖？[happy] 天哪太厉害了！<clap> 我早就说过你运气好的嘛，这下信了吧！
```

示例 3（用户说了一个让人生气的消息，回应时情绪由不解转为愤怒再回到安抚）：
```
[surprised] 等等，他居然没跟你商量就自己决定了？[angry] 这也太不尊重人了。<stomp> 换我早就发火了。[sad] 不过你先别急，气坏了自己不值当。[neutral] 坐下来好好想想怎么跟他谈，我帮你出主意。
```

### 约束条件：
1. **仅输出表演内容**：不要包含任何 Markdown 代码块标记、注释或额外说明
2. **标签必须合法**：
   - `[expression]` 必须是允许的表情值之一
   - `<action>` 必须是允许的动作值之一
3. **禁止动作描写**：台词文本中禁止使用括号（如 `()`、`（）`、`【】`、`{}` 等）或星号（`*`）括住的动作描写（如 `(微笑)`、`*叹气*`、`【思考】`）。所有舞台指示都通过 `[expression]` 与 `<action>` 标签表达，台词只包含实际说出的文字
4. **表情是状态**：`[expression]` 设置的表情会持续生效，直到下一个 `[expression]` 出现；因此连续的台词无需重复标注同一表情
5. **动作是瞬时**：`<action>` 只在其后紧接的台词段生效，之后自动回到待机，无需显式标注
6. **生动优先（重要）**：根据情感与剧情频繁切换表情、插入动作，让虚拟形象活灵活现；同一段台词中如果情绪有起伏，应及时切换 `[expression]` 或插入 `<action>`
7. **不要输出 `<idle>`**：待机由省略动作标签自然表示
"""


def build_performance_prompt(expressions: list[str], actions: list[str]) -> str:
    """Build the system prompt fragment that instructs the LLM how to format
    its output, using the given list of allowed expressions and actions.

    Args:
        expressions: The allowed expression names.
        actions: The allowed action names (typically derived from VRMA
            filenames, with ``idle`` excluded). When empty, the LLM is told
            there are no available actions and should omit the tag.

    Returns:
        The formatted prompt string.
    """
    expressions_str = (
        " | ".join(f'"{e}"' for e in expressions) if expressions else '"neutral"'
    )
    actions_str = " | ".join(f'"{a}"' for a in actions) if actions else "无可用动作"
    return FORMAT_PROMPT.replace("{expressions}", expressions_str).replace(
        "{actions}", actions_str
    )
