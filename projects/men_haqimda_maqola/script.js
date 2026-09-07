const themeToggle = document.getElementById('themeToggle');
const printButton = document.getElementById('printButton');
const year = document.getElementById('year');

const savedTheme = localStorage.getItem('article-theme');

if (savedTheme === 'dark') {
  document.body.classList.add('dark');
  themeToggle.textContent = '☀';
  themeToggle.setAttribute('aria-label', 'Yorug‘ rejimni yoqish');
}

themeToggle.addEventListener('click', () => {
  const isDark = document.body.classList.toggle('dark');
  themeToggle.textContent = isDark ? '☀' : '☾';
  themeToggle.setAttribute('aria-label', isDark ? 'Yorug‘ rejimni yoqish' : 'Tungi rejimni yoqish');
  localStorage.setItem('article-theme', isDark ? 'dark' : 'light');
});

printButton.addEventListener('click', () => {
  window.print();
});

year.textContent = new Date().getFullYear();

const revealElements = document.querySelectorAll('.article-card, .trait-card, .section-title');

if ('IntersectionObserver' in window) {
  revealElements.forEach((element) => {
    element.style.opacity = '0';
    element.style.transform = 'translateY(22px)';
    element.style.transition = 'opacity 0.65s ease, transform 0.65s ease';
  });

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });

  revealElements.forEach((element) => observer.observe(element));
}