// blog.flovatar.com: lite YouTube embeds (the player loads only after a click; no dependencies)
document.addEventListener('click', (e) => {
  const a = e.target.closest('a.yt[data-yt]');
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault();
  const f = document.createElement('iframe');
  f.src = 'https://www.youtube-nocookie.com/embed/' + encodeURIComponent(a.dataset.yt) + '?autoplay=1&rel=0';
  f.title = a.querySelector('.sr') ? a.querySelector('.sr').textContent : 'YouTube video';
  f.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
  f.allowFullscreen = true;
  f.referrerPolicy = 'strict-origin-when-cross-origin';
  a.replaceChildren(f);
  a.removeAttribute('href');
});
