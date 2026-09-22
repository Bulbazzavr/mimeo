/* Локальный интерфейс mimeo. Задача Z-29, план PLAN-8.0.
 *
 * Ни одной библиотеки: ни фреймворка, ни сборки, ни CDN. Страница простая —
 * форма и панель результата, — и всё, что ей нужно, есть в браузере.
 */

'use strict';

const $ = (id) => document.getElementById(id);

let token = null;

/* Пример из корпуса: показывает, какой вход движок ждёт. Это сокращённый
   основной текст сдачи, examples/content-mimeo.md. */
const EXAMPLE = [
  'Сделай, пожалуйста, презентацию про наш движок mimeo, минут на десять, ',
  'показывать будем техническому заказчику. Расскажи сначала, в чём вообще беда: ',
  'когда нужно собрать презентацию в корпоративном стиле, человек берёт шаблон и ',
  'часами двигает текст по слайдам руками, а генераторы, которые сейчас есть, стиль ',
  'не переносят вовсе. Дальше скажи, что мы делаем иначе: мы не рисуем слайд с нуля, ',
  'мы клонируем слайд самого шаблона вместе со всей его вёрсткой и подставляем туда ',
  'текст. На вход при этом идёт обычная сплошная речь, никакой разметки заполнять не ',
  'надо, движок сам режет её на темы. Ещё важно сказать про разбор шаблона: мы ',
  'вытаскиваем из него дизайн-систему, то есть цвета, шрифты, размеры, и библиотеку ',
  'готовых раскладок, и таких раскладок в живых шаблонах находится от 5 до 49 штук. ',
  'Отдельно нужно рассказать про проверку вёрстки, это наша сильная часть: после ',
  'сборки мы открываем файл настоящим PowerPoint и спрашиваем у него реальные ',
  'габариты каждой надписи, а не гадаем по числу знаков, и если текст не поместился — ',
  'ужимаем кегль и пересобираем. Скажи и про воспроизводимость: один и тот же вход ',
  'даёт байт в байт тот же файл. Про зависимости тоже упомяни: у движка их ноль, он ',
  'работает на голой стандартной библиотеке питона. И обязательно скажи честно, чего ',
  'мы пока не умеем, без этого будет выглядеть как реклама.'
].join('');

function setStatus(text, kind) {
  const el = $('status');
  el.textContent = text || '';
  el.className = 'status' + (kind ? ' ' + kind : '');
}

function escape(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/* --- загрузка шаблона -------------------------------------------------- */

$('template').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  token = null;
  if (!file) {
    $('template-name').textContent = 'Файл не выбран — нужен .pptx или .potx';
    return;
  }
  $('template-name').textContent = 'Загружается…';
  try {
    const response = await fetch('/api/template?name=' + encodeURIComponent(file.name), {
      method: 'POST',
      body: file
    });
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'не загрузился');
    token = data.token;
    const mb = (data.bytes / 1048576).toFixed(1);
    $('template-name').textContent = data.name + ' — ' + mb + ' МБ, загружен';
    setStatus('');
    loadDesign();
  } catch (error) {
    $('template-name').textContent = 'Не загрузился: ' + error.message;
  }
});

$('fill-example').addEventListener('click', () => { $('text').value = EXAMPLE; });

/* --- что вынули из шаблона (PLAN-8.1, часть A) -------------------------- */

/* Запрашивается сразу после загрузки файла и НЕ задерживает форму: человек в
   это время печатает текст. Разбор стоит 0.4 с на лёгком шаблоне и 4.6 с на
   выданном VK Tech с его 54 слайдами — к нажатию «Собрать» он уже готов. */
async function loadDesign() {
  const panel = $('design');
  const body = $('design-body');
  panel.hidden = false;
  body.innerHTML = '<p class="hint">Разбираем шаблон…</p>';
  try {
    const response = await fetch('/api/design?token=' + encodeURIComponent(token));
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'не разобрался');
    body.innerHTML = designHtml(data);
  } catch (error) {
    /* Пустая панель читалась бы как «в шаблоне ничего нет». Это разные вещи. */
    body.innerHTML = '<p class="hint">Разобрать шаблон не вышло: '
      + escape(error.message) + '. На сборку это не влияет — попробуйте собрать.</p>';
  }
}

