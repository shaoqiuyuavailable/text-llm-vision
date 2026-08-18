# dsh-vision：DeepSeek Harness 场景级识图路由层（已归档）

> ## 🏁 项目归档（2026-08-18）：历史使命已完成
>
> 本项目的设计目标——**"场景级识图路由"**——已经全部以工程化形态并入
> **dsh-vision-router**（[ysr666/dsh-vision-router](https://github.com/ysr666/dsh-vision-router)）生态，
> 本项目转为只读归档，代码保留供追溯，新功能与维护请移步 dsh-vision-router。
>
> **概念落地轨迹**：
> - **#98 → #141** 本地视觉功能并入（local Ollama/LM Studio 双后端、instantDescribe、
>   vision_screenshot 截图——作者提交 #98，经维护者两轮"严格增量"审查后由维护者整合
>   经 #141 合入 main，#98 作 superseded 关闭）
> - **#136** 1+x 结构化 bootstrap（第一遍整体预判 + 后续按需深挖的复用起点）
> - **#142** v2 能力路由（维护者工程化；PR 描述与 docs 已注明概念来源含本项目早期工作）
> - **#177** freeCloudFirst + OCR 提示词强化（本仓库作者）
> - **#178** mixed 分路 / visionDepth 档位 / content_kind+mixed_of schema 收敛 / guidanceOverrides（本仓库作者）

---

## 与 dsh-vision-router 的关系（归档定稿）

**一句话：本项目是"场景级识图路由"的*概念起源与原型*，dsh-vision-router 是*工程化承接方*。**

| 维度 | dsh-vision（本项目，已归档） | dsh-vision-router（承接方） |
|------|------------------------------|-----------------------------|
| 角色 | **概念与原型来源**（2026-08-14/15 已有代码） | **工程化落地**（框架、工具链、维护） |
| 场景判定 | `scan → zoom → guess`：第一遍整体预判 + 后续按需深挖 | #136 结构化 bootstrap / #178 1+x 深挖引导 |
| 引擎选择 | `_route_engine` 场景→引擎路由表 | #142 能力路由 / #178 mixed 分支引导 |
| 精度控制 | PRECISION 档位（fast/standard/deep） | #178 visionDepth 档位（同名移植） |
| 混合图 | 候选列表 + 聚焦点 + 双分支 | #178 mixed 分路（≤2 分支封顶） |
| 结构化判定 | 视觉模型直接输出枚举（免启发式） | #178 content_kind / mixed_of schema 收敛 |
| 协作方式 | 设计/概念输入方 | 独立 PR 承接，接口稳定后由 #142 消费 |

**归属和分工**（区分*功能移植*与*设计思想继承*）：

- **功能移植**（功能/设计直接落地到 router）：
  - **#98 → #141（作者提交 → 维护者整合合入）**：local Ollama/LM Studio 双后端、
    instantDescribe 即时识别、vision_screenshot 桌面截图——dsh-vision 旧版功能层
    并入 router 的第一波；作者提交 #98 后经维护者两轮严格增量审查（10 + 5 点：
    fair-share 只限 local、auto-discovery 恢复、imageMemory 无界撤回、截图
    boot-time 注册、local dispatcher、bbox 坐标系、1+x 不叠 2+x 等），最终由
    维护者以当前 main 为 source of truth 整合、经 **#141** 合入，**#98 作
    superseded 关闭**（讨论与提交历史保留在 #98）
  - **#177（本仓库作者）**：freeCloudFirst 免费优先 + OCR 提示词强化——成本/速度妥协优化，作者基于 dsh-vision 工程实践的新实现（非旧功能搬移）
  - **#178（本仓库作者）**：mixed 分路、visionDepth 档位、content_kind/mixed_of schema 收敛、guidanceOverrides——**直接移植 dsh-vision 的功能设计**（`_build_branches` 双分支 / PRECISION 档位 / 结构化判定枚举 / 提示词模板化），附迁移说明文档
- **设计思想继承**（概念启发，工程独立实现）：
  - **#136（维护者）**：1+x 结构化 bootstrap——与本项目 `scan → zoom → guess` 骨架**同源**（第一遍整体预判 + 后续按需深挖），维护者独立工程化
  - **#142（维护者）**：v2 能力路由——维护者在 PR 描述与 `docs/v2-capability-routing.md` 注明"scene-aware routing 方向受你之前讨论和 dsh-vision 早期工作的启发"，工程实现独立
- **协作分工**（维护者 #142 评论区确认）：独立 PR 演进，`content_kind`/`mixed_of` 等判定接口稳定后由 #142 能力路由消费；本项目的判定结果可直接喂给 #142，衔接成本低

---

## 项目沿革（背景）

### 第一阶段（2026-08-15 前）：外接视觉插件
自带工具 + 识别引擎实现（describe_image / extract_text / locate_object / compare_images 等），
引擎随 visual-ds 基线封存（commit `0a34ad6`）。GUI 配置卡片、同图去重缓存、云端通道等
均在此阶段完成，详见 [CHANGELOG.md](CHANGELOG.md) 与 git 历史。该阶段的功能层
（本地 Ollama/LM Studio 后端、即时识别、桌面截图）经 **#98 → #141** 并入
dsh-vision-router（见上文归属和分工）。

### 第二阶段（2026-08-16 起）：重启为"场景级识图路由层"
不再自带工具、不再自带识别引擎，只做**模型的识图路由决策**（场景判定 + 引擎选择），
工具调用由其他插件实现。设计要点：
- **开关式前置路由**：总开关开启时在其他插件路由层之前介入；关闭时完全不介入
- **不影响操作工具**：只做"这张图该交给谁识别"的决策，不改写、不接管识别工具
- **技术栈**：dsh 插件机制（cordis + settings + tools 生态），识别后端走 OpenAI 兼容端点

### 第三阶段（2026-08-18）：使命完成，归档
第二阶段的设计已全部并入 dsh-vision-router（见上文关系表），本仓库归档只读。

---

## ⚠️ 注意事项（归档后）

- **仓库只读**：代码保留供追溯，不再接受新功能 PR；问题与需求请到
  [dsh-vision-router](https://github.com/ysr666/dsh-vision-router) 提
- **dsh 本体改动**：本项目曾修改 dsh 源码的四处改动已全部回退并废弃，见
  [docs/upstream-changes.md](docs/upstream-changes.md)
- **本地遗留**：`~/.dsh/profiles/*/node_modules/dsh-vision` 若仍存在为历史安装，
  可删除；当前 dsh 生态的视觉功能由 dsh-vision-router 提供

---

## 📁 文件（归档版）

| 文件 | 作用 |
|------|------|
| `src/index.ts` | 插件面：开关注册 + 前置路由钩子 |
| `python/vision_client.py` | 场景判定 + 引擎路由表（`_route_engine`，概念被 #178 移植） |
| `python/prompts.py` | 场景判定提示词 |
| `python/config_loader.py` | 配置读取（开关 + 后端端点） |
| `scripts/` | 安装 / 冒烟 / 回归测试 |
| `docs/upstream-changes.md` | dsh 本体改动记录（已废弃） |
| `docs/图像处理架构对照-迁移基线.md` | 与 router 的架构对照迁移基线 |

---

## 📜 相关 PR 索引

| PR | 内容 | 作者 |
|----|------|------|
| [ysr666/dsh-vision-router#98](https://github.com/ysr666/dsh-vision-router/pull/98) | 并入 dsh-vision 本地视觉功能（local 后端 / instantDescribe / 截图）；superseded，经 #141 合入 | shaoqiuyuavailable |
| [ysr666/dsh-vision-router#141](https://github.com/ysr666/dsh-vision-router/pull/141) | 维护者以 current main 为 source of truth 整合 #98（保留 #136 1+x / #139 Settings UX） | ysr666 |
| [ysr666/dsh-vision-router#136](https://github.com/ysr666/dsh-vision-router/pull/136) | 1+x 结构化 bootstrap（本项目 scan→zoom/guess 的复用起点） | ysr666 |
| [ysr666/dsh-vision-router#142](https://github.com/ysr666/dsh-vision-router/pull/142) | v2 能力路由（概念来源含本项目，维护者已注明） | ysr666 |
| [ysr666/dsh-vision-router#177](https://github.com/ysr666/dsh-vision-router/pull/177) | freeCloudFirst + OCR 提示词强化 | shaoqiuyuavailable |
| [ysr666/dsh-vision-router#178](https://github.com/ysr666/dsh-vision-router/pull/178) | mixed 分路 / visionDepth / schema 收敛 / guidanceOverrides | shaoqiuyuavailable |

---

*归档时间：2026-08-18。感谢所有使用与关注过本项目的朋友。*
