/**
 * Kobits Café — Interactive Scripts
 *
 * Features:
 *  1. Slide-out Cart Drawer  — add items, adjust quantities, live subtotal + tax
 *  2. Brew Ratio Calculator  — Pour Over / French Press / Espresso recipes
 *  3. Order Confirmation Popup
 */

'use strict';

/* =========================================================
   CONSTANTS
   ========================================================= */

const TAX_RATE = 0.08; // 8 %

/** Brew method configurations */
const BREW_METHODS = {
  pour_over: {
    label: 'Pour Over',
    ratio: 16,          // 1 g beans : 16 ml water
    brewTime: '3 – 4 min',
    description: 'Gentle extraction, bright and clean cup.',
  },
  french_press: {
    label: 'French Press',
    ratio: 12,          // 1 g beans : 12 ml water
    brewTime: '4 min (+ 30 s press)',
    description: 'Full-body immersion, rich and bold.',
  },
  espresso: {
    label: 'Espresso',
    ratio: 2,           // 1 g beans : 2 ml water (1:2 espresso ratio)
    brewTime: '25 – 30 s',
    description: 'High-pressure, concentrated shot.',
  },
};

/* =========================================================
   STATE
   ========================================================= */

/** @type {Map<string, {id: string, name: string, price: number, qty: number}>} */
const cartItems = new Map();

/* =========================================================
   DOM REFERENCES
   ========================================================= */

const cartToggleBtn       = document.getElementById('cart-toggle-btn');
const cartDrawer          = document.getElementById('cart-drawer');
const cartOverlay         = document.getElementById('cart-overlay');
const cartCloseBtn        = document.getElementById('cart-close-btn');
const cartCount           = document.getElementById('cart-count');
const cartItemsList       = document.getElementById('cart-items-list');
const cartEmptyMsg        = document.getElementById('cart-empty-msg');
const cartSubtotalEl      = document.getElementById('cart-subtotal');
const cartTaxEl           = document.getElementById('cart-tax');
const cartTotalEl         = document.getElementById('cart-total');
const checkoutBtn         = document.getElementById('checkout-btn');

const brewMethodSelect    = document.getElementById('brew-method');
const beanWeightInput     = document.getElementById('bean-weight');
const calcBrewBtn         = document.getElementById('calc-brew-btn');
const brewResultEl        = document.getElementById('brew-result');
const resultMethodEl      = document.getElementById('result-method');
const resultBeansEl       = document.getElementById('result-beans');
const resultWaterEl       = document.getElementById('result-water');
const resultTimeEl        = document.getElementById('result-time');
const resultRatioEl       = document.getElementById('result-ratio');

const confirmationOverlay = document.getElementById('confirmation-overlay');
const confirmationPopup   = document.getElementById('confirmation-popup');
const confirmationMsg     = document.getElementById('confirmation-msg');
const confirmationSummary = document.getElementById('confirmation-order-summary');
const confirmationCloseBtn= document.getElementById('confirmation-close-btn');

const addToOrderBtns      = document.querySelectorAll('.add-to-order-btn');

/* =========================================================
   CART DRAWER — Open / Close
   ========================================================= */

/**
 * Opens the slide-out cart drawer.
 */
function openCart() {
  cartDrawer.classList.add('open');
  cartOverlay.classList.add('active');
  cartOverlay.setAttribute('aria-hidden', 'false');
  cartDrawer.setAttribute('aria-hidden', 'false');
  cartCloseBtn.focus();
  document.body.style.overflow = 'hidden';
}

/**
 * Closes the slide-out cart drawer.
 */
function closeCart() {
  cartDrawer.classList.remove('open');
  cartOverlay.classList.remove('active');
  cartOverlay.setAttribute('aria-hidden', 'true');
  cartDrawer.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';
  cartToggleBtn.focus();
}

cartToggleBtn.addEventListener('click', openCart);
cartCloseBtn.addEventListener('click', closeCart);
cartOverlay.addEventListener('click', closeCart);

// Close on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (!confirmationOverlay.hidden) {
      closeConfirmation();
    } else if (cartDrawer.classList.contains('open')) {
      closeCart();
    }
  }
});

