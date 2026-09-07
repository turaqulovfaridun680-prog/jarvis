const header=document.getElementById('header');
const menuBtn=document.getElementById('menuBtn');
const nav=document.getElementById('nav');
const toTop=document.getElementById('toTop');
const toast=document.getElementById('toast');

menuBtn.addEventListener('click',()=>{
  nav.classList.toggle('open');
  menuBtn.classList.toggle('open');
  menuBtn.setAttribute('aria-expanded',nav.classList.contains('open'));
});

document.querySelectorAll('.nav a').forEach(link=>link.addEventListener('click',()=>{
  nav.classList.remove('open');
  menuBtn.classList.remove('open');
  menuBtn.setAttribute('aria-expanded','false');
}));

window.addEventListener('scroll',()=>{
  header.classList.toggle('scrolled',window.scrollY>40);
  toTop.classList.toggle('show',window.scrollY>600);
  updateActiveLink();
});

toTop.addEventListener('click',()=>window.scrollTo({top:0,behavior:'smooth'}));

function updateActiveLink(){
  const sections=document.querySelectorAll('main section[id]');
  let current='home';
  sections.forEach(section=>{
    if(window.scrollY>=section.offsetTop-180) current=section.id;
  });
  document.querySelectorAll('.nav a').forEach(link=>{
    link.classList.toggle('active',link.getAttribute('href')==='#'+current);
  });
}

const filters=document.querySelectorAll('.filter');
const cards=document.querySelectorAll('.product-card');
filters.forEach(button=>button.addEventListener('click',()=>{
  filters.forEach(item=>item.classList.remove('active'));
  button.classList.add('active');
  const category=button.dataset.filter;
  cards.forEach(card=>{
    const visible=category==='all'||card.dataset.category===category;
    card.classList.toggle('hide',!visible);
    if(visible){
      card.style.animation='none';
      requestAnimationFrame(()=>card.style.animation='cardIn .45s ease');
    }
  });
}));

const animationStyle=document.createElement('style');
animationStyle.textContent='@keyframes cardIn{from{opacity:0;transform:translateY(15px)}to{opacity:1;transform:none}}';
document.head.appendChild(animationStyle);

document.querySelectorAll('.order-btn').forEach(button=>button.addEventListener('click',()=>{
  document.getElementById('productSelect').value=button.dataset.product;
  document.getElementById('order').scrollIntoView({behavior:'smooth'});
  setTimeout(()=>document.querySelector('#orderForm input').focus(),700);
}));

document.getElementById('orderForm').addEventListener('submit',async event=>{
  event.preventDefault();
  const endpoint=event.target.dataset.endpoint;
  if(!endpoint){
    alert('Buyurtma qabul qilish manzili hali sozlanmagan. Telefon orqali bog‘laning.');
    return;
  }
  const button=event.target.querySelector('button[type="submit"]');
  button.disabled=true;
  try{
    const response=await fetch(endpoint,{method:'POST',body:new FormData(event.target)});
    if(!response.ok) throw new Error('Buyurtma yuborilmadi');
    toast.classList.add('show');
    event.target.reset();
  }catch(error){
    alert('Buyurtmani yuborishda xatolik bo‘ldi. Iltimos, telefon orqali bog‘laning.');
  }finally{
    button.disabled=false;
  }
  setTimeout(()=>toast.classList.remove('show'),4000);
});

const observer=new IntersectionObserver(entries=>{
  entries.forEach(entry=>{
    if(entry.isIntersecting){
      entry.target.classList.add('visible');
      observer.unobserve(entry.target);
    }
  });
},{threshold:.12});

document.querySelectorAll('.reveal').forEach(element=>observer.observe(element));
document.getElementById('year').textContent=new Date().getFullYear();
updateActiveLink();
