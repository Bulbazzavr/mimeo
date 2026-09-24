"""Синтетический шаблон для тестов.

Собирает минимальный, но настоящий пакет PPTX, который специально задевает три
места, на которых ломаются парсеры:

* косвенность `schemeClr` -> `clrMap` -> роль темы    (DOM-COLOR §2)
* трансформации `lumMod` + `lumOff`                   (DOM-COLOR §3)
* наследование геометрии и текста от макета и мастера (DOM-GEOM §5, DOM-TEXT §2)

Запуск отдельно:  python tests/fixtures/build_fixture.py
"""

from __future__ import annotations

import os
import re
import sys
import zipfile

XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

CONTENT_TYPES = XML + """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
<Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
</Types>"""

ROOT_RELS = XML + f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{RT}/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>"""

PRESENTATION = XML + f"""<p:presentation {A} {R} {P}>
<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
<p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/></p:sldIdLst>
<p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>
<p:notesSz cx="6858000" cy="9144000"/>
<p:defaultTextStyle><a:lvl1pPr algn="l"><a:defRPr sz="1800"/></a:lvl1pPr></p:defaultTextStyle>
</p:presentation>"""

PRESENTATION_RELS = XML + f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{RT}/slideMaster" Target="slideMasters/slideMaster1.xml"/>
<Relationship Id="rId2" Type="{RT}/slide" Target="slides/slide1.xml"/>
<Relationship Id="rId3" Type="{RT}/slide" Target="slides/slide2.xml"/>
<Relationship Id="rId4" Type="{RT}/theme" Target="theme/theme1.xml"/>
</Relationships>"""

_PH_FILL = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'

THEME = XML + f"""<a:theme {A} name="Mimeo Test">
<a:themeElements>
<a:clrScheme name="Mimeo">
<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>
<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
<a:dk2><a:srgbClr val="44546A"/></a:dk2>
<a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>
<a:accent1><a:srgbClr val="4472C4"/></a:accent1>
<a:accent2><a:srgbClr val="ED7D31"/></a:accent2>
<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>
<a:accent4><a:srgbClr val="FFC000"/></a:accent4>
<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5>
<a:accent6><a:srgbClr val="70AD47"/></a:accent6>
<a:hlink><a:srgbClr val="0563C1"/></a:hlink>
<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>
</a:clrScheme>
<a:fontScheme name="Mimeo">
<a:majorFont><a:latin typeface="Unbounded"/><a:ea typeface=""/><a:cs typeface=""/>
<a:font script="Cyrl" typeface="Unbounded"/></a:majorFont>
<a:minorFont><a:latin typeface="Golos Text"/><a:ea typeface=""/><a:cs typeface=""/>
<a:font script="Cyrl" typeface="Golos Text"/></a:minorFont>
</a:fontScheme>
<a:fmtScheme name="Mimeo">
<a:fillStyleLst>{_PH_FILL}{_PH_FILL}{_PH_FILL}</a:fillStyleLst>
<a:lnStyleLst><a:ln w="6350">{_PH_FILL}</a:ln><a:ln w="12700">{_PH_FILL}</a:ln><a:ln w="19050">{_PH_FILL}</a:ln></a:lnStyleLst>
<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
<a:bgFillStyleLst>{_PH_FILL}{_PH_FILL}{_PH_FILL}</a:bgFillStyleLst>
</a:fmtScheme>
</a:themeElements>
</a:theme>"""

_TREE_HEAD = """<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>"""

SLIDE_MASTER = XML + f"""<p:sldMaster {A} {R} {P}>
<p:cSld>
<p:bg><p:bgPr><a:solidFill><a:schemeClr val="bg1"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>
<p:spTree>{_TREE_HEAD}
<p:sp>
<p:nvSpPr><p:cNvPr id="2" name="Title Placeholder"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="838200" y="457200"/><a:ext cx="10515600" cy="1325563"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
<p:txBody><a:bodyPr anchor="ctr"><a:normAutofit/></a:bodyPr><a:lstStyle/><a:p><a:endParaRPr lang="ru-RU"/></a:p></p:txBody>
</p:sp>
<p:sp>
<p:nvSpPr><p:cNvPr id="3" name="Body Placeholder"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="838200" y="2057400"/><a:ext cx="10515600" cy="3602038"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="ru-RU"/></a:p></p:txBody>
</p:sp>
</p:spTree>
</p:cSld>
<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
<p:txStyles>
<p:titleStyle><a:lvl1pPr algn="l"><a:defRPr sz="4400" b="0"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mj-lt"/></a:defRPr></a:lvl1pPr></p:titleStyle>
<p:bodyStyle>
<a:lvl1pPr algn="l"><a:defRPr sz="1800"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/></a:defRPr></a:lvl1pPr>
<a:lvl2pPr algn="l"><a:defRPr sz="1600"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/></a:defRPr></a:lvl2pPr>
</p:bodyStyle>
<p:otherStyle><a:lvl1pPr algn="l"><a:defRPr sz="1400"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/></a:defRPr></a:lvl1pPr></p:otherStyle>
</p:txStyles>
</p:sldMaster>"""

SLIDE_MASTER_RELS = XML + f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{RT}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
<Relationship Id="rId2" Type="{RT}/theme" Target="../theme/theme1.xml"/>
</Relationships>"""

SLIDE_LAYOUT = XML + f"""<p:sldLayout {A} {R} {P} type="obj" preserve="1">
<p:cSld name="Заголовок и объект">
<p:spTree>{_TREE_HEAD}
<p:sp>
<p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="838200" y="457200"/><a:ext cx="10515600" cy="1325563"/></a:xfrm></p:spPr>
<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="ru-RU"/></a:p></p:txBody>
</p:sp>
<p:sp>
<p:nvSpPr><p:cNvPr id="3" name="Content"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>
<p:spPr><a:xfrm><a:off x="838200" y="2057400"/><a:ext cx="10515600" cy="3602038"/></a:xfrm></p:spPr>
<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="ru-RU"/></a:p></p:txBody>
</p:sp>
</p:spTree>
</p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>"""

SLIDE_LAYOUT_RELS = XML + f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{RT}/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>"""

# Слайд 1: у плейсхолдеров нет собственного a:xfrm — геометрия и типографика
# обязаны прийти по наследству. DOM-GEOM §5, DOM-TEXT §2.
SLIDE1 = XML + f"""<p:sld {A} {R} {P}>
<p:cSld><p:spTree>{_TREE_HEAD}
<p:sp>
<p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
<p:spPr/>
<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ru-RU"/><a:t>Как мы строим движок</a:t></a:r></a:p></p:txBody>
</p:sp>
<p:sp>
<p:nvSpPr><p:cNvPr id="3" name="Content"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>
<p:spPr/>
<p:txBody><a:bodyPr/><a:lstStyle/>
<a:p><a:r><a:rPr lang="ru-RU"/><a:t>Разбираем шаблон до дизайн-системы</a:t></a:r></a:p>
<a:p><a:r><a:rPr lang="ru-RU"/><a:t>Выводим библиотеку паттернов слайдов</a:t></a:r></a:p>
<a:p><a:r><a:rPr lang="ru-RU"/><a:t>Собираем колоду и чиним вёрстку по рендеру</a:t></a:r></a:p>
</p:txBody>
</p:sp>
</p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""

# Слайд 2: карточка со скруглением и заливкой accent1 + lumMod/lumOff,
# крупное число и подпись. DOM-COLOR §3, DOM-GEOM §3.
SLIDE2 = XML + f"""<p:sld {A} {R} {P}>
<p:cSld><p:spTree>{_TREE_HEAD}
<p:sp>
<p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
<p:spPr/>
<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ru-RU"/><a:t>Результат</a:t></a:r></a:p></p:txBody>
</p:sp>
<p:sp>
<p:nvSpPr><p:cNvPr id="4" name="Card"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
<p:spPr>
<a:xfrm><a:off x="838200" y="2286000"/><a:ext cx="3200400" cy="2286000"/></a:xfrm>
<a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val 8000"/></a:avLst></a:prstGeom>
<a:solidFill><a:schemeClr val="accent1"><a:lumMod val="60000"/><a:lumOff val="40000"/></a:schemeClr></a:solidFill>
<a:ln w="12700"><a:solidFill><a:schemeClr val="accent1"/></a:solidFill></a:ln>
</p:spPr>
<p:txBody><a:bodyPr anchor="ctr"/><a:lstStyle/>
<a:p><a:pPr algn="ctr"/><a:r><a:rPr lang="ru-RU" sz="8000" b="1"/><a:t>84%</a:t></a:r></a:p>
</p:txBody>
</p:sp>
<p:sp>
<p:nvSpPr><p:cNvPr id="5" name="Caption"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
<p:spPr>
<a:xfrm><a:off x="4419600" y="2286000"/><a:ext cx="3200400" cy="914400"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/>
</p:spPr>
<p:txBody><a:bodyPr/><a:lstStyle/>
<a:p><a:r><a:rPr lang="ru-RU" sz="2800" b="1"/><a:t>Слайдов без дефектов</a:t></a:r></a:p>
</p:txBody>
</p:sp>
</p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>"""

SLIDE_RELS = XML + f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{RT}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>"""

def _text_sp(sid, name, x, y, cx, cy, size, text, bold="0", paras=1):
    body = "".join(
        f'<a:p><a:r><a:rPr lang="ru-RU" sz="{size}" b="{bold}"/>'
        f'<a:t>{text} {i + 1}</a:t></a:r></a:p>' if paras > 1
        else f'<a:p><a:r><a:rPr lang="ru-RU" sz="{size}" b="{bold}"/><a:t>{text}</a:t></a:r></a:p>'
        for i in range(paras)
    )
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/><p:cNvSpPr txBox="1"/>'
            f'<p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
            f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
            f'<p:txBody><a:bodyPr/><a:lstStyle/>{body}</p:txBody></p:sp>')


def _slide(inner: str) -> str:
    return (XML + f'<p:sld {A} {R} {P}><p:cSld><p:spTree>{_TREE_HEAD}{inner}'
            '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')


def _bullets(seed: int) -> str:
    """Раскладка «заголовок и список». Повторяется, значит должна слипнуться."""
    return _slide(
        _text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400, f"Раздел {seed}")
        + _text_sp(3, "B", 838200, 2057400, 10515600, 3602038, 1800,
                   f"Пункт списка {seed}", paras=3)
    )


def _cards(seed: int) -> str:
    """Три карточки в ряд одинаковой ширины."""
    cards = "".join(
        f'<p:sp><p:nvSpPr><p:cNvPr id="{10 + i}" name="Card{i}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{838200 + i * 3600000}" y="2286000"/>'
        f'<a:ext cx="3200400" cy="2286000"/></a:xfrm>'
        f'<a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val 8000"/></a:avLst></a:prstGeom>'
        f'<a:solidFill><a:schemeClr val="accent1"/></a:solidFill></p:spPr>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ru-RU" sz="1800"/>'
        f'<a:t>Карточка {seed}.{i + 1} с описанием того, что она означает</a:t></a:r></a:p>'
        f'</p:txBody></p:sp>'
        for i in range(3)
    )
    return _slide(_text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400, f"Карточки {seed}") + cards)


