"""Read inputs as data, preserving slide order and source provenance."""
from __future__ import annotations

import posixpath
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def part_target(owner, target):
    part = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(owner), target))
    if part.startswith('../'):
        raise ValueError('Invalid package relationship target.')
    return part


def rels(package, part):
    name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    return list(ET.fromstring(package.read(name))) if name in package.namelist() else []


def note_body(package, slide_part):
    for rel in rels(package, slide_part):
        if rel.attrib['Type'].endswith('/notesSlide') and rel.attrib.get('TargetMode') != 'External':
            root = ET.fromstring(package.read(part_target(slide_part, rel.attrib['Target'])))
            for shape in root.findall('.//p:sp', NS):
                ph = shape.find('p:nvSpPr/p:nvPr/p:ph', NS)
                if ph is not None and ph.get('type') == 'body':
                    return '\n'.join(''.join(t.text or '' for t in p.findall('.//a:t', NS))
                                     for p in shape.findall('.//a:p', NS)).strip()
    return ''


def inspect_deck(path):
    with ZipFile(path) as package:
        if package.testzip():
            raise ValueError('PPTX is corrupt.')
        root = ET.fromstring(package.read('ppt/presentation.xml'))
        references = {r.attrib['Id']: r for r in rels(package, 'ppt/presentation.xml')}
        slides = []
        for index, item in enumerate(root.findall('p:sldIdLst/p:sldId', NS), 1):
            rel = references[item.attrib['{' + NS['r'] + '}id']]
            part = part_target('ppt/presentation.xml', rel.attrib['Target'])
            slide = ET.fromstring(package.read(part))
            objects = []
            title = ''
            tree = slide.find('p:cSld/p:spTree', NS)
            for shape in ([] if tree is None else list(tree)):
                text = '\n'.join(''.join(t.text or '' for t in para.findall('.//a:t', NS))
                                 for para in shape.findall('.//a:p', NS)).strip()
                non_visual = shape.find('.//p:cNvPr', NS)
                ph = shape.find('.//p:ph', NS)
                if non_visual is not None:
                    objects.append(dict(id=int(non_visual.get('id', '0')), name=non_visual.get('name', ''), text=text, placeholder=ph.get('type') if ph is not None else None))
                if ph is not None and ph.get('type') in ('title', 'ctrTitle'):
                    title = text
            transition = slide.find('p:transition', NS)
            if transition is None:
                transition = slide.find('.//p:transition', NS)
            notes = note_body(package, part)
            visible_text = '\n'.join(x['text'] for x in objects if x['text'])
            if not title:
                title = next((x['text'].splitlines()[0] for x in objects if x['text']), f'Slide {index}')
            metadata_lines = re.findall(r'(?im)^\s*(?:Source:|Sources:|来源[:：]|Timing:|Duration:|\[.*?seconds.*?\]).*$', notes)
            spoken_lines = [line for line in notes.splitlines() if line.strip() and line not in metadata_lines]
            slides.append(dict(slide=index, title=title, text=visible_text, notes=notes,
                has_speech=bool(spoken_lines), needs_notes_cleanup=bool(metadata_lines), objects=objects,
                advance_ms=int(transition.get('advTm', '0')) if transition is not None else 0))
        filled = sum(s['has_speech'] for s in slides)
        state = 'none' if filled == 0 else 'complete' if filled == len(slides) else 'partial'
        if any(s['needs_notes_cleanup'] for s in slides):
            state = 'needs_cleanup' if state == 'complete' else state
        size = root.find('p:sldSz', NS)
        return dict(slides=slides, slide_count=len(slides), notes_state=state,
                    language=detect_language('\n'.join(s['notes'] or s['text'] for s in slides)),
                    size=dict(size.attrib) if size is not None else {})


def detect_language(text):
    cjk = len(re.findall(r'[\u3400-\u9fff]', text))
    latin = len(re.findall(r'[A-Za-z]+', text))
    if not cjk and not latin:
        return None
    if cjk > 20 and latin > 20 and .25 < cjk / max(1, latin) < 4:
        return 'mixed'
    return 'zh' if cjk > latin else 'en'


def inspect_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(''):
        return dict(pages=[], page_count=len(reader.pages) if not reader.is_encrypted else None, encrypted=True, needs_ocr=False, title=None)
    pages = [dict(page=index, text=page.extract_text() or '') for index, page in enumerate(reader.pages, 1)]
    return dict(pages=pages, page_count=len(pages), encrypted=False,
                needs_ocr=sum(len(p['text'].strip()) for p in pages) < max(30, len(pages) * 20),
                title=str(reader.metadata.title or '') if reader.metadata else '',
                language=detect_language('\n'.join(p['text'] for p in pages[:3])))


def inspect_script(path):
    path = Path(path)
    if path.suffix.lower() == '.docx':
        with ZipFile(path) as package:
            root = ET.fromstring(package.read('word/document.xml'))
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text = '\n'.join(''.join(t.text or '' for t in p.findall('.//w:t', ns))
                             for p in root.findall('.//w:body/w:p', ns))
    elif path.suffix.lower() in ('.md', '.txt'):
        text = path.read_text(encoding='utf-8-sig')
    else:
        raise ValueError('Script must be MD, TXT, or DOCX.')
    pattern = re.compile(r'(?im)^\s*(?:#{1,6}\s*)?(?:Slide\s*|第\s*)(\d+)\s*(?:页)?\s*(?::[^\n]*|：[^\n]*)?\s*$')
    headings = list(pattern.finditer(text))
    slides = [dict(slide=int(m[1]), text=text[m.end():headings[i + 1].start() if i + 1 < len(headings) else len(text)].strip())
              for i, m in enumerate(headings)]
    ordered = [s['slide'] for s in slides] == list(range(1, len(slides) + 1))
    return dict(text=text, slides=slides, aligned=bool(slides) and ordered and all(s['text'] for s in slides),
                language=detect_language(text))


def markdown_script(slides):
    return '# Speaker Script\n\n' + '\n\n'.join(
        f'## Slide {s.get("slide", index):02}: {s.get("title", "")}\n\n{s.get("notes", s.get("text", ""))}' for index, s in enumerate(slides, 1)) + '\n'
