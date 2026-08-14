#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""提示词与采样参数的「内置默认值」。当 config.json 缺失时回退到这些值；
config.json 存在时以它为准（hybrid 模式：内置默认 + 外部覆盖）。

混合方案：
- 主干：scan 判大类+小类 → zoom_{大类} 按类提取事实 → guess 大胆推测
- 组合分支：每个 zoom 提示词末尾带条件分支，兜底代码/表格/界面/地图/证件/表情包等跨界元素

每个提示词条目是 {text, temperature}；无温度时用全局 ollama.temperature。
提示词刻意短而结构化，适配 8B 小视觉模型。
"""

OLLAMA = "http://localhost:11434/api/generate"
VISION_MODEL = "qwen2.5vl"
TEMPERATURE = 0.5
TOP_P = 0.8
PRECISION = "fast"  # fast | standard | deep（识别精度档位）

SCENES = {
    "person": {"sub": ["anime", "real", "group"], "default_sub": "anime"},
    "animal": {"sub": [], "default_sub": ""},
    "document": {"sub": ["chat", "report", "code", "form", "table"], "default_sub": "report"},
    "chart": {"sub": [], "default_sub": ""},
    "generic": {"sub": ["screenshot", "landscape", "object"], "default_sub": "screenshot"},
}

_PROMPTS = {
    "scan": {
        "text": "用一句简短中文描述图片主要内容。然后判断图片类别，严格输出两行：\n大类: person(有真人或动漫角色)|animal(动物)|document(以文字为主要内容的文档/聊天/代码/表格)|chart(图形化数据展示，如折线柱状饼图散点图)|generic(其它，如界面截图/风景/物品/表情包)\n小类: 仅当大类是以下情况才填，否则必须填 无。person小类=anime(动漫角色)|real(真人)|group(多人合影)；document小类=chat(聊天记录)|report(文章报告)|code(代码)|form(表单)|table(表格)；generic小类=screenshot(软件/网页界面截图)|landscape(风景)|object(物品)|meme(表情包/梗图)。animal和chart没有小类，必须填无。\n判定要点：代码不是图表(属document.code)；表格不是图表(属document.table)；纯表情包/梗图归generic.meme而非person；界面截图归generic.screenshot而非document。\n不要输出除这两行之外的任何内容。",
        "temperature": 0.3,
    },
    "zoom_person": {
        "text": "逐项检查并回答：1)主体人物/角色的身份 2)头发/眼睛/服装颜色款式 3)画面中所有文字，有就原文照抄 4)logo、标志、图案 5)背景和道具。若图中含代码、表格、界面、地图、证件、表情包等特殊元素，额外提取对应信息。最后给出最可能的身份判断和依据。",
        "temperature": 0.3,
    },
    "zoom_animal": {
        "text": "逐项检查并回答：1)动物种类 2)毛色/花纹/身体特征 3)姿态和表情 4)环境和背景 5)画面中所有文字。若图中含代码、表格、界面、地图、证件、表情包等特殊元素，额外提取对应信息。最后给出最可能的品种或身份判断。",
        "temperature": 0.3,
    },
    "zoom_document": {
        "text": "逐项检查并回答：1)文档类型(报告/聊天/代码/表单/表格/邮件等) 2)标题和主题 3)关键要点和数据 4)所有文字尽量原文照抄 5)整体布局结构。若图中含代码、表格、界面、地图、证件等特殊元素，额外提取对应信息。最后给出这份文档的核心内容概述。",
        "temperature": 0.2,
    },
    "zoom_chart": {
        "text": "逐项检查并回答：1)图表类型(散点图/折线/柱状/饼图/地图/表格等) 2)坐标轴含义和范围 3)数据点/趋势/关键数值 4)图中所有文字和标注 5)颜色/分组含义。若图中含代码、界面等特殊元素，额外提取对应信息。最后给出图表反映的核心结论。",
        "temperature": 0.3,
    },
    "zoom_generic": {
        "text": "逐项检查并回答：1)主体对象是什么 2)场景和背景环境 3)画面中所有文字，有就原文照抄 4)标志/图案/特殊元素 5)整体布局。若图中含代码、表格、界面、地图、证件、表情包等特殊元素，额外提取对应信息。最后给出这张图片最可能的用途或性质。",
        "temperature": 0.3,
    },
    "guess": {
        "text": "这是推测环节。基于下面提取的事实特征、场景大类和小类，大胆推测图中主体最可能的身份、型号或含义。列出2-3个候选，每个说明判断依据和置信度(高/中/低)，按可能性从高到低排序。宁可多猜几个，不要只报事实；不确定就明确标注。",
        "temperature": 0.5,
    },
    "spatial": {
        "text": "定位图中所有有意义的元素（UI元素、图表、文字块、人物、物体等），用JSON输出每个元素的名称和边界框坐标。格式：[{\"name\":\"元素名\",\"bbox\":[x1,y1,x2,y2]}]。坐标用模型的grounding输出格式（如0-1000归一化或模型内部网格）；若模型不支持边界框定位，仅输出元素名称列表即可。按视觉显著程度排序。",
        "temperature": 0.1,
    },
}

# 导出：config_loader 使用
PROMPTS = _PROMPTS
