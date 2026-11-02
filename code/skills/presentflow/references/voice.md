# 语音选择与数据边界

1. `voice_mode=none`：不问录音、不装 TTS；交付 PPT＋稿子。
2. `voice_mode=stock`：普通旁白，无需本人录音；当前内置在线适配器为 ElevenLabs，也可导入其他工具的成品音频。
3. `voice_mode=clone`：用户同意使用本人或已获本人同意的音色后，选择本地 Qwen 或在线服务。先选方案，再按方案要求采集录音。
4. `voice_mode=import`：用户已经按页实录或自行生成旁白，导入逐页 MP3 manifest。

`doctor` 只读系统 RAM、NVIDIA GPU/显存、FFmpeg 情况，不探测私人文件，不联网测试服务，不部署。检测结果只代表运行该命令的机器：如果 AI 在远程沙箱或云服务器执行，不能当作用户电脑配置；应让用户在本机运行 doctor，或提供 RAM / GPU / 显存并通过 `plan --hardware` 导入。显存 ≥6 GB、RAM ≥16 GB 可优先建议 Qwen 0.6B 的短测；更大显存可列出 1.7B 为可选。阈值只是工具启发式筛选，实际取决于精度、驱动、可用内存、文本长度。无 CUDA 或资源不足时展示在线/本人实录选择；用户要求完全本地则不能推荐自动转云，CPU/MPS 等需另外评估适配。

Qwen 适配器使用本地 Base 模型、干净参考音频与准确参考转录。profile 中提供 `model`、`asr_model`、`reference_audio`、`reference_text`、`language`、`asr_language`、`vocabulary` 和可选 `pronunciations`。先将外部录音本地转为干净单人 WAV，核对参考转录。不能给中文讲稿使用英文专用 ASR。短试听也用于实测本机耗时，再估算整场时间，不许把别人的测速当成本机保证。

Qwen 官方支持短音色参考；实际建议用户录制约 20–40 秒清晰、自然的目标语言语音，再选择连续干净片段。不要要求用户刻意制造换气或背景噪声。ElevenLabs 官方通常建议更长、更一致的样本；按当时官方文档给出适当要求，不套用本地样本长度。

在线适配器仅通过 `https://api.elevenlabs.io` 官方 API 工作，不调用第三方公共演示站，也不承诺免费。用户明确选择服务、账号预算和具体数据范围之后才执行。现有个人音色 ID 可放私有 profile；新建克隆会根据服务返回信息检查是否仍需说话人验证。不要绕过服务验证。

```text
presentflow consent-summary runs/talk --purpose upload_recording --voice-name "My voice"
# 实际取得用户对此服务及录音的上传许可之后：
presentflow grant-consent runs/talk --purpose upload_recording --voice-name "My voice" --approve
presentflow create-voice runs/talk --name "My voice"

presentflow consent-summary runs/talk --purpose synthesize_speech --profile voice.local.json --preview
# 实际取得此范围许可后记录 grant，再执行 preview。
presentflow preview runs/talk --profile voice.local.json
presentflow approve-preview runs/talk --sha256 用户已试听的音频哈希
```

整场发送的文本与试听不同，需对完整文本范围单独授权（`consent-summary` / `grant-consent` 不加 `--preview`），若该范围此前已明确授权则不重复问。`consents.json` 的授权绑定服务、用途和数据哈希；改稿需要重新核对外发文本。确认同一服务并不意味着允许把录音同时发送到所有服务。

云语音密钥通过当前进程 `PRESENTFLOW_ELEVENLABS_API_KEY` 提供，不写进仓库或命令参数。`cloud_budget_chars` 是本次任务累计请求字符上限，包括试听和失败前已经保留的请求，防止把每次重跑当成新预算。字符数并非准确账单；具体收费、套餐权限和数据保存政策应核对官方信息。请求结果丢失时不自动重试收费调用。

原生输出需要桌面 PowerPoint，模型只在生成端需要。轻微换气和房间底噪是单独可选音轨，不承诺合成音色等同于本人逐句实录。

官方资料（选型时复核版本与条款）：[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)、[ElevenLabs IVC](https://elevenlabs.io/docs/api-reference/voices/ivc/create)、[ElevenLabs TTS](https://elevenlabs.io/docs/api-reference/text-to-speech/convert)、[录音建议](https://elevenlabs.io/docs/creative-platform/voices/voice-cloning/instant-voice-cloning)。仓库 MIT 许可证不替代模型、权重、服务和素材各自的许可。
