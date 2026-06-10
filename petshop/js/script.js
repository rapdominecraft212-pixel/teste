// ============== PRODUTOS ==============
const products = [
  { id: 1, name: "Ração Premium Cães", category: "caes", price: 89.90, image: "https://images.unsplash.com/photo-1589924691195-41432c84c161?w=600&q=90&fit=crop", badge: "Mais Vendido" },
  { id: 2, name: "Ração Premium Gatos", category: "gatos", price: 79.90, image: "https://images.unsplash.com/photo-1514888286974-6c03e2ca1dba?w=600&q=90&fit=crop", badge: "" },
  { id: 3, name: "Coleira Conforto", category: "acessorios", price: 49.90, image: "https://images.unsplash.com/photo-1552053833-1411d7a8b6a8?w=600&q=90&fit=crop", badge: "" },
  { id: 4, name: "Shampoo Neutro", category: "higiene", price: 34.90, image: "https://images.unsplash.com/photo-1556228720-1957be83f314?w=600&q=90&fit=crop", badge: "" },
  { id: 5, name: "Brinquedo Kong", category: "caes", price: 59.90, image: "https://images.unsplash.com/photo-1535930749574-1399327ce78f?w=600&q=90&fit=crop", badge: "Promoção" },
  { id: 6, name: "Arranhador Torre", category: "gatos", price: 129.90, image: "https://images.unsplash.com/photo-1545249390-6bdfa286032f?w=600&q=90&fit=crop", badge: "" },
  { id: 7, name: "Cama Ortopédica", category: "caes", price: 149.90, image: "https://images.unsplash.com/photo-1591946614720-885a9ed8a629?w=600&q=90&fit=crop", badge: "Novidade" },
  { id: 8, name: "Guia Retrátil", category: "acessorios", price: 69.90, image: "https://images.unsplash.com/photo-1551845856-c67fb5084daf?w=600&q=90&fit=crop", badge: "" },
  { id: 9, name: "Antipulgas Combo", category: "higiene", price: 89.90, image: "https://images.unsplash.com/photo-1583337130417-3346a1be7dee?w=600&q=90&fit=crop", badge: "" },
  { id: 10, name: "Roupinha Fashion", category: "acessorios", price: 54.90, image: "https://images.unsplash.com/photo-1576201836106-db1758fd1c97?w=600&q=90&fit=crop", badge: "" },
  { id: 11, name: "Petisco Natural", category: "caes", price: 24.90, image: "https://images.unsplash.com/photo-1582798358481-d199fb7347bb?w=600&q=90&fit=crop", badge: "" },
  { id: 12, name: "Fonte de Água", category: "gatos", price: 99.90, image: "https://images.unsplash.com/photo-1583511655826-1e28b84e84b7?w=600&q=90&fit=crop", badge: "" },
];

// ============== STATE ==============
let cart = JSON.parse(localStorage.getItem("petlove_cart")) || [];
let currentFilter = "todos";

// ============== DOM REFS ==============
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const productsGrid = $("#productsGrid");
const cartItems = $("#cartItems");
const cartCount = $("#cartCount");
const cartTotal = $("#cartTotal");
const cartSidebar = $("#cartSidebar");
const cartOverlay = $("#cartOverlay");
const cartFooter = $("#cartFooter");
const toast = $("#toast");

// ============== RENDER PRODUCTS ==============
function renderProducts(filter = "todos") {
  const filtered = filter === "todos"
    ? products
    : products.filter(p => p.category === filter);

  productsGrid.innerHTML = filtered.map(p => `
    <div class="product-card" data-id="${p.id}">
      <div class="product-image" style="background: none; overflow: hidden;">
        ${p.badge ? `<span class="product-badge">${p.badge}</span>` : ""}
        <img src="${p.image}" alt="${p.name}" loading="lazy"
             style="width:100%;height:100%;object-fit:cover;">
      </div>
      <div class="product-body">
        <span class="product-category">${categoryLabel(p.category)}</span>
        <h3>${p.name}</h3>
        <p>Produto de alta qualidade para seu pet.</p>
        <div class="product-footer">
          <span class="product-price">R$ ${p.price.toFixed(2)}</span>
          <button class="add-to-cart" data-id="${p.id}" aria-label="Adicionar ao carrinho">+</button>
        </div>
      </div>
    </div>
  `).join("");
}

function categoryLabel(cat) {
  const map = { caes: "Cães", gatos: "Gatos", acessorios: "Acessórios", higiene: "Higiene" };
  return map[cat] || cat;
}

