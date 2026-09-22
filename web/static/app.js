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
  } catch (error) {
    $('template-name').textContent = 'Не загрузился: ' + error.message;
  }
});

$('fill-example').addEventListener('click', () => { $('text').value = EXAMPLE; });

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
        variants: parseInt($('variants').value, 10),
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
      + '<a href="/api/deck?token=' + encodeURIComponent(token) + '&n=' + n + '">Скачать .pptx</a>'
      + '</div>';
  }).join('');

  $('verify-block').innerHTML = decks
    .map((deck, n) => verdict(data, deck, n, decks.length))
    .join('');

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

/* Три исхода проверки вёрстки, и они РАЗНЫЕ.
 *
 * Это главное правило проекта, и в вебе оно ломается легче всего: достаточно
 * показать ноль дефектов там, где никто ничего не мерил. Поэтому здесь ни один
 * путь не возвращает пустоту — каждый говорит словами, что именно произошло.
 */
function verdict(data, deck, n, total) {
  const head = total > 1 ? 'Вариант ' + deck.variant + ': ' : '';
  const report = (data.verify || [])[n];

  if (!data.verify_requested) {
    return '<div class="verdict skipped"><h3>' + head + 'Вёрстку не проверяли</h3>'
      + '<p>Галочка была снята. Это <strong>не</strong> значит, что дефектов нет: '
      + 'их никто не искал.</p></div>';
  }
  if (!report) {
    return '<div class="verdict unknown"><h3>' + head + 'Проверить не смогли</h3>'
      + '<p>Проверку просили, но отчёта движок не отдал. Чаще всего это значит, '
      + 'что PowerPoint недоступен: стадия требует Windows с установленным Office.</p></div>';
  }
  if (report.status !== 'ok') {
    const why = report.status === 'unavailable'
      ? 'PowerPoint недоступен: стадия требует Windows с установленным Office.'
      : 'Движок не смог снять габариты надписей.';
    return '<div class="verdict unknown"><h3>' + head + 'Проверить не смогли</h3>'
      + '<p>' + escape(why) + '</p>'
      + (report.note ? '<p class="hint">' + escape(report.note) + '</p>' : '')
      + '<p>Сколько здесь дефектов — <strong>неизвестно</strong>.</p></div>';
  }

  const before = report.defects ? report.defects.before : null;
  const after = report.defects ? report.defects.after : null;
  const stopped = {
    fits: 'всё поместилось',
    floor: 'дальше ужимать нельзя — упёрлись в предел читаемости',
    rounds: 'кончились раунды ремонта'
  }[report.stopped] || report.stopped;
  const occluded = (report.occluded || []).length;

  return '<div class="verdict ok"><h3>' + head + 'Вёрстка проверена настоящим PowerPoint</h3>'
    + '<p class="numbers">Переполнений было <strong>' + before + '</strong>, '
    + 'после ремонта <strong>' + after + '</strong>. Остановились: ' + escape(stopped) + '.</p>'
    + (occluded
        ? '<p>Надписей, закрытых чужой фигурой: <strong>' + occluded + '</strong>. '
          + 'Ремонтом это не чинится — меньший кегль укорачивает текст, но не сужает строку.</p>'
        : '<p>Закрытых чужой фигурой надписей нет.</p>')
    + '</div>';
}
