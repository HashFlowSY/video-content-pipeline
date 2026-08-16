# 阶段 11 模型选型调研：逐能力候选优劣势矩阵

调研日期：2026-08-16

目标设备（已确认为最终测试机）：**Apple M1，16 GiB 统一内存**，macOS，磁盘余量约 307 GB。
资源硬约束（2026-08-16 拷问已锁定）：**单阶段峰值内存包络 12 GiB**，重模型阶段独立子进程串行执行。

本文只使用一手来源（官方 GitHub 仓库、Hugging Face 模型卡、PyPI、官方文档），
由四轮独立调研合成；社区数据或推算值均显式标注。每个能力给出候选矩阵、
推荐与风险，文末是供维护者拍板的决策清单。

**本文只做选型论证，不下载、不执行任何模型或依赖。** 所有下载在拍板后按
逐模型下载计划单独确认（锁 revision + 哈希）。

---

## 0. 总览：推荐组合一览

| 能力 | 推荐模型 | 推荐运行时 | 权重体积 | 预计峰值内存 |
|---|---|---|---|---|
| `asr_primary` | Qwen3-ASR-1.7B（mlx-community 8bit） | mlx-audio | 2.46 GB | ~3 GiB |
| `asr_review` | whisper-large-v3（mlx-community fp16） | mlx-whisper | ~3 GB | ~4-5 GiB |
| `forced_alignment` | Qwen3-ForcedAligner-0.6B（mlx-community 8bit） | mlx-audio | 1.27 GB | ~2 GiB |
| `vad` | silero-vad v6.2.1（onnx） | onnxruntime（直接携带 .onnx，免 torch） | ~2 MB | 可忽略 |
| `diarization` | pyannote-segmentation-3.0 ONNX + 3D-Speaker CAM++ zh-en | sherpa-onnx | ~35 MB | <1 GiB |
| `ocr_primary` | RapidOCR 内置 PP-OCRv6 small（中英） | rapidocr==3.9.2 + onnxruntime | 随 wheel（27.3 MB） | <1 GiB |
| `text_semantics` | Qwen3-4B-Instruct-2507（mlx-community 4bit） | mlx-lm | 2.26 GB | ~3 GiB（受 KV 上限控制） |

推荐组合下载总量约 **9 GB**；任一单模型阶段峰值都在 12 GiB 包络内且余量充足。
全链 license：Apache-2.0 / MIT，无凭证门控，无付费。

---

## 1. `asr_primary` — Qwen3-ASR

### 事实基础

- `Qwen/Qwen3-ASR-1.7B` 开源权重确认存在（Apache-2.0，safetensors 共 4.7 GB，
  30 语种 + 22 中文方言，内置语种识别；`-hf` 变体需 transformers ≥ 5.13）。
  另有 `Qwen/Qwen3-ASR-0.6B`。
  来源：https://huggingface.co/Qwen/Qwen3-ASR-1.7B （revision `7278e1e7…`）
- mlx-community 量化梯（1.7B/0.6B 各有 4/5/6/8bit/bf16）确认存在；
  1.7B-8bit 为 2.46 GB。来源：https://huggingface.co/mlx-community/Qwen3-ASR-1.7B-8bit
- 官方模型卡基准（1.7B vs 0.6B）：Librispeech clean/other WER 1.63/3.38 对
  2.11/4.55；WenetSpeech net/meeting 4.97/5.88 对 5.97/6.88；Fleurs-zh 2.41 对
  2.88。差距一致但温和（中文约 1 个绝对点）。
- 量化损失（社区数据，英文基准）：8bit ≈ +0.04 WER（近无损），4bit ≈ +0.43/+1.38。
  **中文量化损失无公开数据**——原型阶段必须自测。
  来源：https://github.com/moona3k/mlx-qwen3-asr
- **官方 `qwen-asr` 包在本机不可行**：CUDA 导向、硬依赖 gradio/flask、
  钉死 transformers==4.57.6、无 macOS/MPS 文档路径、无本地长音频切分。
  来源：https://github.com/QwenLM/Qwen3-ASR/blob/main/pyproject.toml
- **没有任何官方方案支持本地小时级长音频**：所有现实路线都假设调用方自行
  基于 VAD 分段——这与本管线既有的 VAD 能力和对齐器 5 分钟窗口天然吻合
  （见 §8 架构含义）。

### 模型候选矩阵

| | Qwen3-ASR-1.7B-8bit（推荐） | Qwen3-ASR-0.6B-8bit | 1.7B-4bit |
|---|---|---|---|
| 权重 | 2.46 GB | ~0.7 GB | ~1.3 GB（推算） |
| 中文质量 | 基准最优 | 各基准落后约 1 点 | 8bit 近无损、4bit 英文 +0.4~1.4 WER，中文未知 |
| 速度 | 慢于 0.6B | 最快 | 快于 8bit |
| 风险 | 无突出 | 长视频累积错误更多 | 中文量化损失无数据 |

### 运行时候选矩阵

| | mlx-audio（推荐） | mlx-qwen3-asr（moona3k） |
|---|---|---|
| 归属 | Blaizzy/mlx-audio，MIT，PyPI 0.4.8 | 社区单维护者，Apache-2.0，~180 星 |
| 官方列名支持 | Qwen3-ASR 与 ForcedAligner 均在支持列表 | 否（但有 462 测试 + 对官方后端的 parity 门禁工件） |
| 覆盖能力 | **一个运行时同时服务 asr_primary 和 forced_alignment** | ASR + 对齐 + 自带 SRT/长音频切分/流式 |
| 依赖 | 无 torch；多用途大包（含 TTS/STS 冗余面） | 无 torch；专用面小 |
| 风险 | Qwen3 支持较新；无内置长音频切分（本管线不需要它有） | 单维护者停更风险；SRT/切分功能与本管线自有职责重叠 |

**推荐**：模型 `mlx-community/Qwen3-ASR-1.7B-8bit`，运行时 `mlx-audio`。
理由：一个运行时覆盖两个能力，依赖面最小化；本管线自己做 VAD 分段、
时间轴和产出格式，mlx-qwen3-asr 的附加功能全部用不上，反而引入职责重叠。
原型若发现 mlx-audio 的 Qwen3 路径质量或稳定性不行，mlx-qwen3-asr 是
现成退路（其 parity 工件降低了替换验证成本）。

---

## 2. `asr_review` — whisper-large-v3

### 事实基础

- `openai/whisper-large-v3`：Apache-2.0，1.55B 参数，99 语种。
  来源：https://huggingface.co/openai/whisper-large-v3
- M1 路线 A：`mlx-whisper`（PyPI 0.4.3，MIT，依赖面小，需 ffmpeg，
  支持 word_timestamps）+ `mlx-community/whisper-large-v3-mlx`（MIT）。
  MLX 走 GPU；社区口径 M1 上 large-v3 约 0.9-1x 实时（非一手数据）。
- M1 路线 B：`faster-whisper`（CT2 int8 ≈ 3 GB，自带 silero VAD 过滤）——
  但 **CT2 在 macOS 上仅 CPU、无 Metal**（k2 issue #911 确认），小时级音频最慢。
- 幻觉声誉（多方确认）：large-v3 长音频有重复循环与静音幻觉倾向，中文弱于
  英文。**作为 review（第二意见）模型可接受**：本管线只在可疑区间上喂
  VAD 修剪后的片段，且第二模型只是独立证据、不自动裁决真值
  （transcription 上下文既有合同）。

### 候选矩阵

| | mlx-whisper + large-v3-mlx fp16（推荐） | faster-whisper CT2 int8 | large-v3-turbo（变体） |
|---|---|---|---|
| 内存 | ~4-5 GiB 峰值 | ~3 GB | 更小更快 |
| 速度（M1） | GPU，约 1x 实时 | CPU-only，最慢 | 最快 |
| 依赖 | mlx-whisper（MIT，小） | ctranslate2 栈 | 同 mlx-whisper |
| 风险 | 速度平平（review 只跑可疑区间，可接受） | 小时级不可行 | 蒸馏模型作为「独立证据」的证据力更弱 |

**推荐**：`mlx-whisper` + `mlx-community/whisper-large-v3-mlx`（fp16）。
review 只在可疑区间运行，速度不是瓶颈；fp16 完整版证据力最强。
与主模型不同家族，满足 Independent-model review 要求。

---

## 3. `forced_alignment` — Qwen3-ForcedAligner-0.6B

### 事实基础

- `Qwen/Qwen3-ForcedAligner-0.6B` 确认存在：Apache-2.0，实际 0.9B 参数，
  非自回归单次前向，输出词/字级 `{text, start_time, end_time}`，11 语种含中英。
  来源：https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B
- **硬限制：单次最长 5 分钟音频**（模型卡原文），无官方长音频滑窗指引——
  分段义务在调用方，本管线的 VAD 分段天然满足（见 §8）。
- MLX 路线：`mlx-community/Qwen3-ForcedAligner-0.6B-8bit`（1.27 GB，
  经 mlx-audio 0.3.1 转换，`mlx_audio.stt.generate` 可跑）。
  月下载约 876——采用度低是主要风险，原型必须重点验证。

**推荐**：`mlx-community/Qwen3-ForcedAligner-0.6B-8bit`，运行时与
asr_primary 共用 `mlx-audio`。备选是官方 `qwen-asr` 包 CPU 路径做
parity 抽查（不作为生产路径，理由同 §1）。

---

## 4. `vad` — silero-vad

### 事实基础

- v6.2.1（2026-02-24），MIT，onnx 模型约 2 MB，完全离线。
- **pip 包 `silero-vad` 声明依赖 torch>=1.12 + torchaudio**（即使只用 onnx 路径）。
  来源：https://pypi.org/project/silero-vad/
- torch-free 路线：模型文件就在仓库源码树
  `src/silero_vad/data/silero_vad.onnx`（含 16k/half/op18 变体），
  可按 git tag + 文件哈希钉版、直接携带 + onnxruntime 推理。
  来源：https://github.com/snakers4/silero-vad/tree/master/src/silero_vad/data

**推荐**：**直接携带 `silero_vad.onnx`（按 v6.2.1 tag + sha256 钉版）+
onnxruntime**，不安装 `silero-vad` pip 包，省掉整个 torch 栈。
onnxruntime 反正是 diarization 与 OCR 的共同依赖，边际成本为零。

---

## 5. `diarization` — 注册表缺口的补位

### 事实基础

- 代码消费 `diarization` 能力（audio_analysis 上下文），注册表此前无候选。
- **pyannote 官方排除理由坐实**：`pyannote/segmentation-3.0` 与
  `speaker-diarization-3.1` 的 HF 仓库均为门控（登录 + 接受条款 + token），
  属于凭证门控候选，按 capabilities 合同直接卡死能力。但其 **license 本身
  是 MIT**（模型卡原文「will always remain open-source」）——所以第三方
  合法转换再分发是干净的。
- **sherpa-onnx 路线（k2-fsa）**：代码 Apache-2.0；PyPI 1.13.5 有 macOS
  universal2（arm64）wheel，**无 torch**；模型是**公开 GitHub release 直链**
  （零凭证）：pyannote segmentation-3.0 的 ONNX 转换（7 MB，MIT）+
  3D-Speaker CAM++ `zh_en_16k-common_advanced` 双语 embedding
  （28.3 MB，ModelScope API 确认 Apache-2.0）+ 聚类。分段模型 powerset
  编码支持重叠说话人。CPU 速度：issue 口径 RTF≈0.2（非官方基准）。
  来源：https://github.com/k2-fsa/sherpa-onnx （release tag
  `speaker-segmentation-models` / `speaker-recongition-models`——后者拼写
  错误是真实 tag 名）
- **FunASR CAM++ 管线**：`iic/speech_campplus_speaker-diarization_common`
  （ModelScope，Apache-2.0，免凭证），中文强；但拖 torch + torchaudio +
  modelscope 全栈，且英文/重叠处理弱于 pyannote 分段方案。
- 其余出局：NeMo Sortformer（英文为主 + v1 为 NC license）、diart（默认依赖
  门控 pyannote）、WeSpeaker/3D-Speaker 本体（是 sherpa-onnx 方案的原料，
  非成品管线）。

### 候选矩阵

| | sherpa-onnx 管线（推荐） | FunASR CAM++ 管线 |
|---|---|---|
| 依赖 | pip 单包，无 torch | torch + torchaudio + modelscope |
| 模型体积 | ~35 MB | 数百 MB 级 |
| 中/英 | 双语 embedding + 语言无关分段 | 中文强、英文弱 |
| 重叠语音 | 分段模型原生支持 | 弱 |
| 凭证 | GitHub release 直链，零凭证 | ModelScope 免凭证 |
| 风险 | 转换/聚类为社区维护，DER 与 pyannote 官方有偏差记录（issue #1708）；CPU 速度无官方基准 | 依赖重；重叠处理弱 |