// ============== RENDER CART ==============
function renderCart() {
  if (cart.length === 0) {
    cartItems.innerHTML = `
      <div class="cart-empty">
        <div class="icon">🛒</div>
        <p>Seu carrinho está vazio</p>
        <p style="font-size:0.9rem; margin-top:8px;">Adicione produtos para começar</p>
      </div>
    `;
    cartFooter.style.display = "block";
    cartCount.textContent = "0";
    cartTotal.textContent = "R$ 0,00";
    return;
  }

  cartItems.innerHTML = cart.map((item, idx) => `
    <div class="cart-item">
      <div class="cart-item-image" style="overflow:hidden;">
        <img src="${item.image}" alt="${item.name}"
             style="width:100%;height:100%;object-fit:cover;border-radius:8px;">
      </div>
      <div class="cart-item-info">
        <h4>${item.name}</h4>
        <p>R$ ${item.price.toFixed(2)}</p>
        <div class="cart-item-qty">
          <button onclick="updateQty(${idx}, -1)">−</button>
          <span>${item.qty}</span>
          <button onclick="updateQty(${idx}, 1)">+</button>
        </div>
      </div>
      <button class="cart-item-remove" onclick="removeItem(${idx})">✕</button>
    </div>
  `).join("");

  const total = cart.reduce((sum, item) => sum + item.price * item.qty, 0);
  const count = cart.reduce((sum, item) => sum + item.qty, 0);

  cartCount.textContent = count;
  cartTotal.textContent = `R$ ${total.toFixed(2)}`;
  cartFooter.style.display = "block";
  saveCart();
}

function updateQty(idx, delta) {
  cart[idx].qty += delta;
  if (cart[idx].qty <= 0) {
    cart.splice(idx, 1);
  }
  renderCart();
  updateCartCount();
}

function removeItem(idx) {
  cart.splice(idx, 1);
  renderCart();
  updateCartCount();
}

function updateCartCount() {
  const count = cart.reduce((s, i) => s + i.qty, 0);
  cartCount.textContent = count;
}

function saveCart() {
  localStorage.setItem("petlove_cart", JSON.stringify(cart));
}

// ============== ADD TO CART ==============
function addToCart(productId) {
  const product = products.find(p => p.id === productId);
  if (!product) return;

  const existing = cart.find(item => item.id === productId);
  if (existing) {
    existing.qty += 1;
  } else {
    cart.push({ ...product, qty: 1 });
  }

  renderCart();
  updateCartCount();
  showToast(`${product.name} adicionado ao carrinho!`);
}

// ============== TOAST ==============
function showToast(msg) {
  toast.textContent = msg;
  toast.classList.add("show");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => toast.classList.remove("show"), 2800);
}

// ============== CART OPEN/CLOSE ==============
function openCart() {
  cartSidebar.classList.add("open");
  cartOverlay.classList.add("open");
  document.body.style.overflow = "hidden";
}

function closeCart() {
  cartSidebar.classList.remove("open");
  cartOverlay.classList.remove("open");
  document.body.style.overflow = "";
}

// ============== MODAL ==============
function openModal() {
  $("#modalOverlay").classList.add("open");
  document.body.style.overflow = "hidden";
}

function closeModal() {
  $("#modalOverlay").classList.remove("open");
  document.body.style.overflow = "";
}

// ============== EVENTS ==============

// Cart buttons
$("#cartBtn").addEventListener("click", openCart);
$("#cartClose").addEventListener("click", closeCart);
cartOverlay.addEventListener("click", closeCart);

// Checkout
$("#cartCheckout").addEventListener("click", () => {
  if (cart.length === 0) {
    showToast("Seu carrinho está vazio!");
    return;
  }
  closeCart();
  openModal();
  cart = [];
  renderCart();
  updateCartCount();
  saveCart();
});

// Modal
$("#modalClose").addEventListener("click", closeModal);
$("#modalOverlay").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeModal();
});

// Product filters
$$(".filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    $$(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentFilter = btn.dataset.filter;
    renderProducts(currentFilter);
  });
});

// Add to cart (event delegation)
productsGrid.addEventListener("click", (e) => {
  const btn = e.target.closest(".add-to-cart");
  if (btn) addToCart(Number(btn.dataset.id));
});

// Mobile toggle
$("#mobileToggle").addEventListener("click", () => {
  $("#nav").classList.toggle("open");
});

// Close mobile nav on link click
$$(".nav a").forEach(a => {
  a.addEventListener("click", () => {
    $("#nav").classList.remove("open");
  });
});

// Header scroll effect
window.addEventListener("scroll", () => {
  const header = $("#header");
  header.classList.toggle("scrolled", window.scrollY > 40);

  // Back to top
  const btn = $("#backToTop");
  btn.classList.toggle("visible", window.scrollY > 400);
});

// Back to top
$("#backToTop").addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
});

// Nav active on scroll
const sections = $$("section[id]");
window.addEventListener("scroll", () => {
  let current = "";
  sections.forEach(s => {
    if (window.scrollY >= s.offsetTop - 200) {
      current = s.id;
    }
  });
  $$(".nav a").forEach(a => {
    a.classList.toggle("active", a.getAttribute("href") === `#${current}`);
  });
});

// Contact form
$("#contactForm").addEventListener("submit", (e) => {
  e.preventDefault();
  showToast("Mensagem enviada com sucesso! Entraremos em contato.");
  e.target.reset();
});

// Keyboard: ESC to close cart/modal
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeCart();
    closeModal();
  }
});

// ============== INIT ==============
renderProducts();
renderCart();
updateCartCount();
