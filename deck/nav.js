/* deck 翻页：竖滑为主（手机就是上滑），键盘左右/空格/上下跳页，进度条与页码跟随 */

const slides = [...document.querySelectorAll('.slide')];
const bar = document.querySelector('.progress__bar');
const current = document.querySelector('.pager__current');
const total = document.querySelector('.pager__total');

if (total) total.textContent = String(slides.length);

let index = 0;

function go(next) {
  index = Math.max(0, Math.min(slides.length - 1, next));
  slides[index].scrollIntoView({ block: 'start' });
}

function sync() {
  // 当前屏 = 视口中线落在哪一页上
  const middle = window.scrollY + window.innerHeight / 2;
  index = slides.findIndex((slide) => {
    const top = slide.offsetTop;
    return middle >= top && middle < top + slide.offsetHeight;
  });
  if (index < 0) index = 0;
  if (current) current.textContent = String(index + 1);
  if (bar) bar.style.width = `${((index + 1) / slides.length) * 100}%`;
}

window.addEventListener('scroll', sync, { passive: true });
window.addEventListener('resize', sync);
sync();

document.addEventListener('keydown', (event) => {
  const next = {
    ArrowRight: index + 1,
    ArrowDown: index + 1,
    PageDown: index + 1,
    ' ': index + 1,
    ArrowLeft: index - 1,
    ArrowUp: index - 1,
    PageUp: index - 1,
    Home: 0,
    End: slides.length - 1,
  }[event.key];

  if (next === undefined) return;
  event.preventDefault();
  go(next);
});

document.querySelector('.pager__prev')?.addEventListener('click', () => go(index - 1));
document.querySelector('.pager__next')?.addEventListener('click', () => go(index + 1));
