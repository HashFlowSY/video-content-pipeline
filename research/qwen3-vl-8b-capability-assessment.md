# Qwen3-VL-8B-Instruct 4bit 视频内容能力评估

调研日期：2026-08-02

评估对象：

- 原始模型：`Qwen/Qwen3-VL-8B-Instruct`
- 本地候选：`mlx-community/Qwen3-VL-8B-Instruct-4bit`
- 推理框架：`Blaizzy/mlx-vlm`
- 目标设备：M1 Pro、32 GB 统一内存

本文只使用一手来源：Qwen 官方仓库、模型卡和技术报告，Hugging Face
Transformers 的 Qwen3-VL 官方集成，MLX-VLM 官方仓库与 release，以及
mlx-community 模型卡和配置。

## 结论

**有条件支持。**

`Qwen3-VL-8B-Instruct` 足以作为首版的段级视觉语义引擎，当前没有证据要求
一开始就升级到 32B；但 `mlx-community` 4bit 版本尚不能被预先认定为最终合格
模型。它必须放在外部证据流水线之后使用，并通过真实样本 prototype。

正确定位：

- 负责理解已经采集到的稳定页面、PPT/文档、问题卡、连麦布局、内嵌媒体状态、
  图表，以及这些画面与字幕/OCR 的关系。
- 负责基于来源证据生成段级详细信息、摘要和语义断点候选。
- 不负责权威 OCR、媒体 PTS、完整事件采集、ASR、说话人或音频来源归因。
- 不把 0-4 小时视频直接交给 MLX-VLM 的原生 `--video` 路径。
- 不承诺 4bit 复现 Qwen 官方原始 8B 的 benchmark。

本线程已经排除了点击顺序、快捷键、细粒度实物动作还原和正式输出截图，因此
首版视觉范围主要是稳定且有信息价值的状态。这与 8B 的公开强项基本匹配。剩余
风险主要来自部署路径、量化差值和抽帧召回，而不是“8B 完全看不懂这些内容”。

## 证据边界

以下四个判断必须分开：

1. Qwen 官方原始 8B 是否具有相应能力。
2. MLX-VLM 是否存在 Qwen3-VL 图像、多图和视频代码路径。
3. mlx-community 4bit 是否保持了原始模型质量。
4. M1 Pro 32 GB 是否能以计划中的帧数、分辨率和段长稳定运行。

官方资料只对前两项提供了较强证据。后两项必须实测。

公开 benchmark 分数也不能直接换算成真实视频的 OCR CER、数字正确率、视觉
事实召回率、语义分段正确率或摘要幻觉率。

## 一、原始 8B 的官方能力

### 1. 官方能力范围

Qwen 官方将 Qwen3-VL 描述为支持：

- 原生 256K 上下文，可扩展到 1M。
- 长视频理解、视频 OCR 和秒级事件定位。
- 多图理解。
- 文档解析和 32 种语言 OCR。
- PC 和移动端 GUI 理解。
- 图表、STEM 和多模态推理。

来源：

