# 制作与交付

先安装命令行核心（源码仓库 `python -m pip install -e ./code`；使用自己选择的独立 Python 环境）。

```text
presentflow init runs/talk --paper paper.pdf
presentflow plan runs/talk
presentflow answer runs/talk --set language=en --set duration_minutes=15 --set template_preference=builtin_academic --set occasion=conference --set branding=text_only --set voice_mode=none
presentflow extract runs/talk
```

AI 助手阅读 `private/extracted` 和论文原页后，完成下面的结构化 `talk.json`。每页 `sources` 是私有证据，`notes` 只写朗读文本。

按用户时长安排页数与稿长，先用文字估算，再用首段试听实测语速；整场生成后按实际音频总长复核。差距明显时精简或补足内容，不只改变文件名中的“15 分钟”，也不要未经用户偏好把声音加速到不自然。

```json
{
  "title": "Research presentation",
  "slides": [
    {
      "title": "Research question",
      "bullets": ["A concise claim supported by the supplied paper"],
      "notes": "Hello everyone. Today I will explain our research question.",
      "sources": [{"page": 1, "detail": "Research question in the abstract"}]
    }
  ]
}
```

```text
presentflow compose runs/talk --spec talk.json --output output/talk.pptx
```

已有成稿 PPT 时 `compose` 默认只改逐页备注，要求 spec 页数与成稿一致，并比较各页渲染。`deck_action=restyle` 才按模板或新布局重新制片。

模板模式每页指定 `template_slide`、`updates`（形状名称→新文本）和 `retain_text_shapes`（明确保留的品牌/页脚文字）。先读取提取清单里的对象名称和 ID；也可直接用 `id:2` 作为键。程序会把已识别名称转为稳定 ID，避免 PowerPoint 打开文件时翻译默认形状名称。原生渲染器拒绝未替换且未明确保留的模板文本，防止旧论文内容混进新汇报。复杂分组、图表、公式由 AI 通过宿主的演示文稿能力处理；可把制作好的 PPT 用 `attach --deck` 导入。

内置简洁版式支持标题、要点、图片和原生表格，图片路径相对 spec；表格使用 `table: {"columns": [...], "rows": [[...]]}`。图片与表格不要同时挤在同一默认版式；需要复杂布局时使用演示文稿工具并逐页检查。标志可放 spec 顶层 `conference_logo` / `institution_logo`。这些必须是已取得的本地真实图片，不会自动访问 URL。

外部讲稿用 `## Slide 01: Title` 页标记（也支持 `第 1 页：标题`）；DOCX 必须含同样页标记。无页标记时先由 AI 对齐，再生成 MD。将 `script_source` 设为用户选定的 `external_script` 或 `deck_notes`，不能擅自处理冲突。

最终对语音装配使用 `assemble`。`--air` 才启用 profile 中指定的环境层。保存为新的普通 PPTX；已有文件不会覆盖。最终文件和逐页 MD 是用户交付物，`private/` 是本地任务证据。任务目录默认放 `runs/`，不进入源码归档。
