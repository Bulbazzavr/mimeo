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

/* --- цвета шаблона (решение пользователя 28 сентября) -------------------- */

/* Базовый вид — по умолчанию. Переключатель «В цветах шаблона» берёт акцент
   страницы из палитры загруженного шаблона: роль темы accent1…accent6, иначе
   ядро палитры, — первый цвет, который не серый и не почти белый или чёрный.
   Слишком светлый затемняется, пока белый текст на нём не станет читаем
   (контраст 4.5:1 — тот же порог, что в Приложении 1 ТЗ). Выбор запоминается в
   браузере; хранилища может не быть — тогда просто не запомнится. */
const THEME_KEY = 'dp-theme';
let templateAccent = null;

function rgbOf(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function colourful(rgb) {
  const max = Math.max(...rgb) / 255;
  const min = Math.min(...rgb) / 255;
  const light = (max + min) / 2;
  const sat = max === min ? 0 : (max - min) / (1 - Math.abs(2 * light - 1));
  return sat >= 0.25 && light >= 0.12 && light <= 0.88;
}

function readable(rgb) {
  let c = rgb.slice();
  for (let i = 0; i < 20 && 1.05 / (luminance(c) + 0.05) < 4.5; i += 1) {
    c = c.map((v) => Math.round(v * 0.92));
  }
  return c;
}

function pickAccent(palette) {
  const theme = (palette && palette.theme) || [];
  const roles = ['accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6'];
  const ordered = roles.map((r) => (theme.find((t) => (t.role || '').toLowerCase() === r) || {}).hex)
    .concat((palette && palette.core) || []);
  for (const hex of ordered) {
    const rgb = rgbOf(hex);
    if (rgb && colourful(rgb)) return readable(rgb);
  }
  return null;
}

function applyTheme() {
  const style = document.documentElement.style;
  const rgb = $('theme-toggle').checked ? templateAccent : null;
  if (rgb) {
    const [r, g, b] = rgb;
    style.setProperty('--accent', 'rgb(' + r + ', ' + g + ', ' + b + ')');
    style.setProperty('--accent-soft', 'rgba(' + r + ', ' + g + ', ' + b + ', 0.09)');
    style.setProperty('--accent-glow', 'rgba(' + r + ', ' + g + ', ' + b + ', 0.26)');
  } else {
    ['--accent', '--accent-soft', '--accent-glow'].forEach((v) => style.removeProperty(v));
  }
  const sw = $('theme-swatch');
  sw.hidden = !templateAccent;
  if (templateAccent) sw.style.background = 'rgb(' + templateAccent.join(', ') + ')';
}

try { $('theme-toggle').checked = localStorage.getItem(THEME_KEY) === 'template'; } catch (e) { /* нет хранилища */ }
$('theme-toggle').addEventListener('change', () => {
  try { localStorage.setItem(THEME_KEY, $('theme-toggle').checked ? 'template' : 'base'); } catch (e) { /* нет хранилища */ }
  applyTheme();
});

/* --- загрузка шаблона -------------------------------------------------- */

/* Зона файла подсвечивается, пока над ней держат перетаскиваемый файл: в
   Firefox и Safari :hover во время перетаскивания не срабатывает. Сам файл
   принимает прозрачный <input>, растянутый по зоне. */
document.querySelectorAll('.drop').forEach((zone) => {
  const on = () => zone.classList.add('over');
  const off = () => zone.classList.remove('over');
  zone.addEventListener('dragenter', on);
  zone.addEventListener('dragover', on);
  zone.addEventListener('dragleave', off);
  zone.addEventListener('drop', off);
});

$('template').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  token = null;
  templateAccent = null;
  applyTheme();
  $('template-drop').classList.remove('has-file');
  if (!file) {
    $('template-name').textContent = '.pptx или .potx';
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
    $('template-drop').classList.add('has-file');
    setStatus('');
    loadDesign();
  } catch (error) {
    $('template-name').textContent = 'Не загрузился: ' + error.message;
  }
});

$('fill-example').addEventListener('click', () => { $('text').value = EXAMPLE; });

/* --- контент-пакет (Z-73) ---------------------------------------------- */

/* ТЗ, раздел 2, п. 1: «импорт… контент-пакетов». Текст файлом ложится в поле —
   его видно и можно поправить; картинки уходят на сервер перед сборкой и
   встают в каталог прогона рядом с текстом. Движок берёт картинку, названную
   в тексте, поэтому неназванные дописываются в конец текста строкой — модель
   разложит их по слайдам по смыслу имени. */
