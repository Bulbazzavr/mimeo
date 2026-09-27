"""Геометрия фигур для HTML: предустановленные формы и `a:custGeom` в пути SVG.

DOM-GEOM §3. Предустановки описаны в ECMA-376 (presetShapeDefinitions.xml) на
языке формул; здесь переписаны самые ходовые из них прямо на Python — по
замеру корпуса это `rect`, `roundRect`, `ellipse`, соединительные линии и ещё
три десятка форм. Незнакомая предустановка рисуется прямоугольником, и
вызывающий узнаёт об этом по `Geometry.approx`: молча заменять форму нельзя.

`a:custGeom` разбирается честно: направляющие (`a:gdLst`) считаются тем же
вычислителем формул, что и в стандарте, пути масштабируются из своих `w`/`h`
в размер фигуры.

Все координаты — EMU в системе фигуры; в строку пути они переводятся с
масштабом, который задаёт вызывающий (пункты для `viewBox`, доли для
`clipPathUnits="objectBoundingBox"`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ..oxml.ns import local_name, qn

#: 60000-е доли градуса — единица углов DrawingML. DOM-GEOM §2.
_DEG = 60000.0


def num(v: float, nd: int = 3) -> str:
    """Число в строку без хвостовых нулей: вывод обязан быть коротким и
    одинаковым от прогона к прогону."""
    s = f"{v:.{nd}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "", "-") else s


# --- построитель пути --------------------------------------------------------


class Path:
    """Один `a:path`: команды в EMU фигуры плюс режимы заливки и обводки."""

    def __init__(self, fill: str = "norm", stroke: bool = True) -> None:
        self.fill = fill          # norm | none | lighten | lightenLess | darken | darkenLess
        self.stroke = stroke
        self.cmds: list[tuple] = []
        self._cur = (0.0, 0.0)
        self._start = (0.0, 0.0)

    def M(self, x: float, y: float) -> Path:
        self.cmds.append(("M", x, y))
        self._cur = self._start = (x, y)
        return self

    def L(self, x: float, y: float) -> Path:
        self.cmds.append(("L", x, y))
        self._cur = (x, y)
        return self

    def C(self, x1: float, y1: float, x2: float, y2: float, x: float, y: float) -> Path:
        self.cmds.append(("C", x1, y1, x2, y2, x, y))
        self._cur = (x, y)
        return self

    def Q(self, x1: float, y1: float, x: float, y: float) -> Path:
        self.cmds.append(("Q", x1, y1, x, y))
        self._cur = (x, y)
        return self

    def Z(self) -> Path:
        self.cmds.append(("Z",))
        self._cur = self._start
        return self

    def A(self, wr: float, hr: float, st: float, sw: float) -> Path:
        """`a:arcTo`: дуга эллипса от текущей точки. Углы — в градусах.

        Угол DrawingML геометрический (луч из центра), а не параметрический;
        пересчёт такой же, как в Apache POI (`ArcToCommand`). Дуга режется на
        куски не больше 90°, чтобы у SVG не было неоднозначности флагов.
        """
        x0, y0 = self._cur
        if wr <= 0 or hr <= 0 or sw == 0:
            return self
        t1 = _param(wr, hr, st)
        t2 = _param(wr, hr, st + sw)
        cx = x0 - wr * math.cos(t1)
        cy = y0 - hr * math.sin(t1)
        total = t2 - t1
        if abs(sw) >= 360:
            total = math.copysign(2 * math.pi, sw)
        steps = max(1, math.ceil(abs(total) / (math.pi / 2) - 1e-9))
        sweep = 1 if sw > 0 else 0
        for k in range(1, steps + 1):
            t = t1 + total * k / steps
            x, y = cx + wr * math.cos(t), cy + hr * math.sin(t)
            self.cmds.append(("A", wr, hr, sweep, x, y))
            self._cur = (x, y)
        return self

    def d(self, sx: float, sy: float, nd: int = 2) -> str:
        """Строка `d` для SVG с масштабом по осям."""
        out: list[str] = []
        for c in self.cmds:
            op = c[0]
            if op in ("M", "L"):
                out.append(f"{op}{num(c[1] * sx, nd)} {num(c[2] * sy, nd)}")
            elif op == "C":
                out.append("C" + " ".join(num(v * (sx if i % 2 == 0 else sy), nd)
                                          for i, v in enumerate(c[1:])))
            elif op == "Q":
                out.append("Q" + " ".join(num(v * (sx if i % 2 == 0 else sy), nd)
                                          for i, v in enumerate(c[1:])))
            elif op == "A":
                _, wr, hr, sweep, x, y = c
                out.append(f"A{num(wr * sx, nd)} {num(hr * sy, nd)} 0 0 {sweep} "
                           f"{num(x * sx, nd)} {num(y * sy, nd)}")
            elif op == "Z":
                out.append("Z")
        return "".join(out)


def _param(wr: float, hr: float, deg: float) -> float:
    """Геометрический угол -> параметрический, без разрыва на ±180°."""
    a = math.radians(deg)
    p = math.atan2(wr * math.sin(a), hr * math.cos(a))
    return p + 2 * math.pi * round((a - p) / (2 * math.pi))


@dataclass
class Geometry:
    """Итог разбора формы: как её рисовать и где у неё текст."""

    kind: str                    # rect | roundRect | ellipse | path
    paths: list[Path] = field(default_factory=list)
    radius: float = 0.0          # для roundRect, EMU
    text_rect: tuple[float, float, float, float] | None = None  # l, t, r, b в EMU
    approx: str | None = None    # что пришлось приблизить — в отчёт
    line_like: bool = False      # открытая линия: заливки нет, только обводка


# --- предустановки --------------------------------------------------------------


def _adj(av: dict[str, float], name: str, default: float) -> float:
    return av.get(name, default)


def _pin(lo: float, v: float, hi: float) -> float:
    return lo if v < lo else min(v, hi)


def _rect_path(w: float, h: float) -> Path:
    return Path().M(0, 0).L(w, 0).L(w, h).L(0, h).Z()


def _ellipse_path(w: float, h: float) -> Path:
    wd2, hd2 = w / 2, h / 2
    return (Path().M(0, hd2).A(wd2, hd2, 180, 90).A(wd2, hd2, 270, 90)
            .A(wd2, hd2, 0, 90).A(wd2, hd2, 90, 90).Z())


def _ellipse_point(wr: float, hr: float, deg: float) -> tuple[float, float]:
    t = _param(wr, hr, deg)
    return wr * math.cos(t), hr * math.sin(t)


def _poly(points: list[tuple[float, float]]) -> Path:
    p = Path().M(*points[0])
    for pt in points[1:]:
        p.L(*pt)
    return p.Z()


def _g_rect(w, h, av):
    return [_rect_path(w, h)], None


def _g_round_rect(w, h, av):
    ss = min(w, h)
    x1 = ss * _pin(0, _adj(av, "adj", 16667), 50000) / 100000
    p = (Path().M(0, x1).A(x1, x1, 180, 90).L(w - x1, 0).A(x1, x1, 270, 90)
         .L(w, h - x1).A(x1, x1, 0, 90).L(x1, h).A(x1, x1, 90, 90).Z())
    il = x1 * 0.29289
    return [p], (il, il, w - il, h - il)


def _g_ellipse(w, h, av):
    dx, dy = w / 2 * 0.70711, h / 2 * 0.70711
    return [_ellipse_path(w, h)], (w / 2 - dx, h / 2 - dy, w / 2 + dx, h / 2 + dy)


def _g_line(w, h, av):
    return [Path(fill="none").M(0, 0).L(w, h)], None


def _g_line_inv(w, h, av):
    return [Path(fill="none").M(0, h).L(w, 0)], None


def _g_triangle(w, h, av):
    a = _pin(0, _adj(av, "adj", 50000), 100000)
    x1, x2 = w * a / 200000, w * a / 100000
    return [_poly([(0, h), (x2, 0), (w, h)])], (x1, h / 2, x1 + w / 2, h)


def _g_rt_triangle(w, h, av):
    return [_poly([(0, h), (0, 0), (w, h)])], (w / 12, h * 7 / 12, w * 7 / 12, h * 11 / 12)


def _g_diamond(w, h, av):
    return ([_poly([(0, h / 2), (w / 2, 0), (w, h / 2), (w / 2, h)])],
            (w / 4, h / 4, w * 3 / 4, h * 3 / 4))


def _g_parallelogram(w, h, av):
    ss = min(w, h)
    a = _pin(0, _adj(av, "adj", 25000), 100000 * w / ss if ss else 0)
    x2 = ss * a / 100000
    return [_poly([(0, h), (x2, 0), (w, 0), (w - x2, h)])], (x2 / 2, 0, w - x2 / 2, h)


def _g_trapezoid(w, h, av):
    ss = min(w, h)
    a = _pin(0, _adj(av, "adj", 25000), 50000 * w / ss if ss else 0)
    x2 = ss * a / 100000
    return [_poly([(0, h), (x2, 0), (w - x2, 0), (w, h)])], (x2 * 2 / 3, 0, w - x2 * 2 / 3, h)


def _g_input_output(w, h, av):
    return [_poly([(0, h), (w / 5, 0), (w, 0), (w * 4 / 5, h)])], (w / 10, 0, w * 9 / 10, h)


def _g_pentagon(w, h, av):
    hf, vf = _adj(av, "hf", 105146) / 100000, _adj(av, "vf", 110557) / 100000
    swd2, shd2, svc = w / 2 * hf, h / 2 * vf, h / 2 * vf
    hc = w / 2
    dx1, dx2 = swd2 * math.cos(math.radians(18)), swd2 * math.cos(math.radians(54))
    dy1, dy2 = shd2 * math.sin(math.radians(18)), shd2 * math.sin(math.radians(54))
    pts = [(hc - dx1, svc - dy1), (hc, 0), (hc + dx1, svc - dy1),
           (hc + dx2, svc + dy2), (hc - dx2, svc + dy2)]
    return [_poly(pts)], (hc - dx2, svc - dy1, hc + dx2, svc + dy2)


def _g_home_plate(w, h, av):
    ss = min(w, h)
    a = _pin(0, _adj(av, "adj", 50000), 100000 * w / ss if ss else 0)
    x1 = w - ss * a / 100000
    return [_poly([(0, 0), (x1, 0), (w, h / 2), (x1, h), (0, h)])], (0, 0, (x1 + w) / 2, h)


def _g_chevron(w, h, av):
    ss = min(w, h)
    a = _pin(0, _adj(av, "adj", 50000), 100000 * w / ss if ss else 0)
    x1 = ss * a / 100000
    x2 = w - x1
    return ([_poly([(0, 0), (x2, 0), (w, h / 2), (x2, h), (0, h), (x1, h / 2)])],
            (x1, 0, x2, h))


def _g_hexagon(w, h, av):
    ss = min(w, h)
    a = _pin(0, _adj(av, "adj", 25000), 50000 * w / ss if ss else 0)
    x1 = ss * a / 100000
    x2 = w - x1
    return ([_poly([(0, h / 2), (x1, 0), (x2, 0), (w, h / 2), (x2, h), (x1, h)])],
            (x1 / 2, h / 8, w - x1 / 2, h * 7 / 8))


def _g_octagon(w, h, av):
    ss = min(w, h)
    x1 = ss * _pin(0, _adj(av, "adj", 29289), 50000) / 100000
    x2, y2 = w - x1, h - x1
    pts = [(0, x1), (x1, 0), (x2, 0), (w, x1), (w, y2), (x2, h), (x1, h), (0, y2)]
    return [_poly(pts)], (x1 / 2, x1 / 2, w - x1 / 2, h - x1 / 2)


def _regular(n: int, w: float, h: float, hf: float = 1.0, vf: float = 1.0) -> Path:
    """Правильный многоугольник, вписанный в рамку; вершина сверху."""
    pts = []
    for k in range(n):
        ang = math.radians(-90 + 360 * k / n)
        pts.append((w / 2 + w / 2 * hf * math.cos(ang), h / 2 * vf + h / 2 * vf * math.sin(ang)))
    return _poly(pts)


def _star(n: int, default_adj: float, hf: float = 1.0, vf: float = 1.0):
    def g(w, h, av):
        a = _pin(0, _adj(av, "adj", default_adj), 50000)
        hf_ = _adj(av, "hf", hf * 100000) / 100000
        vf_ = _adj(av, "vf", vf * 100000) / 100000
        rx, ry = w / 2 * hf_, h / 2 * vf_
        cx, cy = w / 2, h / 2 * vf_
        pts = []
        for k in range(2 * n):
            ang = math.radians(-90 + 180 * k / n)
            f = 1.0 if k % 2 == 0 else a / 50000
            pts.append((cx + rx * f * math.cos(ang), cy + ry * f * math.sin(ang)))
        ir = a / 50000
        return [_poly(pts)], (cx - rx * ir * 0.7, cy - ry * ir * 0.7,
                              cx + rx * ir * 0.7, cy + ry * ir * 0.7)
    return g


def _polygon(n: int, hf: float = 1.0, vf: float = 1.0):
    def g(w, h, av):
        hf_ = _adj(av, "hf", hf * 100000) / 100000
        vf_ = _adj(av, "vf", vf * 100000) / 100000
        return [_regular(n, w, h, hf_, vf_)], (w * 0.2, h * 0.2, w * 0.8, h * 0.8)
    return g


def _g_round1_rect(w, h, av):
    ss = min(w, h)
    d = ss * _pin(0, _adj(av, "adj", 16667), 50000) / 100000
    p = Path().M(0, 0).L(w - d, 0).A(d, d, 270, 90).L(w, h).L(0, h).Z()
    return [p], (0, 0, w - d * 0.29289, h)


def _g_round2_same(w, h, av):
    ss = min(w, h)
    t = ss * _pin(0, _adj(av, "adj1", 16667), 50000) / 100000
    b = ss * _pin(0, _adj(av, "adj2", 0), 50000) / 100000
    p = Path().M(t, 0).L(w - t, 0).A(t, t, 270, 90).L(w, h - b).A(b, b, 0, 90)
    p.L(b, h).A(b, b, 90, 90).L(0, t).A(t, t, 180, 90).Z()
    il = max(t, b) * 0.29289
    return [p], (il, t * 0.29289, w - il, h - b * 0.29289)


def _g_round2_diag(w, h, av):
    ss = min(w, h)
    x1 = ss * _pin(0, _adj(av, "adj1", 16667), 50000) / 100000
    a = ss * _pin(0, _adj(av, "adj2", 0), 50000) / 100000
    p = Path().M(x1, 0).L(w - a, 0).A(a, a, 270, 90).L(w, h - x1).A(x1, x1, 0, 90)
    p.L(a, h).A(a, a, 90, 90).L(0, x1).A(x1, x1, 180, 90).Z()
    il = max(x1, a) * 0.29289
    return [p], (il, il, w - il, h - il)


def _g_snip1(w, h, av):
    ss = min(w, h)
    d = ss * _pin(0, _adj(av, "adj", 16667), 50000) / 100000
    return [_poly([(0, 0), (w - d, 0), (w, d), (w, h), (0, h)])], (0, d / 2, w - d / 2, h)


def _g_snip2_same(w, h, av):
    ss = min(w, h)
    t = ss * _pin(0, _adj(av, "adj1", 16667), 50000) / 100000
    b = ss * _pin(0, _adj(av, "adj2", 0), 50000) / 100000
    pts = [(t, 0), (w - t, 0), (w, t), (w, h - b), (w - b, h), (b, h), (0, h - b), (0, t)]
    return [_poly(pts)], (max(t, b) / 2, t / 2, w - max(t, b) / 2, h - b / 2)


def _g_snip2_diag(w, h, av):
    ss = min(w, h)
    lt = ss * _pin(0, _adj(av, "adj1", 0), 50000) / 100000
    rt = ss * _pin(0, _adj(av, "adj2", 16667), 50000) / 100000
    pts = [(lt, 0), (w - rt, 0), (w, rt), (w, h - lt), (w - lt, h), (rt, h), (0, h - rt), (0, lt)]
    return [_poly(pts)], (max(lt, rt) / 2, max(lt, rt) / 2, w - max(lt, rt) / 2, h - max(lt, rt) / 2)


def _g_plus(w, h, av):
    ss = min(w, h)
    x1 = ss * _pin(0, _adj(av, "adj", 25000), 50000) / 100000
    x2, y2 = w - x1, h - x1
    pts = [(0, x1), (x1, x1), (x1, 0), (x2, 0), (x2, x1), (w, x1), (w, y2), (x2, y2),
           (x2, h), (x1, h), (x1, y2), (0, y2)]
    return [_poly(pts)], (0, x1, w, y2)


def _g_frame(w, h, av):
    ss = min(w, h)
    x1 = ss * _pin(0, _adj(av, "adj1", 12500), 50000) / 100000
    p = _rect_path(w, h)
    p.M(x1, x1).L(x1, h - x1).L(w - x1, h - x1).L(w - x1, x1).Z()
    return [p], (x1, x1, w - x1, h - x1)


def _g_right_arrow(w, h, av):
    ss = min(w, h)
    a1 = _pin(0, _adj(av, "adj1", 50000), 100000)
    a2 = _pin(0, _adj(av, "adj2", 50000), 100000 * w / ss if ss else 0)
    x1 = w - ss * a2 / 100000
    dy1 = h * a1 / 200000
    y1, y2 = h / 2 - dy1, h / 2 + dy1
    pts = [(0, y1), (x1, y1), (x1, 0), (w, h / 2), (x1, h), (x1, y2), (0, y2)]
    x2 = x1 + (y1 * (w - x1) / (h / 2) if h else 0)
    return [_poly(pts)], (0, y1, x2, y2)


def _g_left_arrow(w, h, av):
    ss = min(w, h)
    a1 = _pin(0, _adj(av, "adj1", 50000), 100000)
    a2 = _pin(0, _adj(av, "adj2", 50000), 100000 * w / ss if ss else 0)
    x2 = ss * a2 / 100000
    dy1 = h * a1 / 200000
    y1, y2 = h / 2 - dy1, h / 2 + dy1
    pts = [(0, h / 2), (x2, 0), (x2, y1), (w, y1), (w, y2), (x2, y2), (x2, h)]
    return [_poly(pts)], (x2 / 2, y1, w, y2)


def _g_up_arrow(w, h, av):
    ss = min(w, h)
    a1 = _pin(0, _adj(av, "adj1", 50000), 100000)
    a2 = _pin(0, _adj(av, "adj2", 50000), 100000 * h / ss if ss else 0)
    y2 = ss * a2 / 100000
    dx1 = w * a1 / 200000
    x1, x2 = w / 2 - dx1, w / 2 + dx1
    pts = [(0, y2), (w / 2, 0), (w, y2), (x2, y2), (x2, h), (x1, h), (x1, y2)]
    return [_poly(pts)], (x1, y2 / 2, x2, h)


def _g_down_arrow(w, h, av):
    ss = min(w, h)
    a1 = _pin(0, _adj(av, "adj1", 50000), 100000)
    a2 = _pin(0, _adj(av, "adj2", 50000), 100000 * h / ss if ss else 0)
    y1 = h - ss * a2 / 100000
    dx1 = w * a1 / 200000
    x1, x2 = w / 2 - dx1, w / 2 + dx1
    pts = [(0, y1), (x1, y1), (x1, 0), (x2, 0), (x2, y1), (w, y1), (w / 2, h)]
    return [_poly(pts)], (x1, 0, x2, (y1 + h) / 2)


def _g_left_right_arrow(w, h, av):
    ss = min(w, h)
    a1 = _pin(0, _adj(av, "adj1", 50000), 100000)
    a2 = _pin(0, _adj(av, "adj2", 50000), 50000 * w / ss if ss else 0)
    x2 = ss * a2 / 100000
    x3 = w - x2
    dy = h * a1 / 200000
    y1, y2 = h / 2 - dy, h / 2 + dy
    pts = [(0, h / 2), (x2, 0), (x2, y1), (x3, y1), (x3, 0), (w, h / 2), (x3, h), (x3, y2),
           (x2, y2), (x2, h)]
    return [_poly(pts)], (x2 / 2, y1, w - x2 / 2, y2)


def _angles(av: dict[str, float], d1: float, d2: float) -> tuple[float, float]:
    st = _pin(0, _adj(av, "adj1", d1), 21599999) / _DEG
    en = _pin(0, _adj(av, "adj2", d2), 21599999) / _DEG
    sw = en - st
    if sw <= 0:
        sw += 360
    return st, sw


def _g_pie(w, h, av):
    st, sw = _angles(av, 0, 16200000)
    wd2, hd2 = w / 2, h / 2
    dx, dy = _ellipse_point(wd2, hd2, st)
    p = Path().M(wd2 + dx, hd2 + dy).A(wd2, hd2, st, sw).L(wd2, hd2).Z()
    return [p], (w * 0.15, h * 0.15, w * 0.85, h * 0.85)


def _g_arc(w, h, av):
    st, sw = _angles(av, 16200000, 0)
    wd2, hd2 = w / 2, h / 2
    dx, dy = _ellipse_point(wd2, hd2, st)
    fill = Path(stroke=False).M(wd2 + dx, hd2 + dy).A(wd2, hd2, st, sw).L(wd2, hd2).Z()
    line = Path(fill="none").M(wd2 + dx, hd2 + dy).A(wd2, hd2, st, sw)
    return [fill, line], None


def _g_chord(w, h, av):
    st, sw = _angles(av, 2700000, 16200000)
    wd2, hd2 = w / 2, h / 2
    dx, dy = _ellipse_point(wd2, hd2, st)
    return [Path().M(wd2 + dx, hd2 + dy).A(wd2, hd2, st, sw).Z()], None


def _g_block_arc(w, h, av):
    st = _pin(0, _adj(av, "adj1", 10800000), 21599999) / _DEG
    en = _pin(0, _adj(av, "adj2", 0), 21599999) / _DEG
    sw = en - st
    if sw <= 0:
        sw += 360
    ss = min(w, h)
    dr = ss * _pin(0, _adj(av, "adj3", 25000), 50000) / 100000
    wd2, hd2 = w / 2, h / 2
    iw, ih = max(wd2 - dr, 0), max(hd2 - dr, 0)
    ox, oy = _ellipse_point(wd2, hd2, st)
    p = Path().M(wd2 + ox, hd2 + oy).A(wd2, hd2, st, sw)
    ix, iy = _ellipse_point(iw, ih, st + sw)
    p.L(wd2 + ix, hd2 + iy).A(iw, ih, st + sw, -sw).Z()
    return [p], None


def _g_donut(w, h, av):
    ss = min(w, h)
    dr = ss * _pin(0, _adj(av, "adj", 25000), 50000) / 100000
    wd2, hd2 = w / 2, h / 2
    iw, ih = max(wd2 - dr, 0), max(hd2 - dr, 0)
    p = _ellipse_path(w, h)
    p.M(dr, hd2).A(iw, ih, 180, -90).A(iw, ih, 90, -90).A(iw, ih, 0, -90).A(iw, ih, 270, -90).Z()
    return [p], None


def _g_teardrop(w, h, av):
    a = _pin(0, _adj(av, "adj", 100000), 200000)
    wd2, hd2 = w / 2, h / 2
    sw_, sh_ = math.sqrt(2) * wd2 * a / 100000, math.sqrt(2) * hd2 * a / 100000
    x1 = wd2 + sw_ * math.cos(math.radians(45))
    y1 = hd2 - sh_ * math.sin(math.radians(45))
    x2, y2 = (wd2 + x1) / 2, (hd2 + y1) / 2
    p = (Path().M(0, hd2).A(wd2, hd2, 180, 90).Q(x2, 0, x1, y1).Q(w, y2, w, hd2)
         .A(wd2, hd2, 0, 90).A(wd2, hd2, 90, 90).Z())
    dx, dy = wd2 * 0.70711, hd2 * 0.70711
    return [p], (wd2 - dx, hd2 - dy, wd2 + dx, hd2 + dy)


def _g_can(w, h, av):
    ss = min(w, h)
    y1 = ss * _pin(0, _adj(av, "adj", 25000), 50000 * h / ss if ss else 0) / 200000
    wd2 = w / 2
    body = Path().M(0, y1).A(wd2, y1, 180, -180).L(w, h - y1).A(wd2, y1, 0, 180).Z()
    top = Path(fill="lighten", stroke=False).M(0, y1).A(wd2, y1, 180, 180).A(wd2, y1, 0, 180).Z()
    outline = Path(fill="none").M(w, y1).A(wd2, y1, 0, 180).A(wd2, y1, 180, 180).L(w, h - y1)
    outline.A(wd2, y1, 0, 180).L(0, y1)
    return [body, top, outline], (0, y1 * 2, w, h - y1)


def _g_cube(w, h, av):
    ss = min(w, h)
    y1 = ss * _pin(0, _adj(av, "adj", 25000), 100000) / 100000
    x4, y4 = w - y1, h - y1
    front = _poly([(0, y1), (x4, y1), (x4, h), (0, h)])
    top = _poly([(0, y1), (y1, 0), (w, 0), (x4, y1)])
    top.fill = "lightenLess"
    side = _poly([(x4, h), (x4, y1), (w, 0), (w, y4)])
    side.fill = "darkenLess"
    return [front, top, side], (0, y1, x4, h)


def _g_wedge_rect_callout(w, h, av):
    dxp = w * _adj(av, "adj1", -20833) / 100000
    dyp = h * _adj(av, "adj2", 62500) / 100000
    xp, yp = w / 2 + dxp, h / 2 + dyp
    horiz = abs(dxp) * h > abs(dyp) * w
    if not horiz and dyp > 0:
        x1 = w * (7 / 12 if dxp > 0 else 2 / 12)
        pts = [(0, 0), (w, 0), (w, h), (x1 + w / 6, h), (xp, yp), (x1, h), (0, h)]
    elif not horiz:
        x1 = w * (7 / 12 if dxp > 0 else 2 / 12)
        pts = [(0, 0), (x1, 0), (xp, yp), (x1 + w / 6, 0), (w, 0), (w, h), (0, h)]
    elif dxp > 0:
        y1 = h * (7 / 12 if dyp > 0 else 2 / 12)
        pts = [(0, 0), (w, 0), (w, y1), (xp, yp), (w, y1 + h / 6), (w, h), (0, h)]
    else:
        y1 = h * (7 / 12 if dyp > 0 else 2 / 12)
        pts = [(0, 0), (w, 0), (w, h), (0, h), (0, y1 + h / 6), (xp, yp), (0, y1)]
    return [_poly(pts)], (0, 0, w, h)


def _g_terminator(w, h, av):
    rx = w * 3475 / 21600
    p = (Path().M(rx, 0).L(w - rx, 0).A(rx, h / 2, 270, 180).L(rx, h)
         .A(rx, h / 2, 90, 180).Z())
    return [p], (w * 1018 / 21600, h * 3163 / 21600, w * 20582 / 21600, h * 18437 / 21600)


def _g_predefined_process(w, h, av):
    x1, x2 = w / 8, w * 7 / 8
    frame = _rect_path(w, h)
    bars = Path(fill="none").M(x1, 0).L(x1, h).M(x2, 0).L(x2, h)
    return [frame, bars], (x1, 0, x2, h)


def _g_document(w, h, av):
    y = h * 17322 / 21600
    p = (Path().M(0, 0).L(w, 0).L(w, y)
         .C(w * 10800 / 21600, y, w * 10800 / 21600, h * 23922 / 21600, 0, h * 20172 / 21600).Z())
    return [p], (0, 0, w, y)


def _g_bent_connector3(w, h, av):
    x1 = w * _adj(av, "adj1", 50000) / 100000
    return [Path(fill="none").M(0, 0).L(x1, 0).L(x1, h).L(w, h)], None


def _g_bent_connector2(w, h, av):
    return [Path(fill="none").M(0, 0).L(w, 0).L(w, h)], None


def _g_curved_connector3(w, h, av):
    x2 = w * _adj(av, "adj1", 50000) / 100000
    x1, x3 = x2 / 2, (w + x2) / 2
    p = Path(fill="none").M(0, 0).C(x1, 0, x2, h / 4, x2, h / 2).C(x2, h * 3 / 4, x3, h, w, h)
    return [p], None


def _g_bracket(side: str):
    def g(w, h, av):
        ss = min(w, h)
        y1 = ss * _pin(0, _adj(av, "adj", 8333), 50000) / 100000
        if side == "left":
            p = Path(fill="none").M(w, h).A(w, y1, 90, 90).L(0, y1).A(w, y1, 180, 90)
        else:
            p = Path(fill="none").M(0, 0).A(w, y1, 270, 90).L(w, h - y1).A(w, y1, 0, 90)
        return [p], None
    return g


def _g_bracket_pair(w, h, av):
    ss = min(w, h)
    x1 = ss * _pin(0, _adj(av, "adj", 16667), 50000) / 100000
    left = Path(fill="none").M(x1, h).A(x1, x1, 90, 90).L(0, x1).A(x1, x1, 180, 90)
    right = Path(fill="none").M(w - x1, 0).A(x1, x1, 270, 90).L(w, h - x1).A(x1, x1, 0, 90)
    return [left, right], (x1 * 0.29, x1 * 0.29, w - x1 * 0.29, h - x1 * 0.29)


#: Имя предустановки -> функция. Синонимы блок-схем — те же формы. Список
#: взят из замера корпуса и общей частоты форм; остальное — прямоугольник.
_PRESETS = {
    "rect": _g_rect,
    "flowChartProcess": _g_rect,
    "actionButtonBlank": _g_rect,
    "roundRect": _g_round_rect,
    "ellipse": _g_ellipse,
    "flowChartConnector": _g_ellipse,
    "line": _g_line,
    "straightConnector1": _g_line,
    "lineInv": _g_line_inv,
    "triangle": _g_triangle,
    "flowChartExtract": lambda w, h, av: _g_triangle(w, h, {}),
    "rtTriangle": _g_rt_triangle,
    "diamond": _g_diamond,
    "flowChartDecision": _g_diamond,
    "parallelogram": _g_parallelogram,
    "flowChartInputOutput": _g_input_output,
    "trapezoid": _g_trapezoid,
    "pentagon": _g_pentagon,
    "homePlate": _g_home_plate,
    "chevron": _g_chevron,
    "hexagon": _g_hexagon,
    "octagon": _g_octagon,
    "heptagon": _polygon(7, 1.02572, 1.05210),
    "decagon": _polygon(10, 1.0, 1.05146),
    "dodecagon": _polygon(12),
    "round1Rect": _g_round1_rect,
    "round2SameRect": _g_round2_same,
    "round2DiagRect": _g_round2_diag,
    "snip1Rect": _g_snip1,
    "snip2SameRect": _g_snip2_same,
    "snip2DiagRect": _g_snip2_diag,
    "plus": _g_plus,
    "flowChartAlternateProcess": lambda w, h, av: _g_round_rect(w, h, {"adj": 16667}),
    "flowChartTerminator": _g_terminator,
    "flowChartPredefinedProcess": _g_predefined_process,
    "flowChartDocument": _g_document,
    "frame": _g_frame,
    "rightArrow": _g_right_arrow,
    "leftArrow": _g_left_arrow,
    "upArrow": _g_up_arrow,
    "downArrow": _g_down_arrow,
    "leftRightArrow": _g_left_right_arrow,
    "pie": _g_pie,
    "arc": _g_arc,
    "chord": _g_chord,
    "blockArc": _g_block_arc,
    "donut": _g_donut,
    "teardrop": _g_teardrop,
    "can": _g_can,
    "cube": _g_cube,
    "wedgeRectCallout": _g_wedge_rect_callout,
    "star4": _star(4, 12500),
    "star5": _star(5, 19098, 1.05146, 1.10557),
    "star6": _star(6, 28868, 1.0, 1.0),
    "star7": _star(7, 34601, 1.02572, 1.05210),
    "star8": _star(8, 37500),
    "star10": _star(10, 42533, 1.05146, 1.0),
    "star12": _star(12, 37500),
    "star16": _star(16, 37500),
    "star24": _star(24, 37500),
    "star32": _star(32, 37500),
    "bentConnector2": _g_bent_connector2,
    "bentConnector3": _g_bent_connector3,
    "curvedConnector3": _g_curved_connector3,
    "leftBracket": _g_bracket("left"),
    "rightBracket": _g_bracket("right"),
    "bracketPair": _g_bracket_pair,
}

#: Формы, которые по смыслу — отрезок или ломаная: у них нет заливки.
_LINE_LIKE = {
    "line", "straightConnector1", "lineInv", "bentConnector2", "bentConnector3",
    "curvedConnector3", "leftBracket", "rightBracket", "bracketPair",
}

#: Приближения, которые не стыдно показать: форма узнаётся, но не точна.
_ALIASES = {
    "bentConnector4": "bentConnector3", "bentConnector5": "bentConnector3",
    "curvedConnector2": "curvedConnector3", "curvedConnector4": "curvedConnector3",
    "curvedConnector5": "curvedConnector3", "notchedRightArrow": "rightArrow",
    "stripedRightArrow": "rightArrow", "wedgeRoundRectCallout": "wedgeRectCallout",
    "wedgeEllipseCallout": "ellipse", "nonIsoscelesTrapezoid": "trapezoid",
    "flowChartManualOperation": "trapezoid", "flowChartPreparation": "hexagon",
    "flowChartMerge": "triangle", "flowChartOnlineStorage": "roundRect",
    "flowChartMagneticDisk": "can", "snipRoundRect": "round1Rect",
    "plaque": "roundRect", "bevel": "rect", "foldedCorner": "rect",
    "flowChartOffpageConnector": "homePlate", "flowChartSort": "diamond",
    "flowChartCollate": "diamond", "flowChartOr": "ellipse",
    "flowChartSummingJunction": "ellipse", "smileyFace": "ellipse",
    "noSmoking": "donut", "moon": "ellipse", "sun": "ellipse", "cloud": "ellipse",
    "heart": "ellipse", "halfFrame": "frame", "corner": "rect",
    "mathPlus": "plus", "mathMinus": "rect", "mathMultiply": "plus",
    "upDownArrow": "upArrow", "quadArrow": "plus", "leftRightUpArrow": "plus",
    "rightArrowCallout": "rightArrow", "leftArrowCallout": "leftArrow",
    "upArrowCallout": "upArrow", "downArrowCallout": "downArrow",
    "leftBrace": "leftBracket", "rightBrace": "rightBracket", "bracePair": "bracketPair",
}


def _av(prst: ET.Element | None) -> dict[str, float]:
    """`a:avLst` предустановки: `<a:gd name="adj" fmla="val 2381"/>`."""
    out: dict[str, float] = {}
    if prst is None:
        return out
    lst = prst.find(qn("a:avLst"))
    if lst is None:
        return out
    for gd in lst.findall(qn("a:gd")):
        parts = (gd.get("fmla") or "").split()
        if len(parts) == 2 and parts[0] == "val":
            try:
                out[gd.get("name") or ""] = float(parts[1])
            except ValueError:
                continue
    return out


def preset(name: str, w: float, h: float, av: dict[str, float] | None = None) -> Geometry:
    """Предустановка в геометрию. Незнакомая — прямоугольник с пометкой."""
    av = av or {}
    approx = None
    fn = _PRESETS.get(name)
    if fn is None and name in _ALIASES:
        approx = f"prstGeom {name}"
        fn = _PRESETS[_ALIASES[name]]
        av = {}
    if fn is None:
        approx = f"prstGeom {name}"
        fn = _g_rect
    paths, text_rect = fn(w, h, av)
    kind = "path"
    radius = 0.0
    if fn is _g_rect:
        kind = "rect"
    elif fn is _g_ellipse:
        kind = "ellipse"
    elif name == "roundRect":
        kind = "roundRect"
        radius = min(w, h) * _pin(0, av.get("adj", 16667), 50000) / 100000
    return Geometry(kind=kind, paths=paths, radius=radius, text_rect=text_rect,
                    approx=approx, line_like=name in _LINE_LIKE)


# --- a:custGeom: вычислитель формул ------------------------------------------


def _builtins(w: float, h: float) -> dict[str, float]:
    """Встроенные направляющие стандарта (ECMA-376, 20.1.9.11)."""
    ss, ls = min(w, h), max(w, h)
    g = {
        "w": w, "h": h, "l": 0.0, "t": 0.0, "r": w, "b": h, "hc": w / 2, "vc": h / 2,
        "ss": ss, "ls": ls,
        "cd2": 10800000.0, "cd4": 5400000.0, "cd8": 2700000.0,
        "3cd4": 16200000.0, "3cd8": 8100000.0, "5cd8": 13500000.0, "7cd8": 18900000.0,
    }
    for n in (2, 3, 4, 5, 6, 8, 10, 12, 32):
        g[f"wd{n}"] = w / n
        g[f"hd{n}"] = h / n
    for n in (2, 4, 6, 8, 16, 32):
        g[f"ssd{n}"] = ss / n
    return g


def _ang(v: float) -> float:
    return math.radians(v / _DEG)


def _eval(fmla: str, env: dict[str, float]) -> float:
    parts = fmla.split()
    if not parts:
        return 0.0

    def a(i: int) -> float:
        tok = parts[i] if i < len(parts) else "0"
        if tok in env:
            return env[tok]
        try:
            return float(tok)
        except ValueError:
            return 0.0

    op = parts[0]
    if op == "val":
        return a(1)
    if op == "*/":
        z = a(3)
        return a(1) * a(2) / z if z else 0.0
    if op == "+-":
        return a(1) + a(2) - a(3)
    if op == "+/":
        z = a(3)
        return (a(1) + a(2)) / z if z else 0.0
    if op == "?:":
        return a(2) if a(1) > 0 else a(3)
    if op == "abs":
        return abs(a(1))
    if op == "at2":
        return math.degrees(math.atan2(a(2), a(1))) * _DEG
    if op == "cat2":
        return a(1) * math.cos(math.atan2(a(3), a(2)))
    if op == "sat2":
        return a(1) * math.sin(math.atan2(a(3), a(2)))
    if op == "cos":
        return a(1) * math.cos(_ang(a(2)))
    if op == "sin":
        return a(1) * math.sin(_ang(a(2)))
    if op == "tan":
        return a(1) * math.tan(_ang(a(2)))
    if op == "max":
        return max(a(1), a(2))
    if op == "min":
        return min(a(1), a(2))
    if op == "mod":
        return math.sqrt(a(1) ** 2 + a(2) ** 2 + a(3) ** 2)
    if op == "pin":
        lo, v, hi = a(1), a(2), a(3)
        return lo if v < lo else min(v, hi)
    if op == "sqrt":
        return math.sqrt(max(a(1), 0.0))
    return 0.0


def _guides(geom: ET.Element, env: dict[str, float]) -> None:
    for lst in ("a:avLst", "a:gdLst"):
        holder = geom.find(qn(lst))
        if holder is None:
            continue
        for gd in holder.findall(qn("a:gd")):
            name = gd.get("name")
            if name:
                try:
                    env[name] = _eval(gd.get("fmla") or "", env)
                except (OverflowError, ValueError, ZeroDivisionError):
                    env[name] = 0.0


def _val(tok: str | None, env: dict[str, float]) -> float:
    if tok is None:
        return 0.0
    if tok in env:
        return env[tok]
    try:
        return float(tok)
    except ValueError:
        return 0.0


def custom(geom: ET.Element, w: float, h: float) -> Geometry:
    """`a:custGeom` в геометрию. Команды пути — ECMA-376, 20.1.9.15."""
    env = _builtins(w, h)
    _guides(geom, env)
    paths: list[Path] = []
    lst = geom.find(qn("a:pathLst"))
    for pe in (lst.findall(qn("a:path")) if lst is not None else []):
        pw, ph = _val(pe.get("w"), env), _val(pe.get("h"), env)
        sx = w / pw if pw else 1.0
        sy = h / ph if ph else 1.0
        stroke = pe.get("stroke") not in ("0", "false")
        p = Path(fill=pe.get("fill") or "norm", stroke=stroke)

        def pt(el: ET.Element | None, sx: float = sx, sy: float = sy) -> tuple[float, float]:
            if el is None:
                return 0.0, 0.0
            return _val(el.get("x"), env) * sx, _val(el.get("y"), env) * sy

        for cmd in pe:
            op = local_name(cmd.tag)
            pts = [pt(c) for c in cmd.findall(qn("a:pt"))]
            if op == "moveTo" and pts:
                p.M(*pts[0])
            elif op == "lnTo" and pts:
                p.L(*pts[0])
            elif op == "cubicBezTo" and len(pts) >= 3:
                p.C(*pts[0], *pts[1], *pts[2])
            elif op == "quadBezTo" and len(pts) >= 2:
                p.Q(*pts[0], *pts[1])
            elif op == "arcTo":
                p.A(_val(cmd.get("wR"), env) * sx, _val(cmd.get("hR"), env) * sy,
                    _val(cmd.get("stAng"), env) / _DEG, _val(cmd.get("swAng"), env) / _DEG)
            elif op == "close":
                p.Z()
        if p.cmds:
            paths.append(p)
    rect = geom.find(qn("a:rect"))
    text_rect = None
    if rect is not None:
        text_rect = (_val(rect.get("l"), env), _val(rect.get("t"), env),
                     _val(rect.get("r"), env), _val(rect.get("b"), env))
    return Geometry(kind="path", paths=paths, text_rect=text_rect)


def shape_geometry(sp_pr_geom: ET.Element | None, w: float, h: float) -> Geometry:
    """Геометрия по элементу `a:prstGeom` / `a:custGeom`; нет элемента —
    прямоугольник (так PowerPoint рисует фигуру без формы)."""
    if sp_pr_geom is None:
        return preset("rect", w, h)
    if sp_pr_geom.tag == qn("a:custGeom"):
        return custom(sp_pr_geom, w, h)
    return preset(sp_pr_geom.get("prst") or "rect", w, h, _av(sp_pr_geom))
