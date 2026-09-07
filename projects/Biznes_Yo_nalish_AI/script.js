const form = document.getElementById('businessForm');
const results = document.getElementById('results');
const progressBar = document.getElementById('progressBar');
const themeToggle = document.getElementById('themeToggle');

const businessData = {
  tech: [
    { name: 'Raqamli xizmatlar agentligi', icon: '💻', desc: 'Sayt yaratish, dizayn, reklama yoki avtomatlashtirish xizmatlarini kichik bizneslarga taklif qiling.' },
    { name: 'Onlayn IT ta’lim', icon: '🎓', desc: 'Amaliy bilimingizni kurs, mentorlik yoki individual dars ko‘rinishida soting.' },
    { name: 'Mikro SaaS mahsulot', icon: '⚡', desc: 'Bitta aniq muammoni hal qiladigan kichik obunali raqamli mahsulot yarating.' }
  ],
  sales: [
    { name: 'Nishali onlayn savdo', icon: '🛒', desc: 'Bitta mijoz guruhiga mo‘ljallangan talabgir mahsulotlarni ijtimoiy tarmoqlar orqali soting.' },
    { name: 'Savdo autsorsing xizmati', icon: '📞', desc: 'Kichik kompaniyalarga mijoz topish va sotuv jarayonini yo‘lga qo‘yishda yordam bering.' },
    { name: 'Distribyutorlik', icon: '🚚', desc: 'Mahalliy bozorda talab bor, ammo yetkazib berilishi sust mahsulotlarni tarqating.' }
  ],
  craft: [
    { name: 'Buyurtma asosida ishlab chiqarish', icon: '🧰', desc: 'Omborga ko‘p mahsulot yig‘masdan, mijoz buyurtmasiga mos mahsulot tayyorlang.' },
    { name: 'Milliy mahsulot brendi', icon: '✨', desc: 'Hunarmandchilik mahsulotingizni zamonaviy qadoq va kuchli hikoya bilan brendga aylantiring.' },
    { name: 'B2B ta’minot xizmati', icon: '🏭', desc: 'Korxona va do‘konlarga muntazam kerak bo‘ladigan mahsulotlarni yetkazib bering.' }
  ],
  education: [
    { name: 'Ekspertlik va konsultatsiya', icon: '🧠', desc: 'Tajribangiz asosida odamlar yoki bizneslarga natijaga yo‘naltirilgan maslahat bering.' },
    { name: 'Onlayn o‘quv markazi', icon: '🎓', desc: 'Muayyan ko‘nikmani o‘rgatuvchi mini-kurs va guruh darslarini yo‘lga qo‘ying.' },
    { name: 'Ta’lim materiallari savdosi', icon: '📚', desc: 'Qo‘llanma, shablon, test va metodikalarni raqamli mahsulot sifatida soting.' }
  ],
  food: [
    { name: 'Yetkazib beriladigan maxsus taomlar', icon: '🥗', desc: 'Ofis xodimlari, sportchilar yoki oilalar kabi aniq auditoriya uchun menyu yarating.' },
    { name: 'Yarim tayyor mahsulot brendi', icon: '🍱', desc: 'Mijozning vaqtini tejaydigan sifatli, qulay va yaxshi qadoqlangan mahsulot taklif qiling.' },
    { name: 'Kichik tadbirlar keteringi', icon: '🎉', desc: 'Avval kichik buyurtmalardan boshlanadigan foydali xizmat modelini yarating.' }
  ],
  beginner: [
    { name: 'Xizmatlar vositachiligi', icon: '🤝', desc: 'Mijoz va malakali ijrochini bog‘lab, buyurtmalarni boshqarish orqali komissiya oling.' },
    { name: 'Ijtimoiy tarmoq savdosi', icon: '📱', desc: 'Kam miqdordagi mahsulot bilan talabni sinang va faqat sotilgan mahsulot hajmini oshiring.' },
    { name: 'Mahalliy xizmat biznesi', icon: '📍', desc: 'Hududingizdagi kundalik muammoni tez, ishonchli va qulay xizmat bilan hal qiling.' }
  ]
};

const fields = [...form.querySelectorAll('input:not([type=radio]), select, textarea')];
fields.forEach(field => {
  field.addEventListener('input', () => {
    const completed = fields.filter(item => item.value.trim() !== '').length;
    progressBar.style.width = Math.max(10, Math.round(completed / fields.length * 100)) + '%';
  });
});

