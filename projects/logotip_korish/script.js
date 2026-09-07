const input = document.getElementById('logoInput');
const preview = document.getElementById('preview');
const removeButton = document.getElementById('removeButton');

const placeholder = `
  <div class="placeholder">
    <span>J</span>
    <p>Logotipingizni yuklang</p>
  </div>
`;

input.addEventListener('change', () => {
  const file = input.files[0];
  if (!file) return;

  if (!file.type.startsWith('image/')) {
    alert('Iltimos, rasm faylini tanlang.');
    input.value = '';
    return;
  }

  const reader = new FileReader();
  reader.onload = event => {
    preview.innerHTML = '';
    const image = document.createElement('img');
    image.src = event.target.result;
    image.alt = 'Yuklangan logotip';
    preview.appendChild(image);
    removeButton.hidden = false;
  };
  reader.readAsDataURL(file);
});

removeButton.addEventListener('click', () => {
  input.value = '';
  preview.innerHTML = placeholder;
  removeButton.hidden = true;
});