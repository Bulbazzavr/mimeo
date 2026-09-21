"""Ёмкость слота знает, что лежит поверх него. `Z-48`, `PLAN-7.8`.

Ни PowerPoint, ни шаблоны не нужны: PNG собираются здесь же, геометрия
считается на руках.

Ловушки, ради которых тест и написан, — все четыре опровергнуты замером на
корпусе, и без них правка была бы хуже прежнего поведения:

* **рамка карточки.** RGBA-картинка, накрывающая подпись целиком и
  непрозрачная на 1%. По правилу «`p:pic` — значит непрозрачно» она обнуляла
  тридцать слотов выданного VK Tech, где на растре видны все подписи;
* **общий размах вместо колонок.** У рамки непрозрачны оба края, между ними
  пусто. «От левого края непрозрачного до правого» — это вся ширина, и рамка
  снова становится плашкой;
* **фигура снизу.** Карточка накрывает свой же текст на 100%, и это норма:
  она лежит **под** ним;
* **формат, который не разобрать.** Чересстрочный PNG — «судить не по чему»,
  и слот не сужается. Это не «прозрачно» и не «заслоняет».
"""

from __future__ import annotations

import struct
import zlib

from mimeo.analyze.deck import Rect
from mimeo.analyze.occlusion import occluding_rects, visible_rect
from mimeo.analyze.raster import opacity_grid
from mimeo.analyze.shapes import ShapeObs
from mimeo.model import Slot
from mimeo.plan.matching import _OVERFLOW_HARD, _hard_limit


# --- PNG на руках ------------------------------------------------------


#: Подпись PNG: восемь байтов, с которых начинается любой файл формата.
SIGNATURE = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))


def _png(width: int, height: int, alpha, ctype: int = 6, interlace: int = 0) -> bytes:
    """RGBA-PNG, где непрозрачность каждого пикселя задаёт `alpha(x, y)`."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)                                    # фильтр None
        for x in range(width):
            raw += bytes((0, 0, 0, alpha(x, y)))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, interlace)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


FRAME = _png(32, 32, lambda x, y: 255 if x in (0, 31) or y in (0, 31) else 0)
RIGHT_HALF = _png(32, 32, lambda x, y: 255 if x >= 16 else 0)


def shape(shape_id="1", x=0, y=0, cx=1000, cy=100, kind="sp", fill=None,
          text="", image=None, hidden=False):
    runs = ()
    if text:
        from mimeo.analyze.shapes import RunObs
        runs = (RunObs(slide_index=0, shape_id=shape_id, text=text, para_index=0,
                       level=0, size_pt=12.0, bold=False, italic=False, caps=None,
                       spacing_pt=0.0, latin=None, cyrl=None, color_hex=None,
                       align=None, line_spacing_pct=None, in_placeholder=None),)
    return ShapeObs(
        slide_index=0, shape_id=shape_id, name="", kind=kind, ph_type=None,
        ph_idx=None, rect=Rect(x, y, cx, cy), rotated=False, hidden=hidden,
        geom=None, corner_radius_pct=None, fill_kind=fill, fill=None, line=None,
        line_w_emu=None, has_shadow=False, runs=runs, image_part=image,
    )


def bytes_of(table):
    return lambda part: table.get(part)


# --- сетка непрозрачности ----------------------------------------------


def test_frame_is_transparent_in_the_middle():
    """Рамка карточки: непрозрачны края, середина пуста."""
    grid = opacity_grid(FRAME, cols=32, rows=32)
    assert grid is not None
    assert grid[16][0] > 0.5 and grid[16][31] > 0.5
    assert grid[16][15] < 0.5 and grid[16][16] < 0.5


def test_a_border_thinner_than_a_cell_does_not_make_the_cell_opaque():
    """Сетка грубее пикселя, и это названо вслух.

    На реальной рамке VK Tech граница в один-два пикселя при ширине 224 —
    седьмая часть клетки, и клетка остаётся прозрачной. Именно поэтому
    тридцать подписей выданного шаблона перестали сужаться.
    """
    assert opacity_grid(FRAME, cols=8, rows=8)[4][0] < 0.5


def test_half_opaque_picture_is_read_as_half():
    grid = opacity_grid(RIGHT_HALF, cols=8, rows=8)
    assert all(v < 0.5 for v in grid[0][:4])
    assert all(v > 0.5 for v in grid[0][4:])


def _png16(width, height, alpha) -> bytes:
    """RGBA-PNG с 16 битами на канал: альфа двумя байтами, старший первым."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            a = alpha(x, y)
            raw += bytes((0, 0, 0, 0, 0, 0, a, a))
    ihdr = struct.pack(">IIBBBBB", width, height, 16, 6, 0, 0, 0)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (SIGNATURE + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def test_sixteen_bits_per_channel_are_parsed():
    """Шестнадцать бит на канал разбираются, и это проверено, а не обещано.

    `DOM-PKG §10` и карточка `Z-50` утверждают, что 8 и 16 бит разбираются, а
    отказ остаётся у 1/2/4. До 21 сентября утверждение стояло **без теста**:
    поймано при разборе «что ещё надо проверить».
    """
    grid = opacity_grid(_png16(32, 32, lambda x, y: 255 if x >= 16 else 0),
                        cols=8, rows=8)
    assert grid is not None, "16 бит обязаны разбираться"
    assert all(v < 0.5 for v in grid[0][:4])
    assert all(v > 0.5 for v in grid[0][4:])


def test_four_bits_per_channel_are_a_refusal():
    """А 1, 2 и 4 бита — отказ, и это тоже проверено.

    Подбайтовые отсчёты распаковывать мы не умеем; ответ «судить не по чему»
    (`Z-50`). Без этой проверки перечень в документах был бы ничем не подпёрт.
    """
    ihdr = struct.pack(">IIBBBBB", 8, 8, 4, 6, 0, 0, 0)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)

    data = (SIGNATURE + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(8 * 17))) + chunk(b"IEND", b""))
    assert opacity_grid(data) is None


def test_jpeg_has_no_alpha_and_counts_as_opaque():
    assert opacity_grid(b"\xff\xd8\xff\xe0" + b"\x00" * 64)[0][0] == 1.0


def test_interlaced_png_is_a_refusal_not_a_verdict():
    """«Проверить не смог» обязано отличаться от «чисто» (`Z-20`)."""
    assert opacity_grid(_png(8, 8, lambda x, y: 255, interlace=1)) is None


def test_unknown_format_is_a_refusal():
    assert opacity_grid(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None


def test_every_png_filter_gives_the_same_answer():
    """Разбирается один канал из четырёх, и это не приближение.

    Любой фильтр PNG ссылается на тот же канал предыдущего пикселя или строки
    выше. Если бы это было неверно, фильтры расходились бы между собой.
    """
    plain = opacity_grid(RIGHT_HALF, cols=8, rows=8)
    for filter_kind in (1, 2, 3, 4):
        data = bytearray(zlib.decompress(_idat(RIGHT_HALF)))
        rows = _refilter(data, 32, 32, filter_kind)
        assert opacity_grid(_rebuild(RIGHT_HALF, rows), cols=8, rows=8) == plain


def _idat(png: bytes) -> bytes:
    out, i = b"", 8
    while i < len(png):
        size, kind = struct.unpack(">I4s", png[i:i + 8])
        if kind == b"IDAT":
            out += png[i + 8:i + 8 + size]
        i += 12 + size
    return out


def _refilter(raw: bytes, width: int, height: int, kind: int) -> bytes:
    """Перезаписать те же пиксели другим фильтром строки."""
    stride = width * 4
    out = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
        out.append(kind)
        enc = bytearray(stride)
        for i in range(stride):
            left = line[i - 4] if i >= 4 else 0
            up = prev[i]
            upleft = prev[i - 4] if i >= 4 else 0
            if kind == 1:
                base = left
            elif kind == 2:
                base = up
            elif kind == 3:
                base = (left + up) >> 1
            else:
                guess = left + up - upleft
                da, db, dc = abs(guess - left), abs(guess - up), abs(guess - upleft)
                base = left if (da <= db and da <= dc) else (up if db <= dc else upleft)
            enc[i] = (line[i] - base) & 0xFF
        out += enc
        prev = line
    return bytes(out)


def _rebuild(png: bytes, raw: bytes) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)
    head = png[:8 + 25]                                  # подпись и IHDR
    return head + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


# --- кто кого накрывает ------------------------------------------------


BOX = shape("1", x=0, y=0, cx=1000, cy=100, text="текст")


def test_nothing_above_means_no_occluder():
    """Пустой список «того, что сверху» — заслонений нет.

    **Этот тест не про порядок отрисовки, и раньше утверждал обратное.** Он
    назывался «карточка снизу не заслоняет» и передавал пустой список: карточки
    в нём не было вовсе, и провалиться он не мог ни при какой поломке `z`.
    Поймано мутацией 21 сентября. Порядок отрисовки проверяет тест ниже.
    """
    assert occluding_rects(BOX, [], bytes_of({})) == []


def test_only_shapes_drawn_later_occlude():
    """Карточка **под** текстом не заслоняет его, фигура **над** — заслоняет.

    Отбор «что лежит сверху» живёт не в `occluding_rects`, а в `_slots`: там
    берутся фигуры, идущие в `p:spTree` позже нашей. До 21 сентября это не
    проверял ни один тест **прямо** — поломку ловил только
    `test_the_ban_does_not_cost_content`, тест про исключительных доноров,
    и ловил случайно, через изменившийся состав колоды.

    Без этой проверки карточка, накрывающая свой же текст на 100%, обнулила бы
    его слот — а это самая частая раскладка корпуса.
    """
    from mimeo.analyze.patterns import _slots
    from mimeo.analyze.shapes import SlideObs

    card = shape("10", x=0, y=0, cx=1000, cy=100, fill="solid")      # ниже текста
    text = shape("11", x=0, y=0, cx=1000, cy=100, text="текст слота")
    over = shape("12", x=600, y=0, cx=400, cy=100, fill="solid")     # выше текста
    slide = SlideObs(index=0, part="/ppt/slides/slide1.xml",
                     background=None, shapes=[card, text, over])

    class _Stub:
        type_scale = ()

    slots = _slots([text], _Stub(), 18.0, None, slide=slide,
                   image_bytes=lambda part: None, unhandled=[], opaque={})
    assert len(slots) == 1
    visible = slots[0].visible
    assert visible.cx == 600, (
        "карточка снизу не должна резать слот, а фигура сверху должна — "
        f"осталось {visible.cx} из 1000"
    )


def test_a_transparent_shape_does_not_occlude():
    over = shape("2", x=500, y=0, cx=500, cy=100, fill="none")
    assert occluding_rects(BOX, [over], bytes_of({})) == []


def test_someone_elses_text_is_neighbourhood_not_occlusion():
    over = shape("2", x=500, y=0, cx=500, cy=100, fill="solid", text="чужое")
    assert occluding_rects(BOX, [over], bytes_of({})) == []


def test_hidden_shape_does_not_occlude():
    over = shape("2", x=500, y=0, cx=500, cy=100, fill="solid", hidden=True)
    assert occluding_rects(BOX, [over], bytes_of({})) == []


def test_solid_shape_occludes_by_its_box():
    over = shape("2", x=500, y=0, cx=500, cy=100, fill="solid")
    assert [(i, r.x, r.cx) for i, r in occluding_rects(BOX, [over], bytes_of({}))] \
        == [("2", 500, 500)]


def test_frame_picture_occludes_only_its_edges():
    """Главная ловушка: рамка накрывает бокс целиком, а прячет только края.

    Бокс лежит в средних строках картинки — там, где у рамки только боковые
    границы. Считались бы колонки общим размахом «от левой непрозрачной до
    правой», рамка стала бы сплошной плашкой и слот обнулился бы.
    """
    over = shape("2", x=0, y=-100, cx=1000, cy=300, kind="pic", image="f.png")
    found = occluding_rects(BOX, [over], bytes_of({"f.png": FRAME}))
    assert len(found) == 2, "у рамки две непрозрачные колонки, а не одна плашка"
    assert visible_rect(BOX.rect, found).cx > BOX.rect.cx * 0.9


def test_half_opaque_picture_cuts_the_slot_in_half():
    over = shape("2", x=0, y=0, cx=1000, cy=100, kind="pic", image="h.png")
    found = occluding_rects(BOX, [over], bytes_of({"h.png": RIGHT_HALF}))
    assert visible_rect(BOX.rect, found).cx == 500


def test_unreadable_picture_leaves_the_slot_alone_and_says_so():
    over = shape("2", x=0, y=0, cx=1000, cy=100, kind="pic", image="i.png")
    said: list[tuple[str, str]] = []
    bad = _png(8, 8, lambda x, y: 255, interlace=1)
    found = occluding_rects(BOX, [over], bytes_of({"i.png": bad}), said)
    assert found == [] and said, "молчаливый пропуск неотличим от «чисто»"
    assert visible_rect(BOX.rect, found).cx == BOX.rect.cx


# --- свободная полоса --------------------------------------------------


def test_a_blocker_grazing_the_edge_is_not_a_border():
    """Помеха, задевшая бокс уголком, ширину не режет."""
    graze = [("2", Rect(500, 90, 500, 200))]              # перекрывает 10 из 100
    assert visible_rect(BOX.rect, graze).cx == 1000


def test_a_blocker_in_the_middle_leaves_the_wider_strip():
    middle = [("2", Rect(700, 0, 100, 100))]
    assert visible_rect(BOX.rect, middle).cx == 700


def test_a_blocker_covering_everything_leaves_nothing():
    whole = [("2", Rect(-10, 0, 1020, 100))]
    assert visible_rect(BOX.rect, whole).cx == 0


# --- контракт ----------------------------------------------------------


def test_schema_allows_a_slot_covered_whole():
    """Полностью закрытый слот даёт `cx = 0`, и схема обязана это принять.

    **Дефект, пойманный запуском `--validate` на живом шаблоне 21 сентября.**
    Схема для `visible_rect_emu` была скопирована с `rect_emu`, где нулевой
    ширины быть не может, — и `patterns.json` выданного VK Tech **не проходил
    собственную схему** в четырёх местах. Фикстуры теста контракта такого слота
    не содержали, поэтому прогон был зелёным.
    """
    import json
    import os

    import pytest

    jsonschema = pytest.importorskip("jsonschema")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "contracts", "pattern-library.schema.json"),
              encoding="utf-8") as fh:
        schema = json.load(fh)
    slot_props = (schema["properties"]["patterns"]["items"]["properties"]
                  ["slots"]["items"]["properties"])
    zero = {"x": 0, "y": 0, "cx": 0, "cy": 100}

    jsonschema.Draft202012Validator(slot_props["visible_rect_emu"]).validate(zero)
    with pytest.raises(jsonschema.ValidationError):
        # А у бокса нулевой ширины по-прежнему быть не может: это разные вещи.
        jsonschema.Draft202012Validator(slot_props["rect_emu"]).validate(zero)


# --- запас на переполнение ---------------------------------------------


def _slot(visible_cx):
    rect = Rect(0, 0, 1000, 100)
    return Slot(id="s01", role="body", content_type="text", rect=rect,
                type_role=None, capacity=None, required=True, shape_id="1",
                visible=Rect(0, 0, visible_cx, 100))


def test_an_occluded_slot_gets_no_slack():
    """Переполнение терпят потому, что его чинит VERIFY. Здесь не чинит.

    Замер: сузить ёмкость было мало. На VK Tech ёмкость упала с 39 знаков до
    32, а PLAN положил туда 49 — полтора потолка.
    """
    assert _hard_limit(_slot(600)) == 1.0
    assert _hard_limit(_slot(1000)) == _OVERFLOW_HARD