**推荐**：sherpa-onnx 管线，注册两个模型资产候选
（`sherpa-onnx-pyannote-segmentation-3-0`、`3dspeaker-campplus-zh-en-advanced`）。
原型阶段用真实中英素材做 DER 目检（本管线的说话人是匿名结构，不猜真名，
对 DER 绝对值的要求本来就低于字幕/转写精度）。

---

## 6. `ocr_primary` — RapidOCR

### 事实基础（补齐既有注册表条目的钉版细节）

- PyPI `rapidocr` 3.9.2（2026-07-21），Apache-2.0，wheel 27.3 MB；
  v3.9.0+ 默认模型为 **PP-OCRv6 small det+rec（多语含中英）+ v4 mobile cls**，
  默认模型打进 wheel（官方安装文档原文），每个模型在 `default_models.yaml`
  里有 SHA256 + 按 release tag 钉版的 ModelScope URL。
  来源：https://pypi.org/project/rapidocr/ 、
  https://rapidai.github.io/RapidOCRDocs/main/model_list/
- onnxruntime 自 rapidocr 2.0.6 起不再自动安装，需显式装
  （onnxruntime 1.28.0 有 macOS arm64 wheel，19.1 MB，CPU EP 即可）。
- 声明依赖 `opencv_python>=4.5.1.48`（全量版；headless 替换是社区惯例
  而非文档背书）。
- license 纵深确认：RapidOCR 代码 Apache-2.0；模型为 PaddleOCR 衍生，
  百度持有版权，PaddleOCR 仓库（含模型）整体 Apache-2.0——全链干净，
  需保留归属声明。
- 视频帧（截屏/PPT 类）无官方精度基准；最高杠杆配置项是
  `limit_side_len`（默认 736，1080p+ 帧需上调防小字被降采样抹掉）；
  屏幕文字方向固定可关 `use_cls`。

**推荐钉版**：`rapidocr==3.9.2` + `onnxruntime==1.28.0` + 声明的 opencv。
安装后首跑 dump `RapidOCR().config`，把实际 det/rec/cls 模型文件名 + 哈希
记入能力清单（wheel 版本 + PyPI 哈希已传递性钉住模型字节）。

---

## 7. `text_semantics` — 新能力的文本模型

### 事实基础

- 本能力是阶段 11 新定义的（既有「受控离线文本适配器」保留为测试路径）。
  职责：语义边界候选 + 段级详细内容 + 摘要，输出严格 JSON，
  经既有裁决/验证层（无效提案留诊断，不会污染正式产出）。
- `mlx-community/Qwen3-4B-Instruct-2507-4bit`：2.26 GB，Apache-2.0，
  **原生 262K 上下文**，2507 代较老 4B 官方基准大幅提升（WritingBench
  68.5→83.4，MMLU-ProX 49.6→61.6，Arena-Hard v2 9.5→43.4），
  原生非 thinking（确定性 JSON 场景友好）。第三方 12 模型小模型基准把它
  排在 Qwen3-8B 之上（distil labs，社区数据）。
  来源：https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
- `Qwen/Qwen3-8B-MLX-4bit`（官方上传）：4.35 GB，**8B 没有 2507 刷新**
  （2507 波只覆盖 4B/30B-A3B/235B-A22B），基座停在 2025-04；原生上下文
  32K；hybrid thinking 开关需显式关闭。
- 黑马 `Qwen/Qwen3.5-4B`（2026-03）：C-Eval 85.1、262K 上下文，但
  Gated-DeltaNet 架构的 MLX 支持尚不成熟（现有 4bit 转换出自 fork 分支、
  带「可能有更好转换」免责声明，Apple Silicon 有延迟回退记录）——
  本轮不选，一个季度后复评。
- 运行时 `mlx-lm`（PyPI 0.31.3，MIT）：`--temp/--seed` 确定性采样、
  `--max-kv-size` 内存上限、`mx.get_peak_memory()` 峰值证据钩子。
  **无原生 JSON schema 约束解码**；严格 JSON = 提示词 + 本管线既有
  校验裁决层；若原型 JSON 失约率高，Outlines 支持 mlx-lm 后端可加装
  （作为记录在案的升级路径，不进首版依赖）。

### 候选矩阵