function swatch(hex, title) {
  return '<span class="sw" style="background:' + escape(hex || '#fff') + '"'
    + ' title="' + escape(title || hex || '') + '"></span>';
}

function designHtml(d) {
  const facts = [
    [d.source.slides, 'слайдов в шаблоне'],
    [d.source.layouts, 'макетов'],
    [d.patterns, 'раскладок извлекли'],
    [d.type_scale.length, 'типо-ролей'],
    [d.palette.observed_total, 'цветов найдено'],
    [d.slide.aspect, 'пропорция']
  ].filter((f) => f[0] !== null && f[0] !== undefined);

  let html = '<div class="facts">' + facts.map((f) =>
    '<div class="fact"><div class="n">' + escape(String(f[0])) + '</div>'
    + '<div class="k">' + escape(f[1]) + '</div></div>').join('') + '</div>';

  if (d.palette.core.length) {
    html += '<h3 class="sub">Ядро палитры — ' + d.palette.core.length + '</h3>'
      + '<div class="swatches">'
      + d.palette.core.map((hex) => swatch(hex, hex)).join('') + '</div>';
  }
  if (d.palette.theme.length) {
    html += '<h3 class="sub">Роли темы</h3><div class="swatches">'
      + d.palette.theme.map((t) => swatch(t.hex, t.role + ' ' + t.hex)).join('')
      + '</div><p class="hint">Наведите на квадрат — покажет роль и код цвета.</p>';
  }
  if (d.type_scale.length) {
    html += '<h3 class="sub">Типографическая шкала</h3>'
      + '<table class="scale"><tr><th>Роль</th><th>Гарнитура</th><th class="num">Кегль</th>'
      + '<th class="num">Мест</th><th>Пример из вашего шаблона</th></tr>'
      + d.type_scale.map((t) => {
        let example;
        if (t.example) {
          example = '<span class="ex">' + escape(t.example) + '</span>';
        } else if (t.pictogram) {
          example = '<span class="none">пиктограммы, не текст</span>';
        } else {
          example = '<span class="none">нет текста</span>';
        }
        return '<tr>'
          + '<td>' + escape(t.role || '—') + '</td>'
          + '<td>' + (t.font
              ? escape(t.font)
              : '<span class="none">от темы</span>') + '</td>'
          + '<td class="num">' + (t.size_pt !== null ? escape(String(t.size_pt)) : '—') + '</td>'
          + '<td class="num">' + (t.count !== null ? escape(String(t.count)) : '—') + '</td>'
          + '<td>' + (t.color_hex ? '<span class="dot" style="background:'
              + escape(t.color_hex) + '"></span>' : '') + example + '</td>'
          + '</tr>';
      }).join('') + '</table>';
  }

  const g = d.grid;
  if (g.samples) {
    const m = [g.margin_top_in, g.margin_right_in, g.margin_bottom_in, g.margin_left_in]
      .map((v) => (v === null ? '?' : v)).join(' / ');
    html += '<h3 class="sub">Сетка</h3><p class="hint">Поля сверху/справа/снизу/слева: '
      + escape(m) + ' дюйма. Средник: '
      + escape(g.gutter_in === null ? 'не выведен' : g.gutter_in + ' дюйма')
      + '. Выведено по ' + g.samples + ' замерам фигур шаблона.</p>';
  }

  if (!d.patterns) {
    html += '<p class="hint"><strong>Раскладок не извлечено.</strong> У шаблона нет '
      + 'слайдов-доноров — так бывает у .potx. Движок возьмёт макеты, и колода '
      + 'соберётся, но выбор будет беднее.</p>';
  }
  return html;
}

/* --- сборка ------------------------------------------------------------ */

$('go').addEventListener('click', async () => {
  if (!token) return setStatus('Сначала выберите шаблон.', 'error');
  const text = $('text').value.trim();
  if (!text) return setStatus('Вставьте текст — движку нечего раскладывать.', 'error');

  const verify = $('verify').checked;
  $('go').disabled = true;
  /* Индикатор нужен только для проверки вёрстки: обычная сборка идёт доли
     секунды, а проверка — десятки. Замер в SPEC-WEB, раздел 5. */
  setStatus(verify
    ? 'Собираем и проверяем вёрстку в PowerPoint — это десятки секунд…'
    : 'Собираем…', 'working');

  try {
    const response = await fetch('/api/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: token,
        text: text,
        slides: $('slides').value.trim(),
        variants: parseInt($('variants').value, 10) || 1,
        verify: verify
      })
    });
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'сборка не удалась');
    render(data);
    setStatus('Готово за ' + data.seconds + ' с.');
  } catch (error) {
    setStatus('Не вышло: ' + error.message, 'error');
  } finally {
    $('go').disabled = false;
  }
});