/* =========================================================
   CART — Add Items
   ========================================================= */

/**
 * Adds an item to the cart by its data-id.
 * @param {string} itemId
 * @param {string} itemName
 * @param {number} itemPrice
 */
function addToCart(itemId, itemName, itemPrice) {
  if (cartItems.has(itemId)) {
    cartItems.get(itemId).qty += 1;
  } else {
    cartItems.set(itemId, { id: itemId, name: itemName, price: itemPrice, qty: 1 });
  }
  renderCart();
}

addToOrderBtns.forEach((btn) => {
  btn.addEventListener('click', () => {
    const card   = btn.closest('.menu-card');
    const itemId    = card.dataset.id;
    const itemName  = card.dataset.name;
    const itemPrice = parseFloat(card.dataset.price);

    addToCart(itemId, itemName, itemPrice);

    // Brief visual feedback
    btn.textContent = '✔ Added!';
    btn.classList.add('added');
    setTimeout(() => {
      btn.textContent = 'Add to Order';
      btn.classList.remove('added');
    }, 1200);
  });
});

/* =========================================================
   CART — Render
   ========================================================= */

/**
 * Re-renders the entire cart UI.
 */
function renderCart() {
  cartItemsList.innerHTML = '';

  if (cartItems.size === 0) {
    cartEmptyMsg.style.display = 'block';
    checkoutBtn.disabled = true;
    updateTotals(0, 0);
    updateCartCount(0);
    return;
  }

  cartEmptyMsg.style.display = 'none';
  checkoutBtn.disabled = false;

  let subtotal = 0;

  cartItems.forEach((item) => {
    const lineTotal = item.price * item.qty;
    subtotal += lineTotal;

    const li = document.createElement('li');
    li.className = 'cart-item';
    li.dataset.id = item.id;
    li.innerHTML = `
      <span class="cart-item-name">${escapeHtml(item.name)}</span>
      <span class="cart-item-price">$${lineTotal.toFixed(2)}</span>
      <div class="cart-item-controls">
        <button class="qty-btn qty-dec" aria-label="Decrease quantity of ${escapeHtml(item.name)}">−</button>
        <span class="cart-item-qty" aria-live="polite">${item.qty}</span>
        <button class="qty-btn qty-inc" aria-label="Increase quantity of ${escapeHtml(item.name)}">+</button>
      </div>
      <button class="remove-item-btn" aria-label="Remove ${escapeHtml(item.name)} from cart">Remove</button>
    `;

    // Quantity decrease
    li.querySelector('.qty-dec').addEventListener('click', () => {
      adjustQty(item.id, -1);
    });

    // Quantity increase
    li.querySelector('.qty-inc').addEventListener('click', () => {
      adjustQty(item.id, +1);
    });

    // Remove item entirely
    li.querySelector('.remove-item-btn').addEventListener('click', () => {
      cartItems.delete(item.id);
      renderCart();
    });

    cartItemsList.appendChild(li);
  });

  updateTotals(subtotal, subtotal * TAX_RATE);
  updateCartCount(getTotalQuantity());
}

/**
 * Adjusts the quantity of an item in the cart.
 * @param {string} itemId
 * @param {number} delta — +1 or -1
 */
function adjustQty(itemId, delta) {
  const item = cartItems.get(itemId);
  if (!item) return;
  item.qty += delta;
  if (item.qty <= 0) {
    cartItems.delete(itemId);
  }
  renderCart();
}

/**
 * Returns total number of items (sum of all quantities) in the cart.
 * @returns {number}
 */
function getTotalQuantity() {
  let total = 0;
  cartItems.forEach((item) => (total += item.qty));
  return total;
}

/**
 * Updates the subtotal / tax / total display in the cart footer.
 * @param {number} subtotal
 * @param {number} tax
 */
function updateTotals(subtotal, tax) {
  const total = subtotal + tax;
  cartSubtotalEl.textContent = `$${subtotal.toFixed(2)}`;
  cartTaxEl.textContent      = `$${tax.toFixed(2)}`;
  cartTotalEl.innerHTML      = `<strong>$${total.toFixed(2)}</strong>`;
}

