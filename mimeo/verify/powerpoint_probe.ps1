# Габариты текста всех фигур собранной колоды. Шаг 1 плана `PLAN-4.0`.
#
# Открывает все файлы списка в ОДНОМ сеансе PowerPoint. Отдельный запуск на файл
# ловил гонку: предыдущий экземпляр ещё не вышел, зонд отказывался работать, а
# вызывающая сторона видела пустой ответ и молча считала ноль фигур.
#
# Вход: -ListFile — текстовый файл UTF-8, по одному пути на строку.
# Выход: строки key=value в UTF-8 (кодировка задаётся явно ниже).
#   page  — габариты слайда;
#   box   — прямоугольник КАЖДОЙ фигуры, в том числе без текста: свободное
#           место занимает что угодно (`PLAN-4.2`);
#   shape — то же плюс габариты набранного текста, поля, кегль, якорь.
# Файлы
# обозначаются НОМЕРОМ строки списка, а не именем: так короче и не зависит
# от того, что консоль сделает с нелатинским путём.
param(
    [Parameter(Mandatory=$true)][string]$ListFile
)

$ErrorActionPreference = 'Stop'

# Вывод в трубу — строго UTF-8. Без этой строки PowerShell пишет в кодировке
# консоли (на русской Windows cp866), Python читает как UTF-8 с
# `errors="replace"`, и текст ошибки COM уничтожается: от причины отказа
# остаётся один HRESULT. Замерено 13 сентября, `WORKLOG/2026-09-13-verify-loop.md`.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$inv = [System.Globalization.CultureInfo]::InvariantCulture
function Num($d) { return ([math]::Round([double]$d, 2)).ToString($inv) }
function Clean($s) { return ($s -replace "[`r`n]+", ' ') }

# Освободить обёртку COM. Без этого приложение остаётся в памяти, пока живы
# переменные, и следующий замер упирается в BUSY.
function Release($obj) {
    if ($null -eq $obj) { return }
    try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($obj) } catch {}
}

# PowerPoint — одноэкземплярный COM-сервер: New-Object подключится к уже
# открытому у пользователя приложению, и Quit() закроет его документы.
$pre = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
if ($pre.Count -gt 0) { Write-Output 'result=BUSY'; exit 3 }

$decks = @(Get-Content -LiteralPath $ListFile -Encoding UTF8 | Where-Object { $_.Trim() -ne '' })
if ($decks.Count -eq 0) { Write-Output 'result=OK'; exit 0 }

