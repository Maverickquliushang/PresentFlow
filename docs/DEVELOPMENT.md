# 开发与运行

当前开发入口：`code/`。环境与业务数据隔离，使用 Python 3.11+。本机使用集中独立环境 `presentflow`，配置在忽略的 `code/.python-environment.json`；每个 PowerShell 进程加载项目 `project-env.ps1`。源码不包含本机 JSON。

```powershell
. ./project-env.ps1
python -m pytest code/tests -q
presentflow --help
presentflow doctor
```

从代码目录安装开发依赖：`python -m pip install -e '.[dev]'`。有 uv 时可用依赖锁安装。仓库 `uv.lock` 锁定可选能力的依赖元数据，普通核心安装不会下载 GPU 权重。需要本地语音时，由用户选择模型和平台后安装 `.[local-voice]`，另按官方说明选择兼容的 PyTorch CUDA / CPU / 系统构建；安装适配器不代表运行时或模型已可用。

`scripts/run.ps1` 先加载本项目环境再调用包。跨平台可直接用安装后的 `presentflow`。PowerShell 子进程只在本进程允许执行项目脚本，不修改系统执行策略或 Office 宏信任。

常用命令：

```text
presentflow init JOB [--paper PDF] [--template PPTX] [--deck PPTX] [--script MD_OR_DOCX] [--recording AUDIO]
presentflow plan JOB [--hardware HARDWARE_JSON] [--output PLAN_JSON]
presentflow answer JOB --set key=value
presentflow attach JOB --deck FILE
presentflow extract JOB
presentflow compose JOB --spec TALK_JSON --output NEW_PPTX
presentflow preview JOB --profile VOICE_PROFILE
presentflow approve-preview JOB --sha256 HASH
presentflow narrate JOB --profile VOICE_PROFILE
presentflow assemble JOB --audio-manifest MANIFEST_JSON --output NEW_PPTX
```

`attach` 会清除任务当前生成产物引用，防止把新源文件与旧成品混用，但不删除历史文件。仅改讲稿时直接编辑任务的逐页 MD 并重跑，或建立新任务并按明确来源导入旧缓存；不要无端 `attach` 录音导致内容产物引用被重置。

只允许私有本地 profile 配置路径。通用发行包不能包含记录用户环境的 JSON、录音、音色向量、权重、API key 或报告。示例都是合成文本/结构，不包含真实研究或个人资料。源码归档使用 `scripts/archive.py` 的明确源码目录清单，排除任何 runs/、私有输出和本机 JSON。

本地语音多语言需匹配本地 ASR。英文路径已有原型经验；其他语言的数字/专名、长句和韵律需额外试听校验。错误会在有限次重试后停下，不能跳过校验并宣称已验证。
