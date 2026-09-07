const form = document.getElementById('briefForm');
const steps = [...document.querySelectorAll('.form-step')];
const nextBtn = document.getElementById('nextBtn');
const prevBtn = document.getElementById('prevBtn');
const submitBtn = document.getElementById('submitBtn');
const progressBar = document.getElementById('progressBar');
const stepText = document.getElementById('stepText');
const progressPercent = document.getElementById('progressPercent');
const result = document.getElementById('result');
const summary = document.getElementById('summary');
const copyBtn = document.getElementById('copyBtn');
const restartBtn = document.getElementById('restartBtn');
let currentStep = 0;

function updateStep() {
  steps.forEach((step, index) => step.classList.toggle('active', index === currentStep));
  const percent = Math.round(((currentStep + 1) / steps.length) * 100);
  progressBar.style.width = percent + '%';
  stepText.textContent = `${currentStep + 1}-qadam / ${steps.length}`;
  progressPercent.textContent = percent + '%';
  prevBtn.disabled = currentStep === 0;
  nextBtn.classList.toggle('hidden', currentStep === steps.length - 1);
  submitBtn.classList.toggle('hidden', currentStep !== steps.length - 1);
}

function validateCurrentStep() {
  const requiredFields = [...steps[currentStep].querySelectorAll('[required]')];
  let valid = true;

  requiredFields.forEach(field => {
    field.classList.remove('invalid');
    if (!field.value.trim()) {
      field.classList.add('invalid');
      valid = false;
    }
  });

  if (!valid) {
    const firstInvalid = steps[currentStep].querySelector('.invalid');
    firstInvalid?.focus();
  }
  return valid;
}

nextBtn.addEventListener('click', () => {
  if (!validateCurrentStep()) return;
  if (currentStep < steps.length - 1) {
    currentStep++;
    updateStep();
    document.querySelector('.form-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
});

prevBtn.addEventListener('click', () => {
  if (currentStep > 0) {
    currentStep--;
    updateStep();
  }
});

form.addEventListener('input', event => event.target.classList.remove('invalid'));

function checkedValues(name) {
  const values = [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(input => input.value);
  return values.length ? values.join(', ') : 'Tanlanmagan';
}

function valueOf(data, name, fallback = 'Ko‘rsatilmagan') {
  const value = data.get(name);
  return value && value.trim() ? value.trim() : fallback;
}

form.addEventListener('submit', event => {
  event.preventDefault();
  if (!validateCurrentStep()) return;

  const data = new FormData(form);
  const brief = `SAYT YARATISH UCHUN BRIEF

1. MIJOZ / BREND
${valueOf(data, 'name')}

2. SAYT TURI
${valueOf(data, 'siteType')}

3. ASOSIY MAQSAD
${valueOf(data, 'goal')}

4. MAQSADLI AUDITORIYA
${valueOf(data, 'audience')}

5. KERAKLI SAHIFALAR
${checkedValues('pages')}

6. QO‘SHIMCHA SAHIFALAR
${valueOf(data, 'extraPages')}

7. DIZAYN USLUBI
${valueOf(data, 'style')}

8. RANGLAR
${valueOf(data, 'colors')}

9. KERAKLI FUNKSIYALAR
${checkedValues('features')}

10. YOQADIGAN SAYTLAR / NAMUNALAR
${valueOf(data, 'references')}

11. MUDDAT
${valueOf(data, 'deadline')}

12. BUDJET
${valueOf(data, 'budget')}

13. QO‘SHIMCHA IZOH
${valueOf(data, 'notes')}`;

  summary.value = brief;
  form.classList.add('hidden');
  document.querySelector('.progress-wrapper').classList.add('hidden');
  result.classList.remove('hidden');
  result.scrollIntoView({ behavior: 'smooth', block: 'center' });
});

copyBtn.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(summary.value);
  } catch {
    summary.select();
    document.execCommand('copy');
  }
  const oldText = copyBtn.textContent;
  copyBtn.textContent = 'Nusxalandi ✓';
  setTimeout(() => copyBtn.textContent = oldText, 1800);
});

restartBtn.addEventListener('click', () => {
  form.reset();
  currentStep = 0;
  result.classList.add('hidden');
  form.classList.remove('hidden');
  document.querySelector('.progress-wrapper').classList.remove('hidden');
  updateStep();
});

updateStep();