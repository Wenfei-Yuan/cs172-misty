# ADHD Reading Support — Stage 1 Setup

## Folder structure so far

```
adhd-extension/
├── manifest.json
├── background.js
├── content_script.js
├── reader.css          ← placeholder, fills out in Stage 2
├── Readability.js      ← you need to download this (see below)
└── icons/
    ├── icon16.png
    ├── icon48.png
    └── icon128.png
```

---

## Step 1: Get Readability.js

Download the single file from Mozilla's GitHub:

  https://raw.githubusercontent.com/mozilla/readability/main/Readability.js

Save it as `Readability.js` in the extension root (same folder as manifest.json).

---

## Step 2: Add icons (optional for now)

Chrome requires icon files declared in manifest.json to exist, but you can
use placeholder PNGs for development. Simplest option:

  1. Create an `icons/` folder
  2. Drop any 16×16, 48×48, and 128×128 PNG in there

Or temporarily remove the `"default_icon"` block from manifest.json — Chrome
will show a generic puzzle-piece icon instead, which is fine for testing.

---

## Step 3: Load the extension in Chrome

1. Open Chrome and go to:  chrome://extensions
2. Enable "Developer mode" (toggle in the top-right corner)
3. Click "Load unpacked"
4. Select the `adhd-extension/` folder

You should see "ADHD Reading Support" appear in the list.

---

## Step 4: Test it

1. Navigate to any news article or Wikipedia page
2. Click the ADHD Reader icon in the Chrome toolbar
3. A white overlay should appear showing the article title
4. Open DevTools (F12) → Console tab
5. You should see a "[ADHD Reader] Extraction result" group with:
   - Title
   - Byline
   - Character length
   - A 300-char preview of the article text

---

## What to check if something goes wrong

**Extension doesn't appear after loading:**
→ Check the errors panel on chrome://extensions — usually a JSON syntax error in manifest.json

**Overlay appears but console shows "Readability could not parse":**
→ The page might be a SPA or login-gated. Try a standard news article (BBC, NYT, Wikipedia).

**"Injection failed" in console:**
→ Chrome blocks injection on chrome:// pages and the Chrome Web Store. Use a real webpage.

**Double-clicking the icon causes nothing (or a second overlay):**
→ The guard at the top of content_script.js prevents double injection. Close the overlay first.

---

## What's stored for Stage 2

The extracted article is attached to the overlay's host element:

```js
document.getElementById("adhd-reader-root").__articleData
// { title, byline, content, textContent, length, excerpt }
```

`reader.js` in Stage 2 will read this directly rather than re-running Readability.
