import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

P='http://schemas.openxmlformats.org/presentationml/2006/main'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG='http://schemas.openxmlformats.org/package/2006/relationships'


@pytest.fixture
def job(tmp_path):
    directory=tmp_path/'run'
    directory.mkdir()
    (directory/'job.json').write_text(json.dumps(dict(schema_version=1,inputs={},answers={},artifacts={})),encoding='utf-8')
    return directory


@pytest.fixture
def minimal_deck(tmp_path):
    def make(notes, *, order=None):
        path=tmp_path/f'deck_{len(list(tmp_path.glob("*.pptx")))}.pptx'
        root=ET.Element(f'{{{P}}}presentation')
        ids=ET.SubElement(root,f'{{{P}}}sldIdLst')
        relationships=ET.Element(f'{{{PKG}}}Relationships')
        order=order or list(range(1,len(notes)+1))
        with ZipFile(path,'w') as z:
            for index in order:
                ET.SubElement(ids,f'{{{P}}}sldId',{'id':str(255+index),f'{{{R}}}id':f'rId{index}'})
            for index,note in enumerate(notes,1):
                ET.SubElement(relationships,f'{{{PKG}}}Relationship',{'Id':f'rId{index}','Type':R+'/slide','Target':f'slides/slide{index}.xml'})
                slide=ET.Element(f'{{{P}}}sld');tree=ET.SubElement(ET.SubElement(slide,f'{{{P}}}cSld'),f'{{{P}}}spTree')
                shape=ET.SubElement(tree,f'{{{P}}}sp');nv=ET.SubElement(shape,f'{{{P}}}nvSpPr')
                ET.SubElement(nv,f'{{{P}}}cNvPr',{'id':'2','name':'Title'})
                ET.SubElement(ET.SubElement(nv,f'{{{P}}}nvPr'),f'{{{P}}}ph',{'type':'title'})
                ET.SubElement(ET.SubElement(ET.SubElement(shape,f'{{{P}}}txBody'),f'{{{A}}}p'),f'{{{A}}}r')
                ET.SubElement(shape.find(f'.//{{{A}}}r'),f'{{{A}}}t').text=f'Title {index}'
                z.writestr(f'ppt/slides/slide{index}.xml',ET.tostring(slide))
                rel=ET.Element(f'{{{PKG}}}Relationships');ET.SubElement(rel,f'{{{PKG}}}Relationship',{'Id':'rIdNotes','Type':R+'/notesSlide','Target':f'../notesSlides/notesSlide{index}.xml'})
                z.writestr(f'ppt/slides/_rels/slide{index}.xml.rels',ET.tostring(rel))
                nroot=ET.Element(f'{{{P}}}notes');ntree=ET.SubElement(ET.SubElement(nroot,f'{{{P}}}cSld'),f'{{{P}}}spTree')
                # Add a page-number placeholder that must never become spoken notes.
                for kind,text in [('sldNum','99'),('body',note)]:
                    ns=ET.SubElement(ntree,f'{{{P}}}sp');nn=ET.SubElement(ns,f'{{{P}}}nvSpPr')
                    ET.SubElement(ET.SubElement(nn,f'{{{P}}}nvPr'),f'{{{P}}}ph',{'type':kind})
                    tx=ET.SubElement(ns,f'{{{P}}}txBody')
                    for line in text.splitlines():
                        ET.SubElement(ET.SubElement(ET.SubElement(tx,f'{{{A}}}p'),f'{{{A}}}r'),f'{{{A}}}t').text=line
                z.writestr(f'ppt/notesSlides/notesSlide{index}.xml',ET.tostring(nroot))
            z.writestr('ppt/presentation.xml',ET.tostring(root))
            z.writestr('ppt/_rels/presentation.xml.rels',ET.tostring(relationships))
        return path
    return make
