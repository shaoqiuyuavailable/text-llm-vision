#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""提示词与采样参数的「内置默认值」（v2）。当 config.json 缺失时回退到这些值；
config.json 存在且 `prompts_version >= 2` 时以它为准（hybrid 模式：内置默认 + 外部覆盖）。

v2 升级（对比 v1）：
- 大类 5 → 16（person/animal/plant/food/vehicle/machine/architecture/document/
  chart/diagram/map/screenshot/object/meme/scene/unknown），generic 保留作纯兜底
- 每类专属 zoom 提示词（17 个含 generic）
- 防幻觉规则内嵌：只写可见事实、模糊文字标 [无法识别]、身份/型号/品牌需 ≥2 证据、
  事实与推测分开、宁可不答不要错答
- guess 反证机制：候选 + 依据 + 反证 + 置信度
- 动态温度：config `modes` 表（--mode 覆盖 guess 温度）
- 温度调整：scan 0.3→0.15、spatial 0.1→0.0

混合方案：
- 主干：scan 判大类+小类 → zoom_{大类} 按类提取事实 → guess 大胆推测
- 组合分支：每个 zoom 提示词末尾带条件分支，兜底跨界元素

每个提示词条目是 {text, temperature}；无温度时用全局 ollama.temperature。
提示词刻意结构化，适配 7B~32B 小视觉模型。
"""

OLLAMA = "http://localhost:11434/api/generate"
VISION_MODEL = "qwen2.5vl"
TEMPERATURE = 0.5
TOP_P = 0.8
PRECISION = "fast"  # fast | standard | deep（识别精度档位）

PROMPTS_VERSION = 2  # config.json 的 prompts_version>=2 才叠加 scenes/prompts（防旧 config 压住 v2 基线）

# 动态温度模式表：--mode <名> 覆盖 guess 温度（对应 v2 用途区间中值）
MODES = {"rigorous": 0.3, "identity": 0.5, "military": 0.6, "anime": 0.7, "open": 0.8}

SCENES = {
    "person": {"sub": ["real_single", "real_group", "anime_character", "game_character",
                       "cosplay", "statue", "painting", "unknown"], "default_sub": "real_single"},
    "animal": {"sub": ["mammal", "bird", "reptile", "amphibian", "fish", "insect", "unknown"],
               "default_sub": "unknown"},
    "plant": {"sub": ["flower", "tree", "fruit", "vegetable", "succulent", "garden", "unknown"],
              "default_sub": "unknown"},
    "food": {"sub": ["dish", "beverage", "snack", "ingredient", "dessert", "tableware", "unknown"],
             "default_sub": "dish"},
    "vehicle": {"sub": ["car", "motorcycle", "truck", "bus", "train", "airplane", "ship",
                        "bicycle", "unknown"], "default_sub": "car"},
    "machine": {"sub": ["industrial", "household", "electronics", "tool", "construction", "unknown"],
                "default_sub": "unknown"},
    "architecture": {"sub": ["building", "interior", "landmark", "bridge", "ruins", "unknown"],
                     "default_sub": "building"},
    "document": {"sub": ["chat", "report", "code", "form", "table", "email", "unknown"],
                 "default_sub": "report"},
    "chart": {"sub": ["line", "bar", "pie", "scatter", "radar", "heatmap", "unknown"],
              "default_sub": "line"},
    "diagram": {"sub": ["flowchart", "org_chart", "network", "sequence", "gantt", "venn", "unknown"],
                "default_sub": "flowchart"},
    "map": {"sub": ["road", "satellite", "floor_plan", "topographic", "subway", "world", "unknown"],
            "default_sub": "road"},
    "screenshot": {"sub": ["software_ui", "website", "chat", "terminal", "error", "settings", "unknown"],
                   "default_sub": "software_ui"},
    "object": {"sub": ["product", "tool", "clothing", "furniture", "book", "toy", "unknown"],
               "default_sub": "unknown"},
    "meme": {"sub": ["template", "text_overlay", "reaction", "caption", "unknown"],
             "default_sub": "text_overlay"},
    "scene": {"sub": ["landscape", "cityscape", "indoor", "nature", "sky", "weather", "unknown"],
              "default_sub": "landscape"},
    "unknown": {"sub": [], "default_sub": ""},
    "generic": {"sub": [], "default_sub": ""},  # 纯兜底（解析层），sub 清空
}

# 防幻觉公共块：内嵌进每个 zoom 提示词
_ANTI_HALLUC = ("只写图中能看到的事实，禁止编造；看不清的文字标 [无法识别]；"
                "身份/型号/品牌需至少 2 个独立视觉证据，证据不足写 未知；"
                "区分「已看到」与「推测」。用中文回答。")

_PROMPTS = {
    "scan": {
        "text": "用一句简短中文描述图片主要内容。然后判断类别，严格输出两行：\n"
                "大类: person(人物/角色，含真人/动漫/游戏/Cosplay/雕像/画作)|animal(动物)|plant(植物)|"
                "food(食物)|vehicle(交通工具)|machine(机械设备)|architecture(建筑)|document(以文字为主的内容)|"
                "chart(数据图)|diagram(结构示意图/流程图)|map(地图)|screenshot(软件/网页/终端界面截图)|"
                "object(无生命物品)|meme(表情包/梗图)|scene(自然/城市/室内场景)|unknown(无法确定)\n"
                "小类: 仅当大类是以下情况才填，否则填 无。person小类=real_single|real_group|anime_character|"
                "game_character|cosplay|statue|painting|unknown；animal=pet|wild|bird|marine|insect|reptile|unknown；"
                "document=chat|report|code|form|table|email|unknown；chart=line|bar|pie|scatter|heatmap|unknown；"
                "diagram=flowchart|network|uml|technical|unknown；screenshot=website|mobile|desktop|game|terminal|"
                "code_editor|unknown；vehicle=car|aircraft|ship|train|spacecraft|unknown；machine=robot|industrial|"
                "electronic|tool|unknown；其它大类小类填 无。\n"
                "判定要点：代码不是图表(属document.code)；表格不是图表(属document.table)；纯表情包/梗图归meme；"
                "界面截图归screenshot；地图归map而非chart；数据图归chart，结构关系图归diagram。\n"
                "不要输出除这两行之外的任何内容。",
        "temperature": 0.15,
    },
    "zoom_person": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)人物/角色数量 2)身份信息(真人/动漫/游戏/雕像/Cosplay/画作，需区分) "
                "3)外貌特征(发型/发色/眼睛颜色/面部特征) 4)服装(颜色/款式/标志) 5)图片文字(清晰照抄，模糊标[无法识别]) "
                "6)logo/徽章/图案 7)背景环境 8)道具。\n最后输出：\n最可能身份:\n依据:\n置信度:\n若无法确认：\n身份:未知",
        "temperature": 0.3,
    },
    "zoom_animal": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)动物类别(哺乳/鸟/爬行/两栖/鱼/昆虫) 2)品种可能性(附证据) "
                "3)毛色/纹理 4)身体特征 5)姿态 6)表情 7)环境 8)图片文字。\n最后输出：\n最可能品种:\n依据:\n置信度:",
        "temperature": 0.3,
    },
    "zoom_plant": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)植物部位(花/叶/茎/果/根) 2)形态与颜色 3)栽培或野生 "
                "4)花盆/土壤/背景 5)图片文字。\n最后给出最可能的植物种类判断与依据。",
        "temperature": 0.3,
    },
    "zoom_food": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)菜品/食材/饮料类型 2)摆盘与容器 3)颜色/纹理/状态 "
                "4)菜单/标签文字(清晰照抄) 5)就餐场景。\n最后给出最可能的菜名/食物判断与依据。",
        "temperature": 0.3,
    },
    "zoom_vehicle": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)交通工具类型(车/摩托/卡车/公交/火车/飞机/船/自行车) "
                "2)品牌/型号(附证据) 3)颜色/牌照/外观 4)新旧与损坏状态 5)场景背景 6)图片文字。\n"
                "最后输出：\n最可能型号:\n依据:\n置信度:",
        "temperature": 0.3,
    },
    "zoom_machine": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)设备类型与用途 2)铭牌/型号文字(清晰照抄) 3)按钮/仪表/指示灯 "
                "4)运行状态 5)结构组件 6)环境背景。\n最后给出最可能的设备判断与依据。",
        "temperature": 0.3,
    },
    "zoom_architecture": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)建筑类型(楼宇/室内/地标/桥梁/遗址) 2)风格与年代线索 3)材质 "
                "4)立面/结构 5)标牌文字(清晰照抄) 6)环境。\n最后给出建筑判断与依据。",
        "temperature": 0.3,
    },
    "zoom_document": {
        "text": f"{_ANTI_HALLUC}\n文字识别规则：清晰文字直接复制原文，模糊文字标 [无法识别]，"
                "禁止根据上下文猜文字。\n逐项分析：1)文档类型(报告/聊天/代码/表单/表格/邮件) 2)标题(不存在写 无) "
                "3)主题 4)关键内容 5)OCR文字(清晰原文/模糊[无法识别]) 6)布局结构(标题区/正文区/表格区/代码区) "
                "7)特殊元素(代码提取语言与结构、表格提取行列关系、网页提取UI元素)。\n最后输出核心内容总结。",
        "temperature": 0.2,
    },
    "zoom_chart": {
        "text": f"{_ANTI_HALLUC}\n禁止编造不存在的数值。\n逐项分析：1)图表类型(折线/柱状/饼图/散点/热力图) "
                "2)坐标轴(X/Y 含义与范围) 3)数据趋势 4)关键数值 5)图例 6)标注文字(清晰照抄)。\n"
                "最后输出图表表达的核心结论。",
        "temperature": 0.3,
    },
    "zoom_diagram": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)示意图类型(流程图/结构图/网络图/时序图) 2)节点与连线关系 "
                "3)标签文字(清晰照抄) 4)整体结构结论。\n最后给出该示意图表达的结论。",
        "temperature": 0.3,
    },
    "zoom_map": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)地图类型(道路/卫星/平面图/地形/地铁/世界) 2)地区与方位 "
                "3)图例与比例尺 4)地名/标注(清晰照抄) 5)路线/路径。\n最后给出地图关键信息。",
        "temperature": 0.3,
    },
    "zoom_screenshot": {
        "text": f"{_ANTI_HALLUC}\n文字识别规则：清晰直接复制原文，模糊标 [无法识别]，禁止猜。\n"
                "这是软件/网页/终端界面截图，逐项照抄：1)窗口标题 2)菜单 3)按钮 4)标签 5)输入框 "
                "6)状态栏 7)弹窗/错误提示 8)其它 UI 元素。\n最后给出该界面的用途/性质判断。",
        "temperature": 0.3,
    },
    "zoom_object": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)主体对象 2)材质/颜色/尺寸线索 3)品牌/型号文字(清晰照抄) "
                "4)用途(附证据) 5)背景环境。\n最后给出物体判断与用途。",
        "temperature": 0.3,
    },
    "zoom_meme": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)模板名(若为经典梗图) 2)画面元素 3)叠加文字(原文照抄) "
                "4)笑点/含义(事实与解读分开标注)。\n最后给出该表情包的解读。",
        "temperature": 0.3,
    },
    "zoom_scene": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)场景类型(自然/城市/室内/天空/天气) 2)主体/前景/背景 "
                "3)光线与天气 4)时间 5)文字/标牌。\n最后给出场景判断。",
        "temperature": 0.3,
    },
    "zoom_unknown": {
        "text": "诚实报告：你无法将图片归入已知类别。1)列出图中可见的元素与文字 2)说明无法分类的原因"
                "(纯色/损坏/过曝/无特征等) 3)禁止编造类别、身份或型号。\n输出：\n可见元素:\n无法分类原因:\n结论:未知",
        "temperature": 0.3,
    },
    "zoom_generic": {
        "text": f"{_ANTI_HALLUC}\n逐项分析：1)主体对象 2)场景背景 3)画面中所有文字(清晰照抄/模糊[无法识别]) "
                "4)标志/图案/特殊元素 5)整体布局。\n最后给出图片最可能的用途或性质。",
        "temperature": 0.3,
    },
    "guess": {
        "text": "这是推测阶段。只能基于前面观察结果，禁止虚构人物身份/型号/品牌/文字。\n"
                "大胆推测图中主体最可能的身份、型号或含义，输出2-3个候选，格式：\n"
                "候选N:\n名称:\n依据:\n反证:\n置信度(高/中/低):\n"
                "反证=与候选矛盾的观察；反证强于证据时降级或判未知。"
                "身份/型号/品牌必须至少 2 个独立视觉证据，证据不足输出：\n未知:\n原因:\n"
                "宁可不答，不要错答。",
        "temperature": 0.5,
    },
    "spatial": {
        "text": "定位图中主要视觉元素。若模型支持边界框定位：输出 JSON 数组 "
                "[{\"name\":\"元素名\",\"bbox\":[x1,y1,x2,y2]}]，坐标 0-1000 归一化，"
                "按视觉重要性从高到低排序；若不支持 bbox：输出 [{\"name\":\"元素名\",\"location\":\"位置描述\"}]；"
                "若无法定位：输出 []。不要输出其它内容。",
        "temperature": 0.0,
    },
}

# 导出：config_loader 使用
PROMPTS = _PROMPTS
