
import os
import re

# Read both versions of HTML
with open('sandboxes/sandbox-f07590b9-de1/cafe/index.html', 'r', encoding='utf-8') as f:
    orig_html = f.read()

with open('cafe/index.html', 'r', encoding='utf-8') as f:
    cart_html = f.read()

# Read both versions of CSS
with open('sandboxes/sandbox-f07590b9-de1/cafe/styles.css', 'r', encoding='utf-8') as f:
    orig_css = f.read()

with open('extensions/kobits-browser/popup.css', 'r', encoding='utf-8') as f:
    cart_css = f.read()

# Read both versions of JS
with open('sandboxes/sandbox-f07590b9-de1/cafe/app.js', 'r', encoding='utf-8') as f:
    orig_js = f.read()

with open('extensions/kobits-browser/popup.js', 'r', encoding='utf-8') as f:
    cart_js = f.read()

print('Loaded all files successfully!')
