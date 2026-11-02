"""Read-only local capability detection; recommendations never select a model."""
import csv
import ctypes
import io
import os
import platform
import shutil
import subprocess


def detect():
    memory = None
    if os.name == 'nt':
        class Memory(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in
                ('total_physical', 'available_physical', 'total_page', 'available_page', 'total_virtual', 'available_virtual', 'extended')]
        state = Memory()
        state.length = ctypes.sizeof(state)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            memory = round(state.total_physical / 1024 ** 3, 1)
    elif hasattr(os, 'sysconf'):
        try:
            memory = round(os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE') / 1024 ** 3, 1)
        except (ValueError, OSError):
            pass
    gpus = []
    if shutil.which('nvidia-smi'):
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
                                    capture_output=True, text=True, check=True, timeout=5)
            for row in csv.reader(io.StringIO(result.stdout)):
                gpus.append(dict(name=row[0].strip(), vram_gb=round(float(row[1]) / 1024, 1), backend='cuda'))
        except (subprocess.SubprocessError, ValueError, IndexError):
            pass
    return dict(os=platform.system(), arch=platform.machine(), ram_gb=memory, gpus=gpus,
                ffmpeg=bool(shutil.which('ffmpeg')), ffprobe=bool(shutil.which('ffprobe')),
                detection_origin='machine_running_this_command')


def recommend(hardware, *, privacy='ask', voice_mode='clone'):
    ram = hardware.get('ram_gb') or 0
    cuda = [g for g in hardware.get('gpus', []) if g.get('backend') == 'cuda']
    vram = max((g.get('vram_gb', 0) for g in cuda), default=0)
    options = []
    if voice_mode == 'none':
        return dict(options=[], requires_user_choice=False, reason='No narration requested.')
    if voice_mode == 'import':
        return dict(options=[dict(id='import', label='使用用户提供的逐页录音')], requires_user_choice=False)
    if voice_mode == 'clone':
        if vram >= 6 and ram >= 16:
            options.append(dict(id='qwen-local', model='Qwen3-TTS-12Hz-0.6B-Base',
                label='本地 Qwen 0.6B：先用短试听确认速度与音色', recommended=True))
            if vram >= 12 and ram >= 32:
                options.append(dict(id='qwen-local-1.7b', model='Qwen3-TTS-12Hz-1.7B-Base',
                    label='本地 Qwen 1.7B：可选，实际显存与速度需短测'))
        else:
            options.append(dict(id='local-assessment', label='本地环境需要进一步评估；不要自动下载模型或承诺 CPU 速度', runnable=False))
    if privacy != 'local_only':
        options.append(dict(id='elevenlabs', label='ElevenLabs 官方 API：需选择账号/预算并明确授权上传相关数据',
                            paid=True, external_data=True, recommended=not any(o.get('recommended') for o in options)))
    if voice_mode == 'stock':
        options.append(dict(id='import', label='导入其他工具生成的普通旁白，无需本人录音'))
    options.append(dict(id='import', label='本人按页实录后导入（切换为实录模式）', external_data=False))
    return dict(options=options, requires_user_choice=True,
        thresholds_are_heuristics=True,
        note='硬件阈值是保守筛选建议，不是模型官方最低要求。还需确认语言、驱动、可用显存、安装意愿与预算。不会自动选择或部署。')