EXTRA_SLIDES = [
    # 0 — обложка
    _slide(_text_sp(2, "T", 838200, 2286000, 10515600, 2000000, 8800, "Заголовок колоды")),
    _bullets(1),                     # 1
    _bullets(2),                     # 2 — должен слиться с 1
    _cards(1),                       # 3
    _cards(2),                       # 4 — должен слиться с 3
    # 5 — крупное число
    _slide(_text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400, "Результат")
           + _text_sp(3, "N", 838200, 2286000, 3200400, 2286000, 8000, "84%", bold="1")
           + _text_sp(4, "C", 4419600, 2286000, 3200400, 914400, 1400, "Слайдов без дефектов")),
    # 6 — раздел
    _slide(_text_sp(2, "T", 838200, 2743200, 10515600, 1325563, 6000, "Часть вторая")),
    # 7 — две колонки
    _slide(_text_sp(2, "T", 838200, 457200, 10515600, 1325563, 4400, "Сравнение")
           + _text_sp(3, "L", 838200, 2057400, 5000000, 3000000, 1800,
                      "Левая колонка с достаточно длинным текстом для проверки раскладки")
           + _text_sp(4, "R", 6400800, 2057400, 5000000, 3000000, 1800,
                      "Правая колонка с достаточно длинным текстом для проверки раскладки")),
    # 8 — финал
    _slide(_text_sp(2, "T", 838200, 2743200, 10515600, 1325563, 6000, "Спасибо")),
]


PARTS: dict[str, str] = {
    "[Content_Types].xml": CONTENT_TYPES,
    "_rels/.rels": ROOT_RELS,
    "ppt/presentation.xml": PRESENTATION,
    "ppt/_rels/presentation.xml.rels": PRESENTATION_RELS,
    "ppt/theme/theme1.xml": THEME,
    "ppt/slideMasters/slideMaster1.xml": SLIDE_MASTER,
    "ppt/slideMasters/_rels/slideMaster1.xml.rels": SLIDE_MASTER_RELS,
    "ppt/slideLayouts/slideLayout1.xml": SLIDE_LAYOUT,
    "ppt/slideLayouts/_rels/slideLayout1.xml.rels": SLIDE_LAYOUT_RELS,
    "ppt/slides/slide1.xml": SLIDE1,
    "ppt/slides/_rels/slide1.xml.rels": SLIDE_RELS,
    "ppt/slides/slide2.xml": SLIDE2,
    "ppt/slides/_rels/slide2.xml.rels": SLIDE_RELS,
}


def _write(path: str, parts: dict[str, str]) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts.items():
            zf.writestr(name, body.encode("utf-8"))
    return path


def build(dest: str | os.PathLike[str]) -> str:
    """Шаблон на два слайда: наследование свойств, цвет, геометрия."""
    return _write(os.fspath(dest), PARTS)


def build_multi(dest: str | os.PathLike[str], slides: list[str] | None = None) -> str:
    """Шаблон на девять слайдов с двумя повторяющимися раскладками.

    На двух слайдах кластеризацию не проверить в принципе, поэтому под неё
    отдельная фикстура. Слайды 1-2 и 3-4 попарно одинаковы по раскладке.
    `slides` — свой набор слайдов вместо `EXTRA_SLIDES` (`Z-58`: макет
    оглавления посреди шаблона).
    """
    slides = EXTRA_SLIDES if slides is None else slides
    parts = dict(PARTS)
    n = len(slides)
    ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{10 + i}"/>' for i in range(n))
    rels = "".join(
        f'<Relationship Id="rId{10 + i}" Type="{RT}/slide" '
        f'Target="slides/slide{i + 1}.xml"/>' for i in range(n)
    )
    parts["ppt/presentation.xml"] = PRESENTATION.replace(
        '<p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/></p:sldIdLst>',
        f"<p:sldIdLst>{ids}</p:sldIdLst>",
    )
    parts["ppt/_rels/presentation.xml.rels"] = (
        XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{RT}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
        f'<Relationship Id="rId4" Type="{RT}/theme" Target="theme/theme1.xml"/>'
        f"{rels}</Relationships>"
    )
    overrides = "".join(
        f'<Override PartName="/ppt/slides/slide{i + 1}.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(n)
    )
    parts["[Content_Types].xml"] = re.sub(
        r'<Override PartName="/ppt/slides/slide\d+\.xml"[^/]*/>', "", CONTENT_TYPES
    ).replace("</Types>", overrides + "</Types>")
    for i, body in enumerate(slides, 1):
        parts[f"ppt/slides/slide{i}.xml"] = body
        parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = SLIDE_RELS
    return _write(os.fspath(dest), parts)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "minimal.pptx"
    )
    print(build(target))