$app = $null
try {
    $app = New-Object -ComObject PowerPoint.Application
    $app.DisplayAlerts = 2
    Write-Output ("version=" + $app.Version)

    # Своих экземпляров до запуска не было (иначе вышли бы по BUSY), значит
    # любой появившийся — наш, и завершить его в конце мы вправе.
    $mine = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue)
    if ($mine.Count -eq 1) { $ownPid = $mine[0].Id } else { $ownPid = 0 }

    for ($d = 0; $d -lt $decks.Count; $d++) {
        Write-Output ("deck=" + $d)
        $pres = $null
        $col = $null
        try {
            # Open(FileName, ReadOnly, Untitled, WithWindow), MsoTriState -1 / 0
            $col = $app.Presentations
            $pres = $col.Open($decks[$d], -1, 0, 0)
        }
        catch {
            Write-Output ("status=open_failed")
            Write-Output ("note=" + (Clean $_.Exception.Message))
            continue
        }
        try {
            Write-Output ("slides=" + $pres.Slides.Count)
            # Край слайда — такая же граница, как соседняя фигура (`PLAN-4.2`).
            Write-Output ("page w=" + (Num $pres.PageSetup.SlideWidth) +
                          " h=" + (Num $pres.PageSetup.SlideHeight))
            for ($i = 1; $i -le $pres.Slides.Count; $i++) {
                $stack = New-Object System.Collections.Stack
                $slides = $pres.Slides
                $slide = $slides.Item($i)
                $shapes = $slide.Shapes
                Write-Output ("onslide=$i count=" + $shapes.Count)
                # Порядок отрисовки: ZOrderPosition считается ВНУТРИ своего
                # контейнера, поэтому у ребёнка группы он сравним только с
                # соседями по группе. Составной ключ строится позиционно: свой
                # разряд на каждый уровень вложенности, шаг делится на тысячу
                # при спуске. Тогда вся группа лежит между соседями по слайду,
                # а дети различимы внутри неё (`Z-48`, `DOM-PKG §11`).
                foreach ($sh in $shapes) {
                    $z = 0
                    try { $z = [int]$sh.ZOrderPosition } catch {}
                    $stack.Push(@($sh, ([long]$z * 1000000), [long]1000))
                }
                Release $shapes; Release $slide; Release $slides
                $shapes = $null; $slide = $null; $slides = $null
                while ($stack.Count -gt 0) {
                    $pair = $stack.Pop()
                    $sh = $pair[0]
                    $z = $pair[1]
                    $step = $pair[2]
                    $tf2 = $null
                    $tr = $null
                    try {
                        # msoGroup = 6: без захода внутрь половина фигур не видна
                        if ($sh.Type -eq 6) {
                            $kids = $sh.GroupItems
                            # Разряд своего уровня, а не умножение всего
                            # ключа. `$z * 1000 + $kz` поднимал ребёнка группы
                            # НАД фигурами, которые лежат выше всей группы:
                            # ребёнок группы №5 получал 5000007 против 10000 у
                            # одиннадцатой фигуры слайда. Отсюда два ложных
                            # заслонения из одиннадцати, оба проверены растром
                            # (`Z-48`, `WORKLOG/2026-09-21-z48-baseline.md`).
                            #
                            # Глубже трёх уровней шаг вырождается в ноль, и дети
                            # получают `z` родителя — ровно прежнее поведение.
                            # Это предел разрядной сетки, названный вслух: такой
                            # вложенности в корпусе нет.
                            foreach ($kid in $kids) {
                                $kz = 0
                                try { $kz = [int]$kid.ZOrderPosition } catch {}
                                $stack.Push(@($kid, ($z + [long]$kz * $step),
                                              [long]($step / 1000)))
                            }
                            Release $kids
                            $kids = $null
                            continue
                        }
                        # Раньше исключение здесь глоталось молча, и фигура
                        # исчезала из замера бесследно. Молчаливый пропуск
                        # неотличим от «текста нет».
                        # Бокс печатается для КАЖДОЙ фигуры, а не только
                        # текстовой: свободное место занимает что угодно.
                        # Замер: текст есть у 143 фигур корпуса из 2209
                        # (`WORKLOG/2026-09-13-autofit.md`).
                        $vis = 0
                        try { if ($sh.Visible -eq -1) { $vis = 1 } } catch {}
                        # Непрозрачность заливки. Без неё «фигура поверх текста»
                        # ничего не значит: прозрачная рамка накрывает текст на
                        # 100% и не мешает ему совсем. Замер 20 сентября поймал
                        # ровно такой ложный случай (`Z-47`).
                        # 0 — заливки нет или она полностью прозрачна.
                        # У КАРТИНКИ заливки нет: её изображение — не Fill, и
                        # `Fill.Visible` там ложь. Первая редакция этого не
                        # учла и выбросила настоящий дефект — текст под
                        # декоративной картинкой на VK Tech (`Z-47`).
                        # msoPicture = 13, msoLinkedPicture = 11.
                        $stype = 0
                        try { $stype = [int]$sh.Type } catch {}
                        $opq = 0
                        if ($stype -eq 13 -or $stype -eq 11) {
                            $opq = 1
                        } else {
                            try {
                                if ($sh.Fill.Visible -eq -1) {
                                    $opq = [math]::Round(1.0 - $sh.Fill.Transparency, 3)
                                }
                            } catch {}
                        }
                        if ($opq -lt 0) { $opq = 0 }
                        Write-Output ("box slide=$i id=" + $sh.Id +
                                      " x=" + (Num $sh.Left) + " y=" + (Num $sh.Top) +
                                      " w=" + (Num $sh.Width) + " h=" + (Num $sh.Height) +
                                      " vis=" + $vis + " z=" + $z + " opq=" + $opq + " t=" + $stype)
                        $hasTf = $false
                        try { $hasTf = ($sh.HasTextFrame -eq -1) }
                        catch { Write-Output ("skipped=HasTextFrame " + (Clean $_.Exception.Message)); continue }
                        if (-not $hasTf) { continue }
                        $tf2 = $sh.TextFrame2
                        if ($tf2.HasText -ne -1) { continue }
                        $tr = $tf2.TextRange
                        if ($tr.Length -le 0) { continue }
                        # Настоящий угол набранного текста и поворот фигуры
                        # (`Z-53`, `PLAN-7.12`). Угол бокса плюс поле совпадает
                        # с углом текста только у якоря «верх»: у середины и
                        # низа он не там у 52 из 56 и 19 из 21 надписей девятки
                        # (`WORKLOG/2026-09-23-z53-baseline.md`). Отдельным
                        # `try`: отказ здесь не должен выбрасывать фигуру из
                        # замера целиком — пустое значение значит «не знаем».
                        $bl = ''; $bt = ''; $rot = ''
                        try { $bl = Num $tr.BoundLeft; $bt = Num $tr.BoundTop } catch {}
                        try { $rot = Num $sh.Rotation } catch {}
                        $vals = @(
                            "slide=$i"
                            "id=" + $sh.Id
                            "w=" + (Num $sh.Width)
                            "h=" + (Num $sh.Height)
                            "tw=" + (Num $tr.BoundWidth)
                            "th=" + (Num $tr.BoundHeight)
                            "ml=" + (Num $tf2.MarginLeft)
                            "mr=" + (Num $tf2.MarginRight)
                            "mt=" + (Num $tf2.MarginTop)
                            "mb=" + (Num $tf2.MarginBottom)
                            "fit=" + $tf2.AutoSize
                            # ВИДИМЫЙ кегль: при normAutofit PowerPoint уже
                            # применил шкалу, и здесь приходит результат, а не
                            # номинал. Проверено замером: 16 pt при шкале 62%
                            # приходит как 10. -2 — в фигуре разные размеры.
                            "sz=" + (Num $tr.Font.Size)
                            "len=" + $tr.Length
                            "x=" + (Num $sh.Left)
                            "y=" + (Num $sh.Top)
                            # MsoVerticalAnchor: 1 верх, 3 середина, 4 низ; 2 и 5 —
                            # верх и низ по базовой линии. Проверено положением
                            # набранного текста 23 сентября: у 3 он точно по
                            # середине бокса у 478 надписей, у 4 точно по низу у
                            # 165. `space.py` пока читает 3 как низ — `Z-54`.
                            # Задаёт, КУДА растёт непоместившийся текст.
                            "anchor=" + $tf2.VerticalAnchor
                            "bl=" + $bl
                            "bt=" + $bt
                            "rot=" + $rot
                        )
                        Write-Output ("shape " + ($vals -join ' '))
                    }
                    catch {
                        # Отдельная фигура может уронить вызов COM. Одна плохая
                        # не должна обнулять замер всей колоды — но и молчать о
                        # ней нельзя.
                        Write-Output ("skipped=" + (Clean $_.Exception.Message))
                    }
                    finally {
                        # Каждая фигура, каркас и диапазон — отдельная обёртка
                        # COM. Пока переменная в области видимости, сборщик её
                        # не тронет, и PowerPoint не завершится: второй замер
                        # подряд получал BUSY. Освобождаем явно.
                        Release $tr
                        Release $tf2
                        Release $sh
                        $tr = $null; $tf2 = $null; $sh = $null
                    }
                }
                $stack = $null
            }
            Write-Output 'status=ok'
        }
        catch {
            Write-Output 'status=walk_failed'
            Write-Output ("note=" + (Clean $_.Exception.Message))
        }
        finally {
            try { if ($pres) { $pres.Close() } } catch {}
            Release $pres; Release $col
            $pres = $null; $col = $null
        }
    }
    $app.Quit()
    Write-Output 'result=OK'
}
catch {
    Write-Output 'result=ERROR'
    Write-Output ("note=" + (Clean $_.Exception.Message))
    try { if ($app) { $app.Quit() } } catch {}
}
finally {
    Release $app
    $app = $null
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    [GC]::Collect()
}

# Quit() возвращает управление до того, как процесс исчез: ждать надо процесса,
# а не команды (`WORKLOG/2026-09-10-com-render-probe.md`).
# Ждём недолго: перечисление фигур оставляет обёртки, и приложение почти всегда
# висит дольше. Данные уже собраны, документ закрыт — ждать нечего.
$t0 = Get-Date
$gone = $false
while (((Get-Date) - $t0).TotalSeconds -lt 2) {
    if (-not (Get-Process -Id $ownPid -ErrorAction SilentlyContinue)) { $gone = $true; break }
    Start-Sleep -Milliseconds 200
}
if (-not $gone -and $ownPid -gt 0) {
    # Перечисление фигур оставляет обёртки, которые приложение держат дольше
    # десяти секунд. Экземпляр наш, документ закрыт — завершаем, иначе
    # следующий замер упрётся в BUSY, а петля VERIFY мерит каждый раунд.
    try { Stop-Process -Id $ownPid -Force -ErrorAction Stop; Write-Output 'exit=killed' }
    catch { Write-Output 'exit=stuck' }
} else {
    Write-Output 'exit=clean'
}