const IMAGE_EXT = /\.(png|jpe?g|gif|bmp|webp)$/i;
let images = [];

/* Знаки, которые обрывают путь в прозе, — то же правило, что у сервера
   (serve.py, safe_image_name): иначе движок не узнал бы имя в тексте. */
function safeName(name) {
  return name.trim().replace(/[\s,;:()«»"'[\]]+/g, '_');
}

async function readText(file) {
  const bytes = await file.arrayBuffer();
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch (error) {
    return new TextDecoder('windows-1251').decode(bytes);
  }
}

function mentionImages() {
  const text = $('text').value;
  const missing = images.map((f) => safeName(f.name)).filter((n) => !text.includes(n));
  if (missing.length) {
    $('text').value = text.replace(/\s+$/, '') + '\n\nПриложенные картинки: ' + missing.join(', ') + '.';
  }
}

function showImages() {
  const box = $('content-images');
  box.hidden = !images.length;
  box.innerHTML = images.length
    ? 'Картинки: ' + escape(images.map((f) => safeName(f.name)).join(', '))
      + ' — названные в тексте встанут на свои слайды. '
      + '<button type="button" class="link" id="drop-images">Убрать картинки</button>'
    : '';
  const drop = $('drop-images');
  if (drop) drop.addEventListener('click', () => { images = []; showImages(); });
}

$('content').addEventListener('change', async (event) => {
  const files = Array.from(event.target.files || []);
  const texts = files.filter((f) => /\.(txt|md)$/i.test(f.name));
  const pictures = files.filter((f) => IMAGE_EXT.test(f.name));
  if (texts.length) $('text').value = (await readText(texts[0])).trim();
  for (const file of pictures) {
    images = images.filter((f) => safeName(f.name) !== safeName(file.name)).concat([file]);
  }
  mentionImages();
  showImages();
  const skipped = files.length - texts.length - pictures.length;
  $('content-name').textContent = (texts.length ? 'Текст: ' + texts[0].name : 'Текст — в поле ниже')
    + (pictures.length ? '; картинок добавлено: ' + pictures.length : '')
    + (texts.length > 1 ? '; взят первый текст из ' + texts.length : '')
    + (skipped ? '; пропущено файлов другого вида: ' + skipped : '');
  $('content-drop').classList.toggle('has-file', Boolean(texts.length || images.length));
  event.target.value = '';            /* тот же файл можно выбрать ещё раз */
});

async function uploadImages() {
  for (const file of images) {
    const response = await fetch('/api/content?token=' + encodeURIComponent(token)
      + '&name=' + encodeURIComponent(safeName(file.name)), { method: 'POST', body: file });
    const data = await response.json();
    if (!data.ok) throw new Error('картинка ' + file.name + ': ' + (data.error || 'не загрузилась'));
  }
}

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
    templateAccent = pickAccent(data.palette);
    applyTheme();
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

/* --- ход сборки по стадиям ----------------------------------------------- */

/* Стадии отмечает сервер по файлам, которые движок уже записал (serve.py,
   /api/progress), — не таймер и не догадка. Движок идёт по вариантам: картинки,
   вёрстка, проверка и аудит первого, потом второго, — поэтому у каждой стадии
   свой счёт, и в работе бывают две сразу. Часы в строке статуса — чтобы видеть
   время против потолка ТЗ, пять минут на колоду. */
let polling = false;
let pollTimer = null;

/* Время — мм:сс (просьба пользователя 28 сентября): «01:56», а не «116.3 с». */
function clock(seconds) {
  const s = Math.round(seconds || 0);
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}

function renderStages(p, finished) {
  const n = p.variants || 1;
  const modelDone = Boolean(p.model) || p.decks > 0;
  const state = (done, started) => (done ? 'done' : (finished ? '' : (started ? 'active' : '')));
  const stages = [['Разбор шаблона и колода от модели',
    p.model ? (p.model.by_model ? 'построила модель' : 'собрана без модели') : '',
    state(modelDone, true)]];
  if (p.images) {
    stages.push(['Сцены и картинки генератора',
      p.pictures ? 'готово: ' + p.pictures : (finished ? 'ни одной' : ''),
      state(p.decks >= n || (finished && p.decks > 0), modelDone)]);
  }
  stages.push(['Вёрстка вариантов', p.decks + ' из ' + n, state(p.decks >= n, modelDone)]);
  if (p.verify) {
    stages.push(['Проверка вёрстки в PowerPoint', p.verified + ' из ' + n,
      state(p.verified >= n, p.decks > 0)]);
    stages.push(['Аудит слайдов', p.audited + ' из ' + n, state(p.audited >= n, p.verified > 0)]);
  }
  const list = $('stages');
  list.innerHTML = stages.map((s) => '<li class="' + s[2] + '">' + escape(s[0])
    + (s[1] ? ' <span class="count">' + escape(s[1]) + '</span>' : '') + '</li>').join('');
  list.hidden = false;
}

async function fetchProgress() {
  const response = await fetch('/api/progress?token=' + encodeURIComponent(token));
  return response.json();
}

async function pollProgress() {
  try {
    const p = await fetchProgress();
    /* Ответ, пришедший после конца сборки, и данные прошлой сборки (сервер
       ещё не отметил начало новой) не показываем. */
    if (!polling || !p.ok || !p.building) return;
    renderStages(p, false);
    setStatus('Идёт сборка — ' + clock(p.elapsed), 'working');
  } catch (error) { /* ход сборки — подсказка; сама сборка ответит своим */ }
}

function startProgress(first) {
  stopProgress();
  $('stages').classList.remove('stopped');
  if (first) renderStages(first, false);
  polling = true;
  pollTimer = setInterval(pollProgress, 1000);
}

function stopProgress() {
  polling = false;
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

async function finishProgress(ok) {
  stopProgress();
  try {
    const p = await fetchProgress();
    /* Оборвалась — стадии «в работе» остаются, но красным и без пульса. */
    if (p.ok && p.elapsed !== undefined) renderStages(p, ok);
  } catch (error) { /* показываем, что успели */ }
  if (!ok) $('stages').classList.add('stopped');
}

/* --- сборка ------------------------------------------------------------ */

$('go').addEventListener('click', async () => {
  if (!token) return setStatus('Сначала выберите шаблон.', 'error');
  const text = $('text').value.trim();
  if (!text) return setStatus('Вставьте текст — движку нечего раскладывать.', 'error');

  const verify = $('verify').checked;
  const mode = document.querySelector('input[name="text-mode"]:checked');
  $('go').disabled = true;
  /* Модель строит колоду за полминуты, при повторе — за минуту (замер Ш9:
     33–34 с на вызов); проверка вёрстки добавляет десятки секунд. */
  setStatus(verify
    ? 'Модель строит колоду, потом проверяем вёрстку в PowerPoint — это минута-две…'
    : 'Модель строит колоду — до минуты…', 'working');
  startProgress({
    ok: true, variants: parseInt($('variants').value, 10) || 1, images: $('images').checked,
    verify: verify, model: null, pictures: 0, decks: 0, verified: 0, audited: 0
  });

  try {
    await uploadImages();
    const response = await fetch('/api/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: token,
        text: text,
        slides: $('slides').value.trim(),
        variants: parseInt($('variants').value, 10) || 1,
        verify: verify,
        images: $('images').checked,
        text_mode: mode ? mode.value : 'improve',
        purpose: $('purpose').value
      })
    });
    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'сборка не удалась');
    await finishProgress(true);
    render(data);
    setStatus('Готово за ' + clock(data.seconds) + '.');
  } catch (error) {
    await finishProgress(false);
    setStatus('Не вышло: ' + error.message, 'error');
  } finally {
    $('go').disabled = false;
  }
});

