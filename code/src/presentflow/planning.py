"""Scenario routing and stage-aware intake. No generation or network side effects."""
from __future__ import annotations

import re

from .hardware import recommend
from .ingest import inspect_deck, inspect_pdf, inspect_script
from .storage import input_files, load_job, sha


def inspect_job(directory):
    path, job = load_job(directory)
    files = input_files(path, job)
    materials = {}
    for role, file in files.items():
        if not file.is_file():
            materials[role] = dict(error='missing_file', path=str(file))
            continue
        info = dict(path=str(file), sha256=sha(file))
        try:
            if role == 'paper':
                info.update(inspect_pdf(file))
            elif role in ('deck', 'template'):
                info.update(inspect_deck(file))
            elif role == 'script':
                info.update(inspect_script(file))
        except Exception as exc:
            info['error'] = f'{type(exc).__name__}: {exc}'
        materials[role] = info
    return path, job, materials


def make_plan(materials, answers=None, hardware=None):
    answers = dict(answers or {})
    hardware = hardware or {}
    issues, questions, tasks, inferred = [], [], [], {}

    def ask(key, stage, text, choices=None, required=True):
        if answers.get(key) is None:
            questions.append(dict(id=key, stage=stage, question=text, choices=choices or [], required=required))

    for role, data in materials.items():
        if data.get('error'):
            issues.append(dict(code='input_error', role=role, detail=data['error']))
    usable = {k: v for k, v in materials.items() if not v.get('error')}
    paper, deck, template, script = (usable.get(k) for k in ('paper', 'deck', 'template', 'script'))
    if paper is not None and paper.get('encrypted'):
        issues.append(dict(code='encrypted_pdf', detail='需要未加密副本或由用户提供密码后本地读取。'))
    if paper is not None and paper.get('needs_ocr'):
        tasks.append(dict(id='ocr_and_visual_review', status='required', reason='论文文字层不足，需本地 OCR 并逐页核查。'))
    script_ready = bool(script and script.get('aligned') and (not deck or len(script['slides']) == deck['slide_count']))
    notes_ready = bool(deck and deck['notes_state'] == 'complete')
    notes_partial = bool(deck and deck['notes_state'] == 'partial')
    if deck:
        scenario = ('paper_' if paper is not None else '') + 'deck_' + ('script' if script_ready else 'notes' if notes_ready else 'partial_notes' if notes_partial else 'no_script')
    elif paper is not None:
        scenario = 'paper_template' if template else 'paper_only'
        if script:
            scenario += '_script'
    elif script:
        scenario = 'script_template' if template else 'script_only'
    else:
        scenario = 'insufficient_content'
        ask('content_source', 'materials', '目前缺少论文、成稿 PPT 或可用讲稿，请提供至少一种内容来源。', ['paper', 'deck', 'script'])
    if deck and template:
        ask('deck_action', 'materials', '已有成稿 PPT 和参考模板：保留成稿排版，还是按模板重新排版？', ['preserve', 'restyle'])
    elif deck:
        inferred['deck_action'] = 'preserve'
    language_source = script if script_ready else deck if deck else template if template else paper
    detected = (language_source or {}).get('language')
    if answers.get('language'):
        inferred['language'] = answers['language']
    elif detected and detected != 'mixed' and (deck or template or script_ready):
        inferred['language'] = detected
        inferred['language_basis'] = 'existing_script_or_slides'
    else:
        ask('language', 'brief', '汇报和演讲稿使用哪种语言？', ['zh', 'en', 'follow_source'])
    if not answers.get('duration_minutes'):
        if answers.get('duration_basis') == 'template' and template:
            timing = sum(s.get('advance_ms', 0) for s in template.get('slides', []))
            if timing > 30000:
                inferred['duration_minutes'] = round(timing / 60000, 1)
                inferred['duration_basis'] = 'template_timings'
            else:
                text = '\n'.join(s.get('notes', '') for s in template.get('slides', []))
                words = len(re.findall(r'[A-Za-z0-9]+', text))
                characters = len(re.findall(r'[\u3400-\u9fff]', text))
                if words or characters:
                    inferred['duration_minutes'] = round(words / 125 + characters / 240, 1)
                    inferred['duration_basis'] = 'rough_template_notes_estimate_125_english_words_or_240_chinese_chars_per_minute'
                else:
                    ask('duration_minutes', 'brief', '模板没有可靠时长，请指定预计报告分钟数。', ['10', '15', '20'])
        elif deck and (notes_ready or script_ready):
            inferred['duration_policy'] = 'preserve_script_then_measure_audio'
        else:
            ask('duration_minutes', 'brief', '预计报告多长时间？有模板时也可以选择参考模板时长。', ['10', '15', '20', 'follow_template'])
    if not deck:
        if not template:
            ask('template_preference', 'brief', '是否有 PPT 模板？没有时可使用简洁学术版式。', ['provide_template', 'builtin_academic'])
        ask('occasion', 'brief', '这是什么汇报场景？可同时提供会议名、报告人和单位。', ['conference', 'group_meeting', 'defense', 'other'])
        if answers.get('occasion') == 'conference':
            ask('conference_name', 'branding', '封面显示哪个会议？尚未确定时可省略会议名称，不能从旧模板推断。', ['provide_name', 'omit'], required=False)
        if not answers.get('presenter_name') and not answers.get('institution'):
            ask('presenter_identity', 'branding', '报告人和单位按论文信息，还是另行指定？也可以先不显示。', ['paper_metadata', 'provide_details', 'omit'], required=False)
        ask('branding', 'branding', '会议和学校标志如何处理？可上传、允许从官网查找，或只使用文字。', ['provided_assets', 'official_lookup', 'text_only'])
    else:
        inferred['branding_policy'] = 'preserve_existing_unless_user_requests_update'
    if script and not script_ready:
        tasks.append(dict(id='align_script', status='required', reason='讲稿无逐页标记、顺序不连续或页数与成稿不符，需按实际幻灯片对齐。'))
    if script_ready and deck and any(s.get('has_speech') for s in deck['slides']):
        existing = [' '.join(s.get('notes', '').split()) for s in deck['slides']]
        external = [' '.join(s['text'].split()) for s in script['slides']]
        if existing != external:
            ask('script_source', 'script', 'PPT 备注与另附讲稿不同，以哪份为准？', ['external_script', 'deck_notes', 'merge_review'])
    tasks.append(dict(id='read_sources', status='required' if paper is not None else 'optional', reason='有论文时核对结论、数据、图表与页码，已有 PPT 保持内容边界。'))
    if not deck or answers.get('deck_action') == 'restyle':
        tasks.append(dict(id='author_deck', status='required', reason='由 AI Skill 根据证据和已确认需求撰写结构化 talk spec，并制作可编辑幻灯片。'))
    else:
        tasks.append(dict(id='preserve_deck', status='ready', reason='保留已有 PPT 的可见排版，按页补齐或更新演讲稿。'))
    tasks.append(dict(id='write_script', status='reuse' if script_ready or notes_ready else 'required',
                      reason='逐页只有朗读文本；保留已有有效讲稿，仅补齐空缺与必要衔接。'))
    if deck and (deck['notes_state'] == 'needs_cleanup' or any(s['needs_notes_cleanup'] for s in deck['slides'])):
        tasks.append(dict(id='clean_notes', status='required', reason='来源、计时和舞台提示与朗读文本分开保存，不能直接朗读元数据。'))
    ask('voice_mode', 'voice', '是否需要配音？可用普通旁白、本人音色、本人逐页实录，或只生成 PPT 与稿子。', ['none', 'stock', 'clone', 'import'])
    mode = answers.get('voice_mode')
    recommendations = recommend(hardware, privacy=answers.get('privacy', 'ask'), voice_mode=mode or 'clone')
    if mode in ('stock', 'clone'):
        ask('privacy', 'voice', '讲稿与录音能否发送给外部语音服务？', ['local_only', 'ask_per_provider'])
        provider = answers.get('voice_provider')
        if not provider:
            choices = list(dict.fromkeys(o['id'] for o in recommendations['options'] if o.get('runnable', True)))
            ask('voice_provider', 'voice', '根据电脑检测和隐私偏好，选择具体语音方案；推荐不会自动安装或启用。', choices)
        if provider in ('qwen-local', 'qwen-local-1.7b'):
            if mode == 'stock':
                issues.append(dict(code='provider_mode_mismatch', detail='当前 Qwen Base 适配器用于有参考音频的克隆；普通旁白请选择兼容服务或导入音频。'))
            ask('local_install', 'voice_setup', '是否愿意按所选模型建立本地依赖并下载权重？已有模型可直接选择复用。', ['reuse_existing', 'allow_setup', 'choose_another'])
        if provider == 'elevenlabs':
            if answers.get('privacy') == 'local_only':
                issues.append(dict(code='cloud_conflicts_with_privacy', detail='选择了仅本地，同时选择在线服务。请调整其中一项。'))
            ask('cloud_budget_chars', 'voice_setup', '这次最多允许生成多少个讲稿字符？请参考服务当前费用设置预算上限。', ['5000', '10000', '20000'])
        if mode == 'clone':
            if not usable.get('recording') and not answers.get('existing_voice_id'):
                ask('reference_recording', 'recording', '所选克隆方案需要本人的参考录音。确定模型后再按其要求给出试读稿、录音长度和上传方式。')
            ask('voice_rights', 'recording', '该音色是你本人，还是已取得本人同意的音色？', ['own_voice', 'speaker_permission'])
        tasks.append(dict(id='preview_voice', status='required', reason='先生成短试听，由用户确认音色、语气与速度，再生成整场；不把素材授权等同于云上传授权。'))
        tasks.append(dict(id='narrate', status='pending', reason='按文本、音色、模型与参数缓存；改稿后只重做受影响音频。'))
    elif mode == 'import':
        ask('audio_manifest', 'recording', '请提供每页实录，或先将整场录音按页对齐；不会把参考音色样本当作完整配音。')
    if mode and mode != 'none':
        tasks.append(dict(id='assemble_autoplay', status='pending', reason='嵌入每页音频并按实际长度计时，输出普通免宏 PPTX。'))
    tasks.append(dict(id='validate_deliver', status='pending', reason='核对内容覆盖、逐页稿、排版、音频、翻页与输出文件；来源记录不写入朗读稿。'))
    stages = ['materials', 'brief', 'branding', 'script', 'voice', 'voice_setup', 'recording']
    current = next((s for s in stages if any(q['stage'] == s for q in questions)), 'production')
    return dict(schema_version=1, scenario=scenario, inferred=inferred, issues=issues, questions=questions,
                current_stage=current, questions_now=[q for q in questions if q['stage'] == current][:3],
                voice_recommendations=recommendations, tasks=tasks,
                ready_for_content=not issues and not any(q['stage'] in ('materials', 'brief') and q['required'] for q in questions),
                narration_preflight='checked_by_execution_command', note='计划是路由与需求检查；事实提炼、幻灯片设计和演讲稿由 Skill 所在 AI 助手完成。')
