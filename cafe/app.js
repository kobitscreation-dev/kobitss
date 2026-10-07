/**
 * The Daily Grind Café — app.js
 * Vanilla JS, no framework, no build step required.
 */

document.addEventListener('DOMContentLoaded', function () {
  'use strict';

  /* ============================================================
     THEME TOGGLE
     Persists user preference to localStorage.
     Toggles data-theme between 'light' and 'dark'.
  ============================================================ */

  var themeToggleBtn = document.getElementById('theme-toggle');
  var htmlEl = document.documentElement;

  // Restore saved theme on load
  (function initTheme() {
    var saved = localStorage.getItem('cafe-theme');
    if (saved === 'dark' || saved === 'light') {
      htmlEl.setAttribute('data-theme', saved);
    }
  })();

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener('click', function () {
      var current = htmlEl.getAttribute('data-theme') || 'light';
      var next = current === 'dark' ? 'light' : 'dark';
      htmlEl.setAttribute('data-theme', next);
      localStorage.setItem('cafe-theme', next);
    });
  }

  /* ============================================================
     MENU FILTER
     Filter buttons toggle .active class and show/hide cards.
     Uses [hidden] attribute to hide non-matching cards.
  ============================================================ */

  var filterBtns = document.querySelectorAll('.filter-btn');
  var menuCards  = document.querySelectorAll('.menu-card');

  filterBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var filter = btn.getAttribute('data-filter');

      // Update active button
      filterBtns.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');

      // Show/hide cards
      menuCards.forEach(function (card) {
        var category = card.getAttribute('data-category');
        if (filter === 'all' || category === filter) {
          card.removeAttribute('hidden');
        } else {
          card.setAttribute('hidden', '');
        }
      });
    });
  });

  /* ============================================================
     CART STATE
  ============================================================ */

  // cart is an array of { name, price, qty }
  var cart = [];

  function findCartItem(name) {
    return cart.find(function (item) { return item.name === name; });
  }

  /* ============================================================
     ADD TO ORDER — 'Add to Order' button listeners
     Adds items to cart array, re-renders line items,
     recalculates subtotal + tax (8.5%) + total.
  ============================================================ */

  var addToOrderBtns = document.querySelectorAll('.add-to-order');

  addToOrderBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var name  = btn.getAttribute('data-name');
      var price = parseFloat(btn.getAttribute('data-price'));

      var existing = findCartItem(name);
      if (existing) {
        existing.qty += 1;
      } else {
        cart.push({ name: name, price: price, qty: 1 });
      }

      renderCart();
      openCart();

      // Brief feedback animation
      btn.textContent = 'Added ✓';
      btn.disabled = true;
      setTimeout(function () {
        btn.textContent = 'Add to Order';
        btn.disabled = false;
      }, 1200);
    });
  });

  /* ============================================================
     CART RENDER
  ============================================================ */

  var cartItemsEl   = document.getElementById('cart-items');
  var cartSubtotal  = document.getElementById('cart-subtotal');
  var cartTax       = document.getElementById('cart-tax');
  var cartTotalEl   = document.getElementById('cart-total');
  var cartCountEl   = document.getElementById('cart-count');

  var TAX_RATE = 0.085; // 8.5%

  function renderCart() {
    if (!cartItemsEl) return;

    var totalQty = cart.reduce(function (sum, item) { return sum + item.qty; }, 0);
    if (cartCountEl) cartCountEl.textContent = totalQty;

    if (cart.length === 0) {
      cartItemsEl.innerHTML = '<p class="cart-empty">Your cart is empty.</p>';
      updateCartTotals(0);
      return;
    }

    var html = '';
    cart.forEach(function (item) {
      var lineTotal = (item.price * item.qty).toFixed(2);
      html += '<div class="cart-line-item">' +
        '<span class="cart-item-name">' + escapeHtml(item.name) + '</span>' +
        '<span class="cart-item-qty">x' + item.qty + '</span>' +
        '<span class="cart-item-price">$' + lineTotal + '</span>' +
        '</div>';
    });
    cartItemsEl.innerHTML = html;

    var subtotal = cart.reduce(function (sum, item) {
      return sum + item.price * item.qty;
    }, 0);
    updateCartTotals(subtotal);
  }

  function updateCartTotals(subtotal) {
    var tax   = subtotal * TAX_RATE;
    var total = subtotal + tax;

    if (cartSubtotal) cartSubtotal.textContent = '$' + subtotal.toFixed(2);
    if (cartTax)      cartTax.textContent      = '$' + tax.toFixed(2);
    if (cartTotalEl)  cartTotalEl.textContent  = '$' + total.toFixed(2);
  }

  /* ============================================================
     CART DRAWER TOGGLE
     Open/close via cart icon button.
     Adds/removes .open class and toggles [hidden] attribute.
  ============================================================ */

  var cartDrawer    = document.getElementById('cart-drawer');
  var cartOverlay   = document.getElementById('cart-overlay');
  var cartToggleBtn = document.getElementById('cart-toggle');
  var cartCloseBtn  = document.getElementById('cart-close');
  var checkoutBtn   = document.getElementById('checkout-btn');

  function openCart() {
    if (!cartDrawer) return;
    cartDrawer.removeAttribute('hidden');
    // Small delay so CSS transition fires after display change
    requestAnimationFrame(function () {
      cartDrawer.classList.add('open');
    });
    if (cartOverlay) {
      cartOverlay.removeAttribute('hidden');
    }
    document.body.style.overflow = 'hidden';
  }

  function closeCart() {
    if (!cartDrawer) return;
    cartDrawer.classList.remove('open');
    // Wait for transition to finish before re-hiding
    cartDrawer.addEventListener('transitionend', function handler() {
      cartDrawer.setAttribute('hidden', '');
      cartDrawer.removeEventListener('transitionend', handler);
    });
    if (cartOverlay) {
      cartOverlay.setAttribute('hidden', '');
    }
    document.body.style.overflow = '';
  }

  if (cartToggleBtn) {
    cartToggleBtn.addEventListener('click', function () {
      var isHidden = cartDrawer && cartDrawer.hasAttribute('hidden');
      if (isHidden) {
        openCart();
      } else {
        closeCart();
      }
    });
  }

  if (cartCloseBtn) {
    cartCloseBtn.addEventListener('click', closeCart);
  }

  if (cartOverlay) {
    cartOverlay.addEventListener('click', closeCart);
  }

  if (checkoutBtn) {
    checkoutBtn.addEventListener('click', function () {
      if (cart.length === 0) {
        alert('Your cart is empty! Add some items first.');
        return;
      }
      alert('Thank you for your order! Your total is ' + cartTotalEl.textContent + '.');
      cart = [];
      renderCart();
      closeCart();
    });
  }

  /* ============================================================
     BREW RATIO CALCULATOR
     Input event listeners on #coffee-grams and #water-ml.
     Compute ratio = water / coffee.
     Strength: Weak <1:12, Balanced 1:12–1:17, Strong >1:17.
  ============================================================ */

  var coffeeInput    = document.getElementById('coffee-grams');
  var waterInput     = document.getElementById('water-ml');
  var ratioOutput    = document.getElementById('brew-ratio-output');
  var strengthOutput = document.getElementById('brew-strength-output');

  function calcBrewRatio() {
    if (!coffeeInput || !waterInput) return;

    var coffeeGrams = parseFloat(coffeeInput.value);
    var waterMl     = parseFloat(waterInput.value);

    if (isNaN(coffeeGrams) || coffeeGrams <= 0 ||
        isNaN(waterMl)     || waterMl <= 0) {
      if (ratioOutput)    ratioOutput.textContent    = '—';
      if (strengthOutput) strengthOutput.textContent = '—';
      return;
    }

    var ratio = waterMl / coffeeGrams;

    if (ratioOutput) {
      ratioOutput.textContent = '1 : ' + ratio.toFixed(1);
    }

    if (strengthOutput) {
      var label;
      var cls;

      if (ratio < 12) {
        label = 'Strong';
        cls   = 'strong';
      } else if (ratio <= 17) {
        label = 'Balanced';
        cls   = 'balanced';
      } else {
        label = 'Weak';
        cls   = 'weak';
      }

      strengthOutput.textContent = label;
      strengthOutput.className   = 'strength-badge ' + cls;
    }
  }

  if (coffeeInput) coffeeInput.addEventListener('input', calcBrewRatio);
  if (waterInput)  waterInput.addEventListener('input',  calcBrewRatio);

  // Run once on load with default values
  calcBrewRatio();

  /* ============================================================
     TABLE RESERVATION MODAL
     Form submit preventDefault, populate modal with booking
     details, show modal, close button hides modal.
  ============================================================ */

  var reservationForm  = document.getElementById('reservation-form');
  var reservationModal = document.getElementById('reservation-modal');
  var modalClose       = document.getElementById('modal-close');
  var modalDone        = document.getElementById('modal-done');
  var modalDetails     = document.getElementById('modal-details');

  function showModal() {
    if (!reservationModal) return;
    reservationModal.removeAttribute('hidden');
    document.body.style.overflow = 'hidden';
  }

  function hideModal() {
    if (!reservationModal) return;
    reservationModal.setAttribute('hidden', '');
    document.body.style.overflow = '';
  }

  if (reservationForm) {
    reservationForm.addEventListener('submit', function (e) {
      e.preventDefault();

      var name   = (reservationForm.querySelector('#res-name')   || {}).value  || '';
      var email  = (reservationForm.querySelector('#res-email')  || {}).value  || '';
      var date   = (reservationForm.querySelector('#res-date')   || {}).value  || '';
      var time   = (reservationForm.querySelector('#res-time')   || {}).value  || '';
      var guests = (reservationForm.querySelector('#res-guests') || {}).value  || '';

      // Basic validation
      if (!name || !date || !time || !guests) {
        alert('Please fill in all required fields.');
        return;
      }

      // Populate modal
      if (modalDetails) {
        modalDetails.innerHTML =
          '<strong>Name:</strong> '   + escapeHtml(name)   + '<br>' +
          '<strong>Email:</strong> '  + escapeHtml(email)  + '<br>' +
          '<strong>Date:</strong> '   + escapeHtml(date)   + '<br>' +
          '<strong>Time:</strong> '   + escapeHtml(time)   + '<br>' +
          '<strong>Guests:</strong> ' + escapeHtml(guests) + '<br><br>' +
          'We look forward to seeing you! A confirmation email has been sent to <strong>' +
          escapeHtml(email) + '</strong>.';
      }

      showModal();
      reservationForm.reset();
    });
  }

  if (modalClose) modalClose.addEventListener('click', hideModal);
  if (modalDone)  modalDone.addEventListener('click',  hideModal);

  // Close modal on overlay click
  if (reservationModal) {
    reservationModal.addEventListener('click', function (e) {
      if (e.target === reservationModal) hideModal();
    });
  }

  /* ============================================================
     UTILITY HELPERS
  ============================================================ */

  function escapeHtml(str) {
    var s = String(str);
    s = s.replace(/&/g, '&amp;');
    s = s.replace(/</g, '&lt;');
    s = s.replace(/>/g, '&gt;');
    s = s.replace(/\x22/g, '&quot;');
    s = s.replace(/\x27/g, '&#39;');
    return s;
  }

}); // end DOMContentLoaded