/**
 * Updates the cart item count badge in the header.
 * @param {number} count
 */
function updateCartCount(count) {
  cartCount.textContent = count;
}

/* =========================================================
   CHECKOUT — Confirmation Popup
   ========================================================= */

/**
 * Builds and shows the order confirmation popup.
 */
function showConfirmation() {
  // Build summary HTML
  let summaryHtml = '';
  let subtotal = 0;

  cartItems.forEach((item) => {
    const lineTotal = item.price * item.qty;
    subtotal += lineTotal;
    summaryHtml += `<p>${escapeHtml(item.name)} × ${item.qty} — $${lineTotal.toFixed(2)}</p>`;
  });

  const tax   = subtotal * TAX_RATE;
  const total = subtotal + tax;

  summaryHtml += `<p class="summary-total">Total (incl. tax): $${total.toFixed(2)}</p>`;
  confirmationSummary.innerHTML = summaryHtml;

  // Show the popup
  confirmationOverlay.hidden = false;
  confirmationOverlay.setAttribute('aria-hidden', 'false');
  confirmationCloseBtn.focus();
  document.body.style.overflow = 'hidden';

  // Close drawer behind it
  cartDrawer.classList.remove('open');
  cartOverlay.classList.remove('active');
}

/**
 * Hides the confirmation popup and resets the cart.
 */
function closeConfirmation() {
  confirmationOverlay.hidden = true;
  confirmationOverlay.setAttribute('aria-hidden', 'true');
  document.body.style.overflow = '';

  // Clear cart after confirmed order
  cartItems.clear();
  renderCart();
  cartToggleBtn.focus();
}

checkoutBtn.addEventListener('click', () => {
  if (cartItems.size > 0) {
    showConfirmation();
  }
});

confirmationCloseBtn.addEventListener('click', closeConfirmation);
confirmationOverlay.addEventListener('click', (e) => {
  if (e.target === confirmationOverlay) {
    closeConfirmation();
  }
});

/* =========================================================
   BREW RATIO CALCULATOR
   ========================================================= */

/**
 * Calculates and displays the brew recipe based on method and bean weight.
 */
function calculateBrewRatio() {
  const methodKey  = brewMethodSelect.value;
  const beanWeight = parseFloat(beanWeightInput.value);

  if (!methodKey || isNaN(beanWeight) || beanWeight <= 0) {
    brewResultEl.hidden = true;
    beanWeightInput.setCustomValidity('Please enter a valid bean weight greater than 0.');
    beanWeightInput.reportValidity();
    return;
  }

  beanWeightInput.setCustomValidity('');

  const method    = BREW_METHODS[methodKey];
  const waterMl   = Math.round(beanWeight * method.ratio);
  const ratioStr  = `1 : ${method.ratio}`;

  resultMethodEl.textContent = method.label;
  resultBeansEl.textContent  = beanWeight.toFixed(1);
  resultWaterEl.textContent  = `${waterMl} ml`;
  resultTimeEl.textContent   = method.brewTime;
  resultRatioEl.textContent  = ratioStr;

  brewResultEl.hidden = false;
}

calcBrewBtn.addEventListener('click', calculateBrewRatio);

// Allow pressing Enter in the input to calculate
beanWeightInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    calculateBrewRatio();
  }
});

// Auto-recalculate if result is already visible and method changes
brewMethodSelect.addEventListener('change', () => {
  if (!brewResultEl.hidden) {
    calculateBrewRatio();
  }
});

/* =========================================================
   UTILITIES
   ========================================================= */

/**
 * Escapes HTML special characters to prevent XSS.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  var s = String(str);
  s = s.replace(/&/g, '&amp;');
  s = s.replace(/</g, '&lt;');
  s = s.replace(/>/g, '&gt;');
  s = s.replace(/\x22/g, '&quot;');
  s = s.replace(/\x27/g, '&#39;');
  return s;
}

/* =========================================================
   INIT
   ========================================================= */

// Render empty cart state on page load
renderCart();