- [Qwen3-VL 官方仓库](https://github.com/QwenLM/Qwen3-VL)
- [Qwen3-VL-8B-Instruct 官方模型卡](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)
- [官方视频理解 cookbook](https://github.com/QwenLM/Qwen3-VL/blob/96588727e44c78b25ba03ea03b8e12f7e64fd0da/cookbooks/video_understanding.ipynb)

这些是模型家族的能力声明，不代表 8B 4bit 在 M1 Pro 上能一次读取 4 小时视频并
完整回忆所有细节。

技术报告中“30 分钟 100%、约 2 小时 99.5%”的 Needle-in-a-Haystack 结果测试
对象是 `Qwen3-VL-235B-A22B-Instruct`，不是 8B。不能用该结果为 8B、MLX 或
4bit 的长视频完整性背书。[Qwen3-VL 技术报告](https://arxiv.org/pdf/2511.21631)

### 2. 8B-Instruct 公开基准

下表取自技术报告的小模型表格，仅列 `Qwen3-VL-8B-Instruct`：

| 类别 | Benchmark | 分数 |
|---|---|---:|
| 文档 | DocVQA test | 96.1 |
| 文档 | InfoVQA test | 83.1 |
| 图表 | ChartQA test | 89.6 |
| OCR | OCRBench | 896 |
| OCR | OCRBench v2 EN / ZH | 65.4 / 61.2 |
| OCR | CC-OCR | 79.9 |
| 长文档 | MMLongBench-Doc | 47.9 |
| 多图 | BLINK | 69.1 |
| 多图 | MuirBench | 64.4 |
| 视频 | MVBench | 68.7 |
| 视频 | Video-MME（无字幕） | 71.4 |
| 长视频 | MLVU M-Avg | 78.1 |
| 长视频 | LVBench | 58.0 |
| 时序定位 | Charades-STA mIoU | 56.0 |
| 视频推理 | VideoMMMU | 65.3 |
| GUI | ScreenSpot Pro | 54.6 |
| GUI | OSWorldG | 58.2 |
| GUI | AndroidWorld | 47.6 |
| GUI | OSWorld | 33.9 |

来源：[Qwen3-VL 技术报告](https://arxiv.org/pdf/2511.21631)及
[官方 8B 模型卡中的性能表](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)。

这些结果说明：

- 8B 对清晰文档、常见 OCR 和图表有较强基础。
- 8B 确实具有多图、视频和 GUI 理解能力。
- 长视频、GUI、复杂图表推理和精确时序都远未达到可取消外部校验的程度。
- 8B 的 HallusionBench 为 61.1，不能将模型视为天然无幻觉的事实抽取器；该
  分数也不能直接解释成项目中的“幻觉率”。

### 3. 官方视频评测仍有采样上限

技术报告的视频评测设置为：

- 每个视频最多 2,048 帧。
- 总视频 token 不超过 224K。
- VideoMMMU 和 MMVU 每帧最多 768 token，其余 benchmark 每帧最多 640。
- Charades-STA 使用 4 fps，其余视频 benchmark 使用 2 fps。

来源：[技术报告 Video Understanding 章节](https://arxiv.org/pdf/2511.21631)。

因此，官方长视频成绩也建立在采样和 token 预算内，不能外推为“任意短暂画面
都能被召回”。

### 4. 该模型不处理视频音频

Qwen3-VL 的官方接口输入图像/视频帧和文本；视频 processor 处理帧和 metadata，
不把音频波形送入模型。

来源：

- [Qwen 官方视频 quickstart](https://github.com/QwenLM/Qwen3-VL)
- [Transformers 4.57.0 Qwen3VLProcessor](https://github.com/huggingface/transformers/blob/v4.57.0/src/transformers/models/qwen3_vl/processing_qwen3_vl.py)

所以直播说话人、问答角色、主播与内嵌视频混音归因必须来自 ASR、diarization
和额外的归因模块。

## 二、4bit MLX 权重能确定什么

### 1. 已知事实

mlx-community 模型卡只明确说明：

- 权重由 `Qwen/Qwen3-VL-8B-Instruct` 转换为 MLX。
- 转换使用 `mlx-vlm 0.3.4`。
- 示例只演示单图推理。

来源：[4bit 模型卡，固定 revision](https://huggingface.co/mlx-community/Qwen3-VL-8B-Instruct-4bit/blob/defcdea7cc7a4b0858fea563cbbce171d328e457/README.md)。

该 revision 的配置是：

- `bits=4`
- `group_size=64`
- `mode=affine`
- 上下文位置上限 262,144
- 视频默认 2 fps
- 视频最多 768 帧

来源：

- [config.json](https://huggingface.co/mlx-community/Qwen3-VL-8B-Instruct-4bit/blob/defcdea7cc7a4b0858fea563cbbce171d328e457/config.json)
- [video_preprocessor_config.json](https://huggingface.co/mlx-community/Qwen3-VL-8B-Instruct-4bit/blob/defcdea7cc7a4b0858fea563cbbce171d328e457/video_preprocessor_config.json)

权重仓库约 5.77 GB。[模型文件列表](https://huggingface.co/mlx-community/Qwen3-VL-8B-Instruct-4bit/tree/defcdea7cc7a4b0858fea563cbbce171d328e457)

### 2. 没有量化质量保证

该模型卡没有公布：

- 量化后的 OCR、图表、视频、多图或 GUI benchmark。
- 与原始 8B 的逐项差值。
- 校准集和域内误差。
- M1 Pro 的速度、峰值内存或最大稳定输入。

所以 Qwen 官方 8B 分数不能直接记到 MLX 4bit 名下。Qwen 团队也没有为该
mlx-community artifact 提供官方质量保证。

MLX-VLM 0.3.4 的标准转换代码会跳过 `vision_tower`，量化其他适用线性层；
但模型卡没有记录实际转换命令。这只能说明标准实现路径，不能充当 artifact
质量认证。

来源：

- [MLX-VLM v0.3.4 convert.py](https://github.com/Blaizzy/mlx-vlm/blob/v0.3.4/mlx_vlm/convert.py)
- [v0.3.4 skip_multimodal_module](https://github.com/Blaizzy/mlx-vlm/blob/v0.3.4/mlx_vlm/utils.py)

可用的同尺寸回退候选是
[`mlx-community/Qwen3-VL-8B-Instruct-8bit`](https://huggingface.co/mlx-community/Qwen3-VL-8B-Instruct-8bit)，
仓库约 9.87 GB；原始权重仓库约 17.53 GB。8bit 同样没有量化后 benchmark，
但可用于判断 4bit 是否出现明显域内退化。

## 三、MLX-VLM 支持状态与风险

### 1. 有 Qwen3-VL 图像、多图和视频代码路径

MLX-VLM v0.3.4 加入并修复了 Qwen3-VL；v0.5.0 又合入 Qwen 系列无 PyTorch
视频 processor 和 chunked-prefill RoPE 修复。当前源码包含：

- `Qwen3VLVideoProcessor`
- `pixel_values_videos`
- `video_grid_thw`
- 多图逐图处理和 grid 拼接

来源：

- [MLX-VLM v0.3.4](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.3.4)
- [MLX-VLM v0.5.0](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.5.0)
- [当前 Qwen3-VL processor](https://github.com/Blaizzy/mlx-vlm/blob/f47a3cedbc709f2c1d25eded3cc39405c7a88742/mlx_vlm/models/qwen3_vl/processing_qwen3_vl.py)
- [当前 Qwen3-VL model](https://github.com/Blaizzy/mlx-vlm/blob/f47a3cedbc709f2c1d25eded3cc39405c7a88742/mlx_vlm/models/qwen3_vl/qwen3_vl.py)

不过，当前 README 的 direct-video 支持清单仍没有列 Qwen3-VL，与源码不一致。
这更像文档滞后，但也意味着不能把 direct-video 视为已经清楚承诺并完整验证的
产品路径。[MLX-VLM Video Understanding](https://github.com/Blaizzy/mlx-vlm/blob/f47a3cedbc709f2c1d25eded3cc39405c7a88742/README.md#video-understanding)

### 2. 运行时必须使用近期版本并固定组合

Qwen3-VL 视觉路径近几个 release 修复过会直接影响本项目的问题：

- v0.6.3：修复 chunked prefill 时 DeepStack 视觉特征错位；旧实现会导致高
  分辨率或多图细节丢失、OCR 退化或重复输出。
- v0.6.6：修复 Qwen3-VL 的 PIL 帧列表和 channel-last 视频输入。
- v0.6.8：修复 Qwen 系列 chunked-prefill 的 MRoPE position slicing。

来源：

- [v0.6.3](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.6.3)
- [v0.6.6](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.6.6)
- [v0.6.8](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.6.8)

因此不能因为模型卡写“converted with 0.3.4”就用 0.3.4 执行正式任务。应固定
v0.6.8 或经 prototype 验证的更新版本，并固定模型 revision。

### 3. MLX 视频路径未复刻官方逐帧时间戳构造

这是最大的实现风险。

Transformers 4.57.0 的官方 Qwen3VLProcessor 会：

1. 读取 `video_metadata.frames_indices` 和 `video_metadata.fps`。
2. 计算 temporal patch 的真实时间。
3. 在每组视频视觉 token 前插入类似 `<12.3 seconds>` 的文本时间戳。

来源：[官方时间戳构造代码](https://github.com/huggingface/transformers/blob/v4.57.0/src/transformers/models/qwen3_vl/processing_qwen3_vl.py#L196-L234)。

当前 MLX Qwen3VLProcessor 只按 `video_grid_thw` 展开视频 token，未见等价的
逐帧文本时间戳插入，也没有消费 `video_metadata.frames_indices`。

来源：[MLX video token 展开代码](https://github.com/Blaizzy/mlx-vlm/blob/f47a3cedbc709f2c1d25eded3cc39405c7a88742/mlx_vlm/models/qwen3_vl/processing_qwen3_vl.py#L620-L679)。

Qwen 技术报告将 textual timestamp alignment 列为视频能力升级的核心机制。
因此，当前 MLX direct-video 路径不能直接继承官方的秒级定位或视频 benchmark。
具体下降幅度未知，必须 prototype。

### 4. MLX 原生 loader 不保留源 PTS

当前 `load_video`：

- 使用 OpenCV `VideoCapture`。
- 读取总帧数和 FPS。
- 按帧号均匀 `linspace` 采样。
- 只返回帧数组和计算后的 sample FPS。
- 不返回逐帧源 PTS。

来源：[MLX-VLM load_video](https://github.com/Blaizzy/mlx-vlm/blob/f47a3cedbc709f2c1d25eded3cc39405c7a88742/mlx_vlm/utils.py#L1592-L1654)。

它不足以成为 VFR、非零起始 PTS 或音视频起点不同文件的权威时间轴。正式路径
必须由 FFmpeg/PyAV 按源 PTS 抽帧。

### 5. 4 小时不能整段输入

MLX 配置默认 2 fps、最多 768 帧。4 小时理论需要 28,800 帧，最终会被压到
768 帧，即平均约 18.75 秒保留一帧。该计算来自配置和均匀采样源码，不是性能
实测。

因此，短 PPT 页面、问题卡、弹窗、快速切换和短内嵌片段可能完全不可见。外部
自适应抽帧和分段不是优化项，而是正确性要求。

## 四、逐项能力判断

| 已确认需求 | 判断 | 正式实现边界 |
|---|---|---|
| PPT、文档和页面文字 | 有条件可做 | RapidOCR 提供原文与坐标；VLM 负责页面结构、图文关系和讲解融合 |
| 网页、软件稳定状态 | 有条件可做 | 外部变化检测先捕获状态；不承诺操作轨迹或未采到的瞬时状态 |
| 直播连麦布局 | 部分可做 | 可描述宫格、画中画、可见昵称和问题卡；身份与语音归属依赖 diarization/OCR |
| 内嵌视频双层归因 | 只能 partial | 外部检测播放器区域、全屏状态和音频线索；混音或证据不足输出 `unknown` |
| 快速短暂视觉变化 | 不可原生保证 | 先用场景、局部帧差和 OCR 变化高频捕获；未采样事件无法恢复 |
| 图表和数字 | 适合解释，不适合做真值 | OCR 提取数字、单位、轴和图例；VLM 解释趋势；冲突进入复核 |
| 模型语义分段 | 可提出候选 | 输入对齐 cue、视觉事件和 OCR；只返回 evidence/cue ID，不自由生成秒数 |
| 来源内详细信息与摘要 | 可做但需校验 | 每条事实引用 `word_id`、`ocr_id` 或 `frame_id`；无支持内容删除或标不确定 |

### PPT、文档和页面索引

8B 的 DocVQA、OCRBench 和 ChartQA 支持其作为页面语义模型。正式流程仍需：

- 以 RapidOCR 或等价确定性 OCR 保存原文。
- 对小字、表格、图表和画中画使用高分辨率 crop。
- 由外部页面哈希/OCR 状态维护 `visual_page_id` 和重复出现区间。
- 页面标题、数字和关键原句不得只由 VLM 生成。

### 网页和软件稳定状态

模型可以理解被采集到的稳定截图。本项目已明确不还原点击、快捷键或操作步骤，
显著降低了难度。仍不能承诺所有界面状态召回，因为那取决于外部采样，而不是
VLM 的语言能力。

### 直播连麦

VLM 可以消费宫格、画中画、昵称和问题卡，但没有官方 8B benchmark 证明它能
长期把每句话归给正确窗口。默认匿名 speaker、证据充分才实名的规则仍然必要。

### 内嵌视频

没有官方 benchmark 直接验证主播画面、播放器区域和两路声音的持续双层归因。
模型应在外部 pipeline 已经分层后生成 `container_context` 与
`embedded_content`。混音、画中画遮挡或全屏切换证据不足时维持
`partial/unknown`。

### 快速变化

官方视频 benchmark 通常为 2 fps，MLX 长片还会进一步稀疏采样。即使模型很强，
也无法恢复没有输入的帧。首版只能承诺经过外部事件采集器实际捕获的变化。

### 图表和数字

ChartQA 89.6 说明能力较强，但不是 100% 数字准确。坐标轴、单位、小数点、
百分号和颜色图例均可能产生高影响错误。数字必须能在 OCR、字幕或画面证据中
逐字找到，否则进入 `review-needed`。

### 语义分段

没有公开 benchmark 对应本项目的“模型自主判断内容断点”。模型只能提出边界；
正式断点必须落在合法 cue/evidence 边界。技术 chunk 不能变成内容边界，所有
证据必须且只能归属一个内容段。

### 来源内摘要

8B 能生成摘要，但“只来自来源”不是天然保证。正式实现应先提取结构化事实，再
生成摘要；每条确定性事实必须附 evidence ID，程序还要检查引用是否真正支持
该结论，而不只是“引用存在”。

## 五、推荐架构

不要采用“模型直接读取完整视频”的单体方案：

1. FFmpeg/PyAV 按真实 PTS 解码。
2. 场景变化、局部帧差、OCR 变化和内容状态机选择候选帧。
3. RapidOCR 保存可见文字、坐标和置信信息。
4. 每帧绑定 `frame_id + source_us + part_id`。
5. ASR、对齐和 diarization 提供语音证据。
6. 向 Qwen3-VL 提交小规模、带时间标签的段级多图，以及 OCR、字幕和上下文。
7. 模型只输出结构化视觉事实、段落候选和 evidence 引用。
8. pipeline 完成事实支持检查、唯一归属、覆盖审计和正式时间映射。

正式路径默认采用“外部真实 PTS 抽帧 + 多图 + 显式 frame ID/时间标签”。若要
使用 MLX 原生 video token，先实现与官方 Qwen3VLProcessor 等价的 timestamp
adapter，并完成 token 级和输出级对照。

## 六、必须完成的 prototype

### A. 运行时正确性

固定：

- `mlx-vlm 0.6.8` 或经验证的精确更新版本。
- 4bit revision `defcdea7cc7a4b0858fea563cbbce171d328e457`。
- prompt、输出 schema、temperature、prefill 参数。

验证单图、多图、PIL 帧列表和原生 video 是否能稳定运行；记录高分辨率、多图
输入的空输出、视觉丢失、重复循环、峰值内存和耗时。

### B. 三条输入路径对照

对同一批短视频比较：

1. MLX 原生 `--video`。
2. 外部真实 PTS 抽帧 + 多图 + 明确时间标签。
3. 实现官方 timestamp-token 行为后的 MLX adapter。

比较短事件召回、时间定位、OCR/图表数字、重复输出和语义断点。在证明前，正式
架构默认使用第 2 条。

### C. 真实样本

至少覆盖：

- 中文 PPT/文档讲解。
- 网页或软件稳定状态录屏。
- 直播连麦宫格、昵称和问题卡。
- 主播播放内嵌视频并评论。
- 含 0.25、0.5、1、2 秒短弹窗/短切换的压力样本。
- 含坐标轴、小数、百分比和单位的图表。

每类需要人工标注关键视觉事实、关键数字、OCR 原文、页面/布局/内嵌状态区间、
内容段边界和不应出现的事实。

### D. 建议验收门槛

以下是项目工程门槛，不是官方指标：

1. 至少 200 条人工标注的关键视觉事实，每类不能只用一条视频。
2. OCR、规则和 VLM 融合后的关键视觉事实 precision 不低于 98%，recall 不低于
   95%；无证据却写成确定事实的数量为 0。
3. 人名、标题、日期、数值、单位和百分比必须 exact match，或自动进入
   `review-needed`。
4. 4bit 相比同输入、同提示词的 8bit，关键事实 F1 下降不超过 2 个百分点，
   且不得新增未被拦截的关键数字、实体或来源归因错误；否则正式默认改为 8bit。
5. 分别测量 0.25、0.5、1、2 秒事件召回。只承诺达到至少 95% 召回率的最短
   时长档位，不能预先承诺所有短事件。
6. 内嵌媒体和连麦归因只自动发布高置信结果，precision 门槛 95%；证据不足输出
   `unknown/partial`，不得靠猜测提高 recall。
7. 语义分段满足证据覆盖 100%、重复归属 0、越界 0；允许一个字幕 cue 偏差时，
   候选边界 F1 建议不低于 0.85。
8. M1 Pro 32 GB 上按计划最大段级输入连续运行不得 OOM；建议单模型阶段峰值统一
   内存控制在约 24 GB 内。最终帧批量必须由实测决定。

### E. 模型选择规则

- 保留 8B，不继续缩到 4B；当前 OCR、图表、来源归因和摘要约束没有足够缩模
  余量。
- 4bit 通过域内门槛后才成为默认。
- 4bit 不通过时先切换同一 8B 的 8bit，而不是放宽事实质量门槛。
- 原始权重只用于小样本对照或诊断，不作为 4 小时任务的常驻配置。
- 无论 4bit 还是 8bit，都保持“段级语义引擎”定位。

## 七、可承诺与不可承诺

### 进入 prototype 后可作为首版目标

- 经 OCR 和高分辨率帧确认的 PPT、文档和页面信息。
- 被外部变化检测捕获的网页/软件稳定状态。
- 连麦布局、问题卡和可见昵称，身份不足时保持匿名。
- 基于已分层证据的内嵌内容与主播上下文，默认允许局部 partial。
- 经 OCR/规则复核的图表解释与关键数字。
- 基于 evidence 的模型语义分段候选和来源内摘要。

### 当前不应承诺

- 4bit 与官方原始 8B 质量等价。
- MLX 原生 video 路径复现官方秒级定位。
- 4 小时视频单次输入或所有短暂事件完整召回。
- VLM 独立完成逐字 OCR 和关键数字真值。
- 混音环境下自动可靠区分主播和内嵌视频声音。
- 无程序校验时生成零幻觉摘要。
- 模型分段边界每次绝对一致。

## 最终判断

| 命题 | 结论 |
|---|---|
| 原始 8B 有 OCR、图表、多图、视频和 GUI 基础 | 官方证据充分 |
| 当前范围需要立即升级到 32B | 没有证据 |
| 4bit 已被证明复现官方 8B benchmark | 否 |
| MLX 有 Qwen3-VL 图像、多图和视频源码 | 是 |
| MLX direct-video 等价于官方 Qwen3 视频 processor | 否，时间戳构造存在关键差异 |
| 8B 4bit 可独立完成 4 小时完整视觉提取 | 不可承诺 |
| 8B 4bit 可作为外部证据 pipeline 后的段级语义引擎 | 值得 prototype |
| PPT、稳定页面、图表和来源内摘要可作首版目标 | 可以，但必须有 OCR、PTS 和 evidence validation |
| 快速事件、直播音画归属、内嵌双层归因可直接标 `full` | 不可以 |

## 来源

### Qwen

- [Qwen3-VL 官方仓库](https://github.com/QwenLM/Qwen3-VL)
- [Qwen3-VL-8B-Instruct 模型卡](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)
- [Qwen3-VL Technical Report](https://arxiv.org/pdf/2511.21631)
- [Qwen3-VL 视频理解 cookbook](https://github.com/QwenLM/Qwen3-VL/blob/96588727e44c78b25ba03ea03b8e12f7e64fd0da/cookbooks/video_understanding.ipynb)
- [Transformers 4.57.0 Qwen3VLProcessor](https://github.com/huggingface/transformers/blob/v4.57.0/src/transformers/models/qwen3_vl/processing_qwen3_vl.py)

### MLX-VLM

- [MLX-VLM 官方仓库](https://github.com/Blaizzy/mlx-vlm)
- [v0.3.4](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.3.4)
- [v0.5.0](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.5.0)
- [v0.6.3](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.6.3)
- [v0.6.6](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.6.6)
- [v0.6.8](https://github.com/Blaizzy/mlx-vlm/releases/tag/v0.6.8)

### mlx-community

- [Qwen3-VL-8B-Instruct-4bit](https://huggingface.co/mlx-community/Qwen3-VL-8B-Instruct-4bit)
- [Qwen3-VL-8B-Instruct-8bit](https://huggingface.co/mlx-community/Qwen3-VL-8B-Instruct-8bit)
- [4bit 固定 revision 配置](https://huggingface.co/mlx-community/Qwen3-VL-8B-Instruct-4bit/tree/defcdea7cc7a4b0858fea563cbbce171d328e457)

## 调研操作

- 未安装任何依赖、插件、应用或模型。
- 未下载任何模型权重。
- 未运行候选模型。
- 只读取官方网页、仓库源码、模型卡、配置和技术报告。
- 写入内容仅为本报告及其 `research/` 目录。