/* --- показ результата --------------------------------------------------- */

function render(data) {
  const decks = data.report.decks || [];
  $('decks').innerHTML = decks.map((deck, n) => {
    const name = deck.variant === null
      ? 'Колода'
      : 'Вариант вёрстки ' + deck.variant;
    const problems = deck.structural_problems === 0
      ? 'структура непротиворечива'
      : deck.structural_problems + ' структурных проблем';
    return '<div class="deck">'
      + '<span class="title">' + escape(name) + '</span>'
      + '<span class="meta">' + deck.slides + ' слайдов, ' + escape(problems) + '</span>'
      + '<button type="button" class="link show-slides" data-n="' + n + '">Показать слайды</button>'
      + '<a href="/api/deck?token=' + encodeURIComponent(token) + '&n=' + n + '">Скачать .pptx</a>'
      + '</div>'
      + '<div class="strip" id="strip-' + n + '"></div>';
  }).join('');

  $('decks').querySelectorAll('.show-slides').forEach((b) => {
    b.addEventListener('click', () => showSlides(parseInt(b.dataset.n, 10), b));
  });

  /* Просили больше, чем вышло, — это надо видеть НЕ раскрывая «подробности».
     Движок объясняет причину словами; молча показать пять колод вместо девяти
     значит соврать отчётом, который формально правдив. */
  /* Сколько просили — берём из ответа сервера, а не из поля формы: поле можно
     тронуть после сборки, и подпись начнёт врать о том, чего не просили. */
  const asked = data.variants_asked || 1;
  const short = decks.length < asked
    ? '<div class="verdict unknown"><h3>Вариантов вышло меньше, чем просили: '
      + decks.length + ' из ' + asked + '</h3><p>' + escape(shortfallReason(data))
      + '</p></div>'
    : '';

  /* Проверку не просили — говорим это ОДИН раз на всю сборку, а не по разу на
     каждый вариант: пять одинаковых абзацев подряд перестают читать, и тогда
     предупреждение не работает вовсе. Найдено глазами на девяти вариантах. */
  $('verify-block').innerHTML = short + (data.verify_requested
    ? verifyBlock(data, decks)
    : notChecked());

  const warnings = (data.report.diagnostics && data.report.diagnostics.warnings) || [];
  const unplaced = (data.report.diagnostics && data.report.diagnostics.unplaced) || [];
  $('warn-count').textContent = '(' + warnings.length + ')';
  const items = unplaced.map((id) =>
      '<li class="unplaced">Раздел «' + escape(id) + '» не разместился ни в одной раскладке — '
      + 'содержание потеряно.</li>')
    .concat(warnings.map((w) => '<li>' + escape(w) + '</li>'));
  $('diagnostics').innerHTML = items.length
    ? '<ul>' + items.join('') + '</ul>'
    : '<p class="hint">Движку сказать нечего: всё разместилось без оговорок.</p>';

  $('result').hidden = false;
  $('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* --- превью слайдов (PLAN-8.2, часть B) --------------------------------- */

/* Рисует тот же PowerPoint, которым стадия VERIFY меряет вёрстку. Значит это
   не картинка рядом с отчётом, а та самая вёрстка, о которой отчёт говорит
   числами: видно «переполнений было 10, стало 7» — и видно, где именно. */
async function showSlides(n, button) {
  const strip = $('strip-' + n);
  button.disabled = true;
  strip.innerHTML = '<p class="hint">Открываем колоду в PowerPoint и снимаем слайды…</p>';
  try {
    const response = await fetch('/api/preview?token=' + encodeURIComponent(token) + '&n=' + n);
    const data = await response.json();
    if (!data.ok) {
      /* «Занят» — это не отказ, а состояние, и у человека есть что с ним
         сделать. Сводить его к общему «не смогли» значит отнять действие. */
      strip.innerHTML = '<div class="verdict ' + (data.busy ? 'unknown' : 'skipped') + '">'
        + '<h3>' + (data.busy ? 'PowerPoint занят' : 'Показать слайды не вышло') + '</h3>'
        + '<p>' + escape(data.error || '') + '</p></div>';
      return;
    }
    strip.innerHTML = data.slides.map((src, i) =>
      '<a href="' + src + '" target="_blank" rel="noopener" title="Слайд ' + (i + 1)
      + ' — открыть целиком"><img src="' + src + '" alt="Слайд ' + (i + 1) + '" loading="lazy">'
      + '<span>' + (i + 1) + '</span></a>').join('');
    button.textContent = 'Слайды показаны';
    return;
  } catch (error) {
    strip.innerHTML = '<p class="hint">Показать слайды не вышло: ' + escape(error.message) + '</p>';
  } finally {
    button.disabled = false;
  }
}

/* Причина недобора приходит от движка первым предупреждением. Своими словами
   её не пересказываем: там названы и число разных колод, и порог расхождения. */
function shortfallReason(data) {
  const warnings = (data.report.diagnostics && data.report.diagnostics.warnings) || [];
  const found = warnings.find((w) => w.indexOf('Запрошено вариантов') === 0);
  return found || 'Движок не назвал причину — это само по себе странно.';
}

/* Три исхода проверки вёрстки, и они РАЗНЫЕ.
 *
 * Это главное правило проекта, и в вебе оно ломается легче всего: достаточно
 * показать ноль дефектов там, где никто ничего не мерил. Поэтому здесь ни один
 * путь не возвращает пустоту — каждый говорит словами, что именно произошло.
 */
function notChecked() {
  return '<div class="verdict skipped"><h3>Вёрстку не проверяли</h3>'
    + '<p>Галочка была снята. Это <strong>не</strong> значит, что дефектов нет: '
    + 'их никто не искал.</p></div>';
}

/* --- главное число (PLAN-8.3, часть C) ---------------------------------- */

/* Причина остановки словами. У «fits» было «всё поместилось», и это неправда:
   ремонт берёт только высоту, и петля встаёт с fits при тексте шире места.
   На корпусе так на семи шаблонах из одиннадцати (Z-52). */
const STOPPED = {
  fits: 'ремонту больше нечего чинить',
  floor: 'упёрлись в предел читаемости',
  rounds: 'кончились раунды ремонта'
};

/* Сколько в списке отчёта. Веб здесь ничего не считает — длина списка, и всё:
   ни вычитания по раундам, ни максимума. Списка нет — «?», а не ноль: ноль был
   бы утверждением, которого движок не делал. */
function count(list) {
  return Array.isArray(list) ? String(list.length) : '?';
}

/* Что ремонт не берёт: наша ширина, заслонение, фигуры донора. */
function unrepairable(report) {
  return [report.overflow_width, report.occluded, report.donor_overflow];
}

function verifyBlock(data, decks) {
  const reports = decks.map((deck, n) => (data.verify || [])[n] || null);
  const measured = reports.filter((r) => r && r.status === 'ok');
  const title = measured.length === decks.length
    ? 'Вёрстка проверена настоящим PowerPoint'
    : 'Проверка вёрстки настоящим PowerPoint';

  /* Объяснения — ОДИН раз на сборку и только те, что к делу. Три одинаковых
     абзаца подряд — стена, и её перестают читать: так было до части C, по
     три повтора на три варианта (PLAN-8.3, замер 8). */
  const notes = [];
  if (measured.some((r) => r.stopped === 'floor')) {
    notes.push('«Упёрлись в предел читаемости» — ужимать кегль дальше значит '
      + 'сделать текст нечитаемым. Оставшееся не спрятано, а названо.');
  }
  if (measured.some((r) => unrepairable(r).some((list) => !Array.isArray(list) || list.length))) {
    notes.push('Ремонт ужимает кегль по высоте, и три вида он не берёт: надпись '
      + 'шире своего места; надпись под чужой фигурой — меньший кегль укорачивает '
      + 'текст, но не сужает строку; переполненные фигуры самого шаблона, куда мы '
      + 'ничего не подставляли.');
  }
  if (measured.length) {
    /* Слепое пятно проверки, Z-53. Снять эту строку ТЕМ ЖЕ коммитом, что
       научит детектор видеть наложение, — иначе страница начнёт преуменьшать
       проверку. Найдено растром сдаточной колоды: WorkSpace, вариант 3. */
    notes.push('Чего проверка пока не видит: текст, налезающий на соседний '
      + 'текст, когда их места в шаблоне перекрываются. Поэтому ноль здесь — '
      + 'итог замера, а не гарантия.');
  }

  return '<div class="verify"><h3>' + escape(title) + '</h3>'
    + '<div class="verdicts">'
    + decks.map((deck, n) => verdict(deck, reports[n], decks.length)).join('')
    + '</div>'
    + (notes.length
        ? '<div class="notes">' + notes.map((t) => '<p>' + escape(t) + '</p>').join('') + '</div>'
        : '')
    + '</div>';
}

function verdict(deck, report, total) {
  const who = total > 1
    ? '<div class="who">Вариант ' + escape(String(deck.variant)) + '</div>'
    : '';

  if (!report) {
    return '<div class="verdict unknown">' + who + '<h4>Проверить не смогли</h4>'
      + '<p>Проверку просили, но отчёта движок не отдал. Чаще всего это значит, '
      + 'что PowerPoint недоступен: стадия требует Windows с установленным Office.</p></div>';
  }
  if (report.status !== 'ok') {
    /* «PowerPoint открыт» и «PowerPoint не установлен» — разные беды, и первая
       у этого пользователя куда вероятнее: он прямо сейчас правит в нём
       сдаточную презентацию. Прежняя формулировка утверждала, что Office не
       установлен, там где он установлен и работает. Замер 22 сентября:
       при занятом приложении приходит status=not_measured, note='not_measured: BUSY'. */
    const busy = (report.note || '').indexOf('BUSY') !== -1;
    const why = busy
      ? 'PowerPoint уже открыт — закройте его и повторите сборку. Приложение '
        + 'одноэкземплярное, и мы не вправе закрывать ваши документы.'
      : (report.status === 'unavailable'
          ? 'PowerPoint недоступен: стадия требует Windows с установленным Office.'
          : 'Движок не смог снять габариты надписей.');
    return '<div class="verdict unknown">' + who + '<h4>Проверить не смогли</h4>'
      + '<p>' + escape(why) + '</p>'
      + (report.note ? '<p class="hint">' + escape(report.note) + '</p>' : '')
      + '<p>Сколько здесь дефектов — <strong>неизвестно</strong>.</p></div>';
  }

  /* Главное число — крупной парой, а не словом в абзаце: это кадр, ради
     которого снимается видео (PLAN-8.1, часть C). Но одно оно — неполная
     правда: «после» считает только высоту, ту, что чинит ремонт. Поэтому в той
     же карточке — три числа того, что ремонт не берёт, и все из отчёта. */
  const shown = (v) => (v === null || v === undefined ? '?' : String(v));
  const before = report.defects ? report.defects.before : null;
  const after = report.defects ? report.defects.after : null;
  /* Ноль НЕ зелёный. У проверки есть слепое пятно (Z-53), и зелёный ноль
     утверждал бы больше, чем она видит. Не ноль — янтарный; красный остаётся
     за потерей содержания. */
  const tone = after ? ' warn' : '';
  const rest = unrepairable(report).map(count);
  /* То же правило для всего, что осталось: не ноль — янтарный. Иначе «1 → 0»
     крупно перевешивает «13» мелко рядом — так и вышло на prostoj-shablon при
     проверке глазами. Неизвестное («?») не окрашиваем: это не «осталось». */
  const row = (label, n) => '<span>' + label + '</span><b'
    + (n !== '0' && n !== '?' ? ' class="warn"' : '') + '>' + n + '</b>';

  return '<div class="verdict ok">' + who
    + '<div class="pair"><span>' + shown(before) + '</span>'
    + '<span class="arrow">→</span>'
    + '<span class="after' + tone + '">' + shown(after) + '</span></div>'
    + '<div class="cap">переполнений по высоте<br>было → после ремонта</div>'
    + '<p class="why">' + escape(STOPPED[report.stopped] || report.stopped) + '</p>'
    + '<div class="rest"><span class="head">Ремонтом не чинится</span>'
    + row('шире своего места', rest[0])
    + row('закрыто фигурой', rest[1])
    + row('в фигурах шаблона', rest[2])
    + '</div></div>';
}