themeToggle.addEventListener('click', () => {
  document.body.classList.toggle('dark');
  themeToggle.textContent = document.body.classList.contains('dark') ? '☀' : '☾';
  localStorage.setItem('biznes-theme', document.body.classList.contains('dark') ? 'dark' : 'light');
});

if (localStorage.getItem('biznes-theme') === 'dark') {
  document.body.classList.add('dark');
  themeToggle.textContent = '☀';
}

form.addEventListener('submit', event => {
  event.preventDefault();

  const name = document.getElementById('name').value.trim();
  const experience = document.getElementById('experience').value;
  const budget = document.getElementById('budget').value;
  const time = document.getElementById('time').value;
  const goal = document.getElementById('goal').value;
  const format = document.querySelector('input[name=format]:checked').value;
  const recommendations = businessData[experience];

  let score = 76;
  if (budget === 'medium') score += 5;
  if (budget === 'high') score += 8;
  if (time === 'medium') score += 4;
  if (time === 'full') score += 7;
  if (goal === 'stable') score += 3;
  if (experience !== 'beginner') score += 3;
  score = Math.min(score, 96);

  document.getElementById('resultTitle').textContent = name + ', siz uchun biznes yo‘nalishlari';
  setRecommendation('main', recommendations[0]);
  setRecommendation('second', recommendations[1]);
  setRecommendation('third', recommendations[2]);
  document.getElementById('matchScore').textContent = score + '%';
  document.getElementById('matchLine').style.width = '0';

  const formatText = format === 'online' ? 'onlayn kanallarda' : format === 'offline' ? 'mahalliy bozorda' : 'onlayn va oflayn kanallarni birlashtirib';
  const budgetText = budget === 'low' ? 'Sarmoyangiz cheklanganligi sababli avval xizmat yoki oldindan buyurtma modelidan foydalaning.' : budget === 'medium' ? 'Sarmoyaning 60 foizidan ko‘pini birinchi bosqichda ishlatmang.' : 'Katta sarmoya bo‘lsa ham, talabni kichik sinov orqali tekshirmasdan katta xarajat qilmang.';
  document.getElementById('riskText').textContent = budgetText + ' Biznesni ' + formatText + ' 5–10 ta haqiqiy mijozda sinab, raqamlar ijobiy bo‘lsagina kengaytiring.';

  const plans = getPlan(recommendations[0].name, time);
  document.getElementById('timeline').innerHTML = plans.map((plan, index) => `<div class="timeline-item"><b>${index + 1}-HAFTA</b><h4>${plan.title}</h4><p>${plan.text}</p></div>`).join('');

  results.classList.remove('hidden');
  results.classList.add('show');
  setTimeout(() => document.getElementById('matchLine').style.width = score + '%', 100);
  results.scrollIntoView({ behavior: 'smooth', block: 'start' });
});

function setRecommendation(prefix, data) {
  document.getElementById(prefix + 'Icon').textContent = data.icon;
  document.getElementById(prefix + 'Business').textContent = data.name;
  document.getElementById(prefix + 'Description').textContent = data.desc;
}

function getPlan(businessName, time) {
  const pace = time === 'part' ? 'Kuniga 1–2 soat ajratib' : time === 'medium' ? 'Har kuni 3–4 soat ajratib' : 'To‘liq ish rejimida';
  return [
    { title: 'Muammo va mijoz', text: pace + ', 10 ta potensial mijoz bilan suhbat qiling va ularning eng qimmat muammosini yozib oling.' },
    { title: 'Minimal taklif', text: businessName + ' uchun bitta sodda xizmat yoki mahsulot paketi, narx va aniq natijani belgilang.' },
    { title: 'Birinchi savdo', text: 'Taklifni kamida 30 kishiga ko‘rsating. Maqsad — mukammallik emas, dastlabki 3 ta haqiqiy savdo.' },
    { title: 'Tahlil va o‘sish', text: 'Xarajat, daromad va mijoz fikrlarini tahlil qiling. Ishlayotgan kanalni tanlab, keyingi 90 kunlik reja tuzing.' }
  ];
}

document.getElementById('restartButton').addEventListener('click', () => {
  results.classList.add('hidden');
  results.classList.remove('show');
  document.getElementById('assessment').scrollIntoView({ behavior: 'smooth' });
});

document.getElementById('printButton').addEventListener('click', () => window.print());
