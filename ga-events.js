// MBAL — native <video> plays -> GA4 events (video_start / _progress / _complete). Tiny; no deps.
// Honors the owner opt-out automatically: gtag() is a no-op when ga-disable-<id> is set.
addEventListener('DOMContentLoaded', function () {
  if (typeof gtag !== 'function') return;
  document.querySelectorAll('video').forEach(function (v) {
    var t = (v.getAttribute('aria-label') || (v.currentSrc || 'video').split('/').pop()).slice(0, 90), s = {};
    v.addEventListener('play', function () { if (!s.x) { s.x = 1; gtag('event', 'video_start', { video_title: t }); } });
    v.addEventListener('ended', function () { gtag('event', 'video_complete', { video_title: t }); });
    v.addEventListener('timeupdate', function () {
      var d = v.duration; if (!d) return; var p = v.currentTime / d;
      [25, 50, 75].forEach(function (m) { if (p >= m / 100 && !s[m]) { s[m] = 1; gtag('event', 'video_progress', { video_title: t, video_percent: m }); } });
    });
  });
});
