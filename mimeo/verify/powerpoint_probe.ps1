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

# Установленные гарнитуры — по реестру шрифтов системы и пользователя: имя
# значения без хвоста «(TrueType)». Гарнитура «установлена», если есть
# начертание с её именем целиком или с ним и пробелом дальше («Play Bold»);
# просто общее начало не годится — «Play» не должен найтись как «Playbill».
$FALLBACK_FONT = 'Arial'
$installedFonts = @{}
foreach ($root in @('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts',
                    'HKCU:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')) {
    try {
        foreach ($p in (Get-ItemProperty -Path $root -ErrorAction Stop).PSObject.Properties) {
            if ($p.Name -like 'PS*') { continue }
            $installedFonts[(($p.Name -replace '\s*\(.*\)\s*$', '').ToLower())] = 1
        }
    } catch {}
}
function Installed($name) {
    $n = ([string]$name).ToLower()
    foreach ($k in $installedFonts.Keys) {
        if ($k -eq $n -or $k.StartsWith($n + ' ') -or $k.StartsWith($n + ' &') -or $k.Contains(' & ' + $n)) { return $true }
    }
    return $false
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
            # Шрифт колоды, которого нет в системе, — мерить заменой, которой
            # PowerPoint его РИСУЕТ. Замер 27 сентября: у всех трёх выданных
            # шаблонов гарнитура Play встроена в файл, но не установлена;
            # объектная модель раскладывает текст по метрикам встроенного Play
            # (3 строки), а отрисовка — экспорт слайда и PDF — рисует Arial
            # (4 строки), и крупный текст WorkSpace уходил за край слайда при
            # отчёте «влезло». С заменой на Arial замер дал ровно строки растра.
            # Колода открыта только для чтения: замена живёт в памяти сеанса.
            foreach ($f in @($pres.Fonts)) {
                try {
                    $fn = [string]$f.Name
                    if ($fn -and -not (Installed $fn)) {
                        $pres.Fonts.Replace($fn, $FALLBACK_FONT)
                        Write-Output ("fontsub from=" + ($fn -replace '\s', '_') + " to=" + $FALLBACK_FONT)
                    }
                } catch {}
            }
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
                # Фигуры макета и образца — логотип, колонтитул, декор — зритель
                # видит на слайде наравне с его собственными, а зонд до
                # 27 сентября их не видел: крупный текст WorkSpace рос на
                # логотип макета и за край слайда, а мерка звала место под ним
                # свободным (`Z-75`). Они идут только боксами — помехой росту
                # текста: `z` ниже любой фигуры слайда и непрозрачность 0, так
                # что заслонять наш текст они не могут. Заполнители макета на
                # слайде не рисуются — пропускаются. Приставка у `id`: номера
                # фигур макета, образца и слайда пересекаются.
                $layers = @()
                try {
                    $lay = $slide.CustomLayout
                    $layers += ,@('L', $lay.Shapes)
                    if ($slide.DisplayMasterShapes -eq -1 -and $lay.DisplayMasterShapes -eq -1) {
                        $layers += ,@('M', $slide.Master.Shapes)
                    }
                } catch {}
                foreach ($layer in $layers) {
                    foreach ($ls in $layer[1]) {
                        try {
                            if ([int]$ls.Type -eq 14) { continue }
                            if ($ls.Visible -ne -1) { continue }
                            Write-Output ("box slide=$i id=" + $layer[0] + $ls.Id +
                                          " x=" + (Num $ls.Left) + " y=" + (Num $ls.Top) +
                                          " w=" + (Num $ls.Width) + " h=" + (Num $ls.Height) +
                                          " vis=1 z=-1 opq=0 t=" + [int]$ls.Type)
                        } catch {}
                    }
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
                        # Слово, разорванное посередине строки (`Z-56`): строка
                        # кончается буквой — без пробела и без знака абзаца, —
                        # а следующая начинается строчной. Бокс уже самого
                        # длинного слова, и PowerPoint режет его без дефиса.
                        # Ширина слова — хвост в конце строки плюс начало на
                        # следующей: по ней ремонт ужимает кегль. Отдельным
                        # `try`: отказ здесь не выбрасывает фигуру из замера.
                        $brk = 0; $ww = 0.0; $lines = $null
                        try {
                            $lines = $tr.Lines()
                            $nl = $lines.Count
                            $pt = ''; $ps = 0
                            for ($k = 1; $k -le $nl; $k++) {
                                $ln = $tr.Lines($k, 1)
                                $ct = [string]$ln.Text; $cs = [int]$ln.Start
                                Release $ln; $ln = $null
                                if ($pt.Length -gt 0 -and $ct.Length -gt 0 -and
                                    [char]::IsLetter($pt[$pt.Length - 1]) -and [char]::IsLower($ct[0])) {
                                    $brk++
                                    $cut = $pt.LastIndexOfAny([char[]]" `t`v")
                                    $sp = $ct.IndexOfAny([char[]]" `t`r`n`v")
                                    if ($sp -lt 0) { $sp = $ct.Length }
                                    $r1 = $tr.Characters($ps + $cut + 1, $pt.Length - $cut - 1)
                                    $r2 = $tr.Characters($cs, $sp)
                                    $w = [double]$r1.BoundWidth + [double]$r2.BoundWidth
                                    Release $r1; Release $r2; $r1 = $null; $r2 = $null
                                    if ($w -gt $ww) { $ww = $w }
                                }
                                $pt = $ct; $ps = $cs
                            }
                        } catch {
                            Write-Output ("skipped=lines " + (Clean $_.Exception.Message))
                        } finally {
                            Release $lines; $lines = $null
                        }
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
                            "brk=" + $brk
                            "ww=" + (Num $ww)
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