| | Qwen3-4B-Instruct-2507-4bit（推荐） | Qwen3-8B-MLX-4bit | Qwen3.5-4B MLX-4bit |
|---|---|---|---|
| 权重/在用内存 | 2.26 GB / ~3 GiB | 4.35 GB / ~5.5-6 GiB | 3.03 GB / ~4 GiB |
| 基座代际 | 2507（最新稳定） | 2025-04（无刷新） | 2026-03（最新） |
| 上下文 | 262K 原生 | 32K（YaRN 131K） | 262K 原生 |
| 中英质量 | 官方大幅提升；第三方排 8B 之上 | 参数更大但基座旧 | 纸面最强（C-Eval 85.1） |
| JSON 确定性 | 原生非 thinking | 需关 thinking 开关 | 未知 |
| MLX 成熟度 | mlx-lm 0.26.2 正式转换 | 官方上传 | fork 分支转换 + 性能回退记录 |

**推荐**：`mlx-community/Qwen3-4B-Instruct-2507-4bit` + `mlx-lm`。
在 12 GiB 包络下给 KV 缓存留出最大余量（长视频段级上下文是真实压力），
且是唯一「新基座 + 原生非 thinking + 262K 上下文」三样全占的候选。

---

## 8. 跨能力架构含义（写入 spec 的输入）

1. **VAD 分段是全链前置义务**：Qwen3-ASR 无官方本地长音频方案、对齐器硬限
   5 分钟、whisper 长音频幻觉集中在静音区——三者共同指向：silero-vad 分段
   （≤5 分钟、静音边界切割）是 asr_primary/asr_review/forced_alignment
   三个能力的共享上游，属管线自有职责。
2. **onnxruntime 是三能力共享依赖**（vad/diarization/ocr），CPU EP 足够，
   不启用 CoreML EP（无该尺寸模型的收益证据）。
3. **子进程模型执行**（已拷问锁定）：mlx-audio/mlx-whisper/mlx-lm 三个 MLX
   运行时各自在独立子进程中加载-推理-退出，退出即归还内存；
   `mx.get_peak_memory()` 作为每阶段峰值证据写入运行记录。
4. **量化中文损失无公开数据**是全组合共同盲区——每能力原型必须包含中文
   样例目检（拷问已定：工程检查 + 简短样例输出给维护者过目）。

## 9. 拟新增推理依赖清单（一次性授权，进 spec）

| 依赖 | 版本基线 | license | 服务能力 |
|---|---|---|---|
| mlx-audio | 0.4.8 | MIT | asr_primary, forced_alignment |
| mlx-whisper | 0.4.3 | MIT | asr_review |
| mlx-lm | 0.31.3 | MIT | text_semantics |
| sherpa-onnx | 1.13.5 | Apache-2.0 | diarization |
| onnxruntime | 1.28.0 | MIT | vad, diarization, ocr_primary |
| rapidocr | 3.9.2 | Apache-2.0 | ocr_primary |
| opencv-python | >=4.5.1.48 | Apache-2.0 | ocr_primary（声明依赖） |
| huggingface_hub（hf CLI） | 最新稳定 | Apache-2.0 | 模型钉版下载（`hf download --revision`） |

注：mlx（core）随 mlx-* 包传递安装；torch 全链不引入。
精确版本在 spec 里按 uv 锁定流程钉死。

## 10. 决策清单（供维护者逐项拍板）

| # | 决策 | 推荐 | 备选 |
|---|---|---|---|
| D1 | asr_primary 模型 | Qwen3-ASR-**1.7B-8bit** | 0.6B-8bit（求速度）；4bit（中文损失未知） |
| D2 | asr_primary/对齐 运行时 | **mlx-audio** | mlx-qwen3-asr（parity 工件强但单维护者） |
| D3 | asr_review | **mlx-whisper + large-v3-mlx fp16** | faster-whisper int8（CPU-only，慢） |
| D4 | vad 形态 | **携带 .onnx + onnxruntime（免 torch）** | silero-vad pip 包（拖 torch） |
| D5 | diarization | **sherpa-onnx 管线**（pyannote-seg-3.0 ONNX + CAM++ zh-en） | FunASR CAM++（重依赖） |
| D6 | ocr | **rapidocr==3.9.2 + onnxruntime==1.28.0** | —（唯一候选，此为钉版确认） |
| D7 | text_semantics | **Qwen3-4B-Instruct-2507-4bit + mlx-lm** | Qwen3-8B-MLX-4bit；Qwen3.5-4B（下季复评） |

全部拍板后：写 `PHASE_11_SPECIFICATION.md`（含依赖一次性授权清单、
逐模型下载计划模板、注册表候选更新、12 GiB 包络改造、子进程执行 ADR、
原型 ticket 与样例目检流程）→ 维护者批准 → 动工。