/* --- показ результата --------------------------------------------------- */

/* Каким путём собран текст колоды — крупно и первым: продукт работает с
   моделью, и колода без неё — отказ, о котором человек обязан узнать, не
   раскрывая «подробности». Слова — из outline.json через сервер. */
const TEXT_MODE = { improve: 'текст переписан короче', keep: 'фразы автора оставлены' };

function modelBlock(model) {
  if (!model) {
    return '<div class="verdict unknown"><h3>Неизвестно, звали ли модель</h3>'
      + '<p>Движок не оставил записи пути модели (outline.json).</p></div>';
  }
  if (model.by_model) {
    return '<div class="verdict ok"><h3>Колоду построила модель'
      + (model.retried ? ' — со второй попытки' : '') + '</h3>'
      + '<p>' + escape(TEXT_MODE[model.text_mode] || model.text_mode || '')
      + '; числа и факты проверены по вашему тексту.</p></div>';
  }
  const why = {
    no_answer: 'Модель не ответила. Запущен ли сервер модели?',
    rejected: 'Модель ответила, но ответ не прошёл проверку'
      + (model.retried ? ' и со второй попытки' : '') + '.',
    unparsed: 'Ответ модели не разобрался.',
    too_long: 'Текст длиннее, чем модель принимает за раз.',
    off: 'Модель выключена.',
    markup: 'Текст размечен заголовками — структуру задал автор, модель не нужна.'
  }[model.status] || 'Модель колоду не построила.';
  const lost = (model.failed || []).length
    ? '<p>Что не прошло проверку:</p><ul>' + model.failed.map((f) =>
        '<li>' + escape(f) + '</li>').join('') + '</ul>'
    : '';
  const tone = model.status === 'markup' ? 'skipped' : 'unknown';
  return '<div class="verdict ' + tone + '"><h3>Колода собрана без модели</h3>'
    + '<p>' + escape(why) + '</p>' + lost
    + '<p class="hint">' + escape(model.line) + '</p></div>';
}

/* Что просили у генератора для каждой картинки колоды — рядом с колодой, а не
   на слайде: на слайде промпт был бы служебным текстом. */
function promptsBlock(pictures) {
  if (!pictures || !pictures.length) return '';
  return '<details class="prompts"><summary>Картинки генератора: '
    + pictures.length + ' — с чем их рисовали</summary><ul>'
    + pictures.map((p) => '<li><b>Слайд ' + p.slide + '.</b> ' + escape(p.prompt)
      + (p.ru ? '<br><span class="ru">' + escape(p.ru) + '</span>' : '') + '</li>').join('')
    + '</ul></details>';
}

function render(data) {
  const decks = data.report.decks || [];
  $('model-block').innerHTML = modelBlock(data.model);
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
      + '<a href="/api/export?token=' + encodeURIComponent(token) + '&n=' + n + '&format=html">.html</a>'
      + '<a href="/api/export?token=' + encodeURIComponent(token) + '&n=' + n + '&format=pdf">.pdf</a>'
      + '</div>'
      + promptsBlock(deck.pictures)
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
  $('audit-block').innerHTML = auditBlock(data, decks);
  bindFix();

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

/* --- аудит слайдов (Z-34, PLAN-11.0) ------------------------------------ */

/* ТЗ, п. 6: система показывает найденное, пользователь выбирает, что
   исправить. У каждой находки видно, чья проверка — кода (детерминированная)
   или модели по картинке слайда (контекстуальная) — и чем её исправят.
   Галочки пусты: выбор за человеком. */
const AUDIT_KIND = { deterministic: 'код', contextual: 'модель по картинке' };
const AUDIT_FIX = { model: 'модель перепишет слайд', layout: 'кегль слайда мельче' };

function auditBlock(data, decks) {
  const audits = data.audit || [];
  if (!audits.some((a) => a)) return '';
  let fixable = 0;
  let html = '<div class="audit"><h3>Аудит слайдов — отметьте, что исправить</h3>'
    + '<p class="hint">Код считает по плану колоды и замеру PowerPoint: пункты, слова, '
    + 'наезд надписей. Модель смотрит на картинку каждого слайда и отвечает на вопросы '
    + 'Приложения 1 ТЗ: вывод ли заголовок, нет ли служебного текста, один ли язык.</p>';
  audits.forEach((a, n) => {
    const deck = decks[n] || {};
    const name = deck.variant === null || deck.variant === undefined
      ? 'Колода' : 'Вариант ' + deck.variant;
    if (!a) {
      html += '<h4>' + escape(name) + '</h4><p class="hint">Аудит не запускался.</p>';
      return;
    }
    /* «Не смог» — не «чисто»: без картинок или модели ноль находок не значит
       ничего, и это сказано прямо. */
    const partial = a.status === 'partial'
      ? '<p class="hint"><strong>Проверено не всё:</strong> ' + escape(a.note) + '</p>' : '';
    const fixed = a.verify && a.verify.before !== null && a.verify.before !== undefined
      ? '<p class="hint">Проверка вёрстки уже ужала сама: переполнений '
        + a.verify.before + ' → ' + a.verify.after + '.</p>' : '';
    const found = a.findings || [];
    html += '<h4>' + escape(name) + ' — находок ' + found.length + '</h4>' + partial + fixed;
    if (!found.length) return;
    html += '<table class="scale audit-table"><tr><th></th><th>Слайд</th><th>Кто нашёл</th>'
      + '<th>Что не так</th><th>Исправление</th></tr>'
      + found.map((f) => {
        if (f.fix) fixable += 1;
        return '<tr><td>' + (f.fix
            ? '<input type="checkbox" class="pick" value="' + escape(f.id) + '">' : '')
          + '</td><td class="num">' + f.slide + '</td>'
          + '<td>' + escape(AUDIT_KIND[f.kind] || f.kind) + '</td>'
          + '<td>' + escape(f.detail) + '</td>'
          + '<td>' + escape(AUDIT_FIX[f.fix] || 'не чинится') + '</td></tr>';
      }).join('') + '</table>';
  });
  if (fixable) {
    html += '<div class="actions"><button type="button" class="primary" id="fix-go">'
      + 'Исправить отмеченное</button>'
      + '<button type="button" class="link" id="fix-all">отметить все</button></div>';
  }
  return html + '</div>';
}

function bindFix() {
  const all = $('fix-all');
  if (all) {
    all.addEventListener('click', () => {
      document.querySelectorAll('#audit-block .pick').forEach((c) => { c.checked = true; });
    });
  }
  const go = $('fix-go');
  if (!go) return;
  go.addEventListener('click', async () => {
    const ids = Array.from(document.querySelectorAll('#audit-block .pick:checked'))
      .map((c) => c.value);
    if (!ids.length) return setStatus('Отметьте хотя бы одну находку.', 'error');
    go.disabled = true;
    setStatus('Исправляем отмеченное и проверяем заново — это минута-две…', 'working');
    startProgress(null);
    try {
      const response = await fetch('/api/fix', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token, ids: ids })
      });
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || 'исправление не удалось');
      await finishProgress(true);
      render(data);
      setStatus('Исправлено и проверено заново за ' + clock(data.seconds) + '.');
    } catch (error) {
      await finishProgress(false);
      setStatus('Не вышло: ' + error.message, 'error');
      go.disabled = false;
    }
  });
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
    notes.push('Ремонт ужимает кегль — против переполнения по высоте и слова, '
      + 'разорванного посередине строки, — а три вида он не берёт: надпись '
      + 'шире своего места; надпись под чужой фигурой — меньший кегль укорачивает '
      + 'текст, но не сужает строку; переполненные фигуры самого шаблона, куда мы '
      + 'ничего не подставляли.');
  }
  if (measured.length) {
    /* Слепые пятна проверки. До 23 сентября здесь стояло одно — текст,
       налезающий на соседний, когда их места в шаблоне перекрываются (Z-53);
       его закрыл тот же коммит, что научил мерку места видеть чужой текст в
       нашем боксе. Замер к нему нашёл ещё два: строки одной надписи друг на
       друге (Z-55) и слово, разорванное посередине (Z-56). Второе с
       26 сентября проверка видит и чинит кеглем; первое — нет: Z-55 закрыта
       тем, что текст не идёт в мелкое место длиннее себя, а не меркой. Снимать
       строку — тем же коммитом, что научит проверку и этому. */
    notes.push('Чего проверка пока не видит: строки одной надписи, налезающие '
      + 'друг на друга. Поэтому ноль здесь — итог замера, а не гарантия.');
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
     правда: «после» считает только то, что чинит ремонт, — высоту и, с
     26 сентября, разорванное слово (Z-56). Поэтому в той
     же карточке — три числа того, что ремонт не берёт, и все из отчёта. */
  const shown = (v) => (v === null || v === undefined ? '?' : String(v));
  const before = report.defects ? report.defects.before : null;
  const after = report.defects ? report.defects.after : null;
  /* Ноль НЕ зелёный. У проверки есть слепое пятно (Z-55; до 23 сентября и
     Z-53, до 26 сентября и Z-56), и зелёный ноль утверждал бы больше, чем она
     видит. Не ноль —
     янтарный; красный остаётся за потерей содержания. */
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
    + '<div class="cap">переполнений и разрывов слов<br>было → после ремонта</div>'
    + '<p class="why">' + escape(STOPPED[report.stopped] || report.stopped) + '</p>'
    + '<div class="rest"><span class="head">Ремонтом не чинится</span>'
    + row('шире своего места', rest[0])
    + row('закрыто фигурой', rest[1])
    + row('в фигурах шаблона', rest[2])
    + '</div></div>';
}
