(function () {
  'use strict';

  var LS_THEME = 'mdwiki.theme';
  var LS_SIDEBAR_HIDDEN = 'mdwiki.sidebarHidden';

  function storage() {
    try { return window.localStorage; } catch (e) { return null; }
  }

  function setStorageItem(key, value) {
    var s = storage();
    if (!s) return;
    try {
      s.setItem(key, value);
    } catch (e) {
      // Ignore storage write failures (private mode, quota exceeded, policy restrictions).
    }
  }

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    setStorageItem(LS_THEME, theme);
    var btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = theme === 'dark' ? '☀' : '☾';
  }

  function currentTheme() {
    return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  }

  function setSidebarHidden(hidden) {
    if (hidden) {
      document.documentElement.dataset.sidebar = 'hidden';
    } else {
      delete document.documentElement.dataset.sidebar;
    }
    setStorageItem(LS_SIDEBAR_HIDDEN, hidden ? '1' : '0');
  }

  function isSidebarHidden() {
    return document.documentElement.dataset.sidebar === 'hidden';
  }

  function bindThemeToggle() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    btn.textContent = currentTheme() === 'dark' ? '☀' : '☾';
    btn.addEventListener('click', function () {
      setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    });
  }

  function bindSidebarToggle() {
    var btn = document.getElementById('sidebar-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        setSidebarHidden(!isSidebarHidden());
      });
    }
    document.addEventListener('keydown', function (e) {
      var mod = e.metaKey || e.ctrlKey;
      if (mod && (e.key === '\\' || e.code === 'Backslash')) {
        e.preventDefault();
        setSidebarHidden(!isSidebarHidden());
      }
    });
  }

  function bindTreeFilter() {
    var input = document.getElementById('sidebar-filter-input');
    var tree = document.getElementById('sidebar-tree');
    if (!input || !tree) return;
    var rows = Array.prototype.slice.call(tree.querySelectorAll('li, .tree-row'));
    var rowCache = rows.map(function (row) {
      return { row: row, label: (row.textContent || '').toLowerCase() };
    });
    var timer = null;
    function applyFilter() {
      var q = input.value.trim().toLowerCase();
      rowCache.forEach(function (entry) {
        if (!q) {
          entry.row.hidden = false;
          return;
        }
        entry.row.hidden = entry.label.indexOf(q) === -1;
      });
    }
    input.addEventListener('input', function () {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(applyFilter, 120);
    });
  }

  function bindTreeFoldersFor(tree) {
    if (!tree) return;
    tree.addEventListener('click', function (e) {
      var folder = e.target.closest('.tree-folder');
      if (!folder || !tree.contains(folder)) return;
      var collapsed = folder.dataset.collapsed === '1';
      folder.dataset.collapsed = collapsed ? '0' : '1';
    });
  }

  function bindTreeFolders() {
    bindTreeFoldersFor(document.getElementById('sidebar-tree'));
    bindTreeFoldersFor(document.getElementById('mobile-tree'));
  }

  function bindMobileMenu() {
    var menu = document.getElementById('mobile-menu');
    var backdrop = document.getElementById('mobile-menu-backdrop');
    var openBtn = document.getElementById('mobile-menu-toggle');
    var closeBtn = document.getElementById('mobile-menu-close');
    if (!menu || !backdrop || !openBtn) return;

    // Background region disabled while the drawer is open. Browsers without
    // inert support still get aria-hidden; that does not fully trap focus, but
    // it keeps the background hidden from assistive technology.
    var appBody = document.querySelector('.app-body');

    function setOpen(open) {
      menu.hidden = !open;
      backdrop.hidden = !open;
      document.documentElement.dataset.mobileMenu = open ? 'open' : '';
      openBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (appBody) {
        if (open) {
          appBody.setAttribute('inert', '');
          appBody.setAttribute('aria-hidden', 'true');
        } else {
          appBody.removeAttribute('inert');
          appBody.removeAttribute('aria-hidden');
        }
      }
      if (open && closeBtn) closeBtn.focus();
    }

    openBtn.addEventListener('click', function () {
      setOpen(true);
    });
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        setOpen(false);
        openBtn.focus();
      });
    }
    backdrop.addEventListener('click', function () {
      setOpen(false);
    });
    menu.addEventListener('click', function (e) {
      var link = e.target.closest('a');
      if (link && menu.contains(link)) setOpen(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !menu.hidden) setOpen(false);
    });
  }

  // ---- Edit page wiring ----

  function getSelection(ta) {
    return {
      start: ta.selectionStart,
      end: ta.selectionEnd,
      text: ta.value.substring(ta.selectionStart, ta.selectionEnd),
    };
  }

  function wrapSelection(ta, before, after) {
    var sel = getSelection(ta);
    var newText = ta.value.substring(0, sel.start) + before + sel.text + after + ta.value.substring(sel.end);
    ta.value = newText;
    ta.selectionStart = sel.start + before.length;
    ta.selectionEnd = sel.end + before.length;
  }

  function prefixCurrentLine(ta, prefix) {
    var s = ta.selectionStart;
    var lineStart = ta.value.lastIndexOf('\n', s - 1) + 1;
    ta.value = ta.value.substring(0, lineStart) + prefix + ta.value.substring(lineStart);
    ta.selectionStart = ta.selectionEnd = s + prefix.length;
  }

  function insertAtCursor(ta, text) {
    var s = ta.selectionStart, e = ta.selectionEnd;
    ta.value = ta.value.substring(0, s) + text + ta.value.substring(e);
    ta.selectionStart = ta.selectionEnd = s + text.length;
  }

  function applyFormat(fmt, ta) {
    switch (fmt) {
      case 'bold':      wrapSelection(ta, '**', '**'); break;
      case 'italic':    wrapSelection(ta, '*', '*'); break;
      case 'strike':    wrapSelection(ta, '~~', '~~'); break;
      case 'code':      wrapSelection(ta, '`', '`'); break;
      case 'h1':        prefixCurrentLine(ta, '# '); break;
      case 'h2':        prefixCurrentLine(ta, '## '); break;
      case 'h3':        prefixCurrentLine(ta, '### '); break;
      case 'quote':     prefixCurrentLine(ta, '> '); break;
      case 'ul':        prefixCurrentLine(ta, '- '); break;
      case 'ol':        prefixCurrentLine(ta, '1. '); break;
      case 'task':      prefixCurrentLine(ta, '- [ ] '); break;
      case 'codeblock': wrapSelection(ta, '```\n', '\n```'); break;
      case 'link':      wrapSelection(ta, '[', '](url)'); break;
      case 'image':     wrapSelection(ta, '![', '](url)'); break;
      case 'hr':        insertAtCursor(ta, '\n\n---\n\n'); break;
    }
  }

  function bindEditPage() {
    var form = document.getElementById('edit-form');
    if (!form) return;
    var textarea = document.getElementById('edit-text');
    var preview = document.getElementById('edit-preview');
    var dirty = document.getElementById('dirty-marker');
    var previewBtn = document.getElementById('preview-toggle');
    if (!textarea || !preview) return;
    var initialText = textarea.value;
    var previewRequestSeq = 0;
    var activePreviewAbort = null;

    textarea.addEventListener('input', function () {
      if (dirty) dirty.hidden = (textarea.value === initialText);
    });

    function showEdit() {
      if (activePreviewAbort) {
        activePreviewAbort.abort();
        activePreviewAbort = null;
      }
      preview.hidden = true;
      textarea.hidden = false;
      if (previewBtn) {
        previewBtn.innerHTML = '👁 Preview';
        previewBtn.disabled = false;
      }
      textarea.focus();
    }

    function showPreview() {
      if (!previewBtn) return;
      if (activePreviewAbort) activePreviewAbort.abort();
      var reqId = ++previewRequestSeq;
      var fd = new FormData();
      fd.append('text', textarea.value);
      fd.append('csrf_token', (form.querySelector('input[name=csrf_token]') || {}).value || '');
      activePreviewAbort = new AbortController();
      previewBtn.disabled = true;
      fetch(form.action + '?action=preview', { method: 'POST', body: fd, signal: activePreviewAbort.signal })
        .then(function (r) {
          if (!r.ok) {
            var err = new Error('Preview request failed');
            err.status = r.status;
            throw err;
          }
          return r.text();
        })
        .then(function (html) {
          if (reqId !== previewRequestSeq) return;
          preview.innerHTML = html;
          textarea.hidden = true;
          preview.hidden = false;
          previewBtn.innerHTML = 'Back to edit';
        })
        .catch(function (err) {
          if (err && err.name === 'AbortError') return;
          var msg = 'Could not load the preview. Try again later.';
          if (err && err.status === 403) msg = 'Preview permission or the security token has expired. Refresh the page and try again.';
          preview.innerHTML = '<p class="preview-error"></p>';
          preview.querySelector('.preview-error').textContent = msg;
          textarea.hidden = true;
          preview.hidden = false;
          previewBtn.innerHTML = 'Back to edit';
        })
        .finally(function () {
          if (reqId !== previewRequestSeq) return;
          activePreviewAbort = null;
          previewBtn.disabled = false;
        });
    }

    if (previewBtn) {
      previewBtn.addEventListener('click', function () {
        if (preview.hidden) showPreview(); else showEdit();
      });
    }

    form.querySelectorAll('.ed-btn[data-fmt]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        applyFormat(btn.dataset.fmt, textarea);
        textarea.dispatchEvent(new Event('input'));
        textarea.focus();
      });
    });

    // ---- File / image upload (button + paste + drag-and-drop) ----
    var statusEl = document.getElementById('upload-status');
    var attachBtn = document.getElementById('attach-btn');
    var fileInput = document.getElementById('attach-input');

    function setUploadStatus(text, isError, autohide) {
      if (!statusEl) return;
      statusEl.textContent = text;
      statusEl.hidden = false;
      statusEl.classList.toggle('is-error', !!isError);
      if (autohide) {
        setTimeout(function () {
          if (statusEl.textContent === text) statusEl.hidden = true;
        }, 2500);
      }
    }

    function uploadFiles(files) {
      var list = Array.prototype.slice.call(files || []);
      if (!list.length) return;
      var csrf = (form.querySelector('input[name=csrf_token]') || {}).value || '';
      setUploadStatus('Uploading…');
      var failures = 0;
      var chain = Promise.resolve();
      list.forEach(function (file) {
        chain = chain.then(function () {
          var fd = new FormData();
          fd.append('action', 'upload');
          fd.append('csrf_token', csrf);
          fd.append('file', file, file.name || 'pasted');
          return fetch(form.action, {
            method: 'POST',
            body: fd,
            headers: { 'Accept': 'application/json' }
          }).then(function (r) {
            return r.json().catch(function () {
              return { ok: false, error: 'Upload failed (' + r.status + ').' };
            });
          }).then(function (data) {
            if (!data.ok) {
              failures++;
              setUploadStatus(data.error || 'Upload failed.', true);
              return;
            }
            insertAtCursor(textarea, data.embed + '\n');
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();
            if (data.notice && data.notice.level === 'error') {
              setUploadStatus(data.notice.message, true);
            }
          });
        });
      });
      chain.then(function () {
        if (!failures) setUploadStatus('Uploaded ✓', false, true);
      });
    }

    if (attachBtn && fileInput) {
      attachBtn.addEventListener('click', function () { fileInput.click(); });
      fileInput.addEventListener('change', function () {
        uploadFiles(fileInput.files);
        fileInput.value = '';
      });
    }

    textarea.addEventListener('paste', function (e) {
      var files = e.clipboardData && e.clipboardData.files;
      if (files && files.length) {
        e.preventDefault();
        uploadFiles(files);
      }
    });

    ['dragenter', 'dragover'].forEach(function (ev) {
      textarea.addEventListener(ev, function (e) {
        if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') === -1) return;
        e.preventDefault();
        textarea.classList.add('drag-over');
      });
    });
    ['dragleave', 'dragend', 'drop'].forEach(function (ev) {
      textarea.addEventListener(ev, function () { textarea.classList.remove('drag-over'); });
    });
    textarea.addEventListener('drop', function (e) {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        e.preventDefault();
        uploadFiles(e.dataTransfer.files);
      }
    });

    textarea.addEventListener('keydown', function (e) {
      if (!(e.metaKey || e.ctrlKey)) return;
      var k = e.key.toLowerCase();
      if (k === 'b') { e.preventDefault(); applyFormat('bold', textarea); textarea.dispatchEvent(new Event('input')); }
      else if (k === 'i') { e.preventDefault(); applyFormat('italic', textarea); textarea.dispatchEvent(new Event('input')); }
      else if (k === 'k') { e.preventDefault(); applyFormat('link', textarea); textarea.dispatchEvent(new Event('input')); }
      else if (k === 's') { e.preventDefault(); form.submit(); }
      else if (k === 'p') { e.preventDefault(); if (previewBtn) previewBtn.click(); }
    });
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(ta);
    }
    return Promise.resolve();
  }

  function bindCodeCopy() {
    document.addEventListener('click', function (e) {
      var btn = e.target.closest('.code-copy');
      if (!btn) return;
      var block = btn.closest('.code-block');
      var code = block && block.querySelector('pre code');
      if (!code) return;
      copyText(code.textContent || '').then(function () {
        var old = btn.textContent;
        btn.textContent = 'copied';
        window.setTimeout(function () {
          btn.textContent = old;
        }, 1200);
      }).catch(function () {
        var old = btn.textContent;
        btn.textContent = 'copy failed';
        window.setTimeout(function () {
          btn.textContent = old;
        }, 1200);
      });
    });
  }

  function bindPrefixDatalistToggle() {
    var toggle = document.querySelector('[data-prefix-toggle]');
    var input = document.getElementById('path-prefix');
    if (!toggle || !input) return;
    toggle.addEventListener('change', function () {
      input.setAttribute('list', toggle.checked ? 'prefix-all' : 'prefix-folders');
    });
  }

  function formatLocalDatetimes() {
    var nodes = document.querySelectorAll('time.local-datetime[datetime]');
    for (var i = 0; i < nodes.length; i++) {
      var iso = nodes[i].getAttribute('datetime');
      if (!iso) continue;
      var d = new Date(iso);
      if (isNaN(d.getTime())) continue;
      try {
        nodes[i].textContent = d.toLocaleString(undefined, {
          year: 'numeric', month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit', second: '2-digit',
          hour12: false,
        });
      } catch (e) {
        nodes[i].textContent = d.toLocaleString();
      }
      nodes[i].setAttribute('title', iso);
    }
  }

  function init() {
    bindThemeToggle();
    bindSidebarToggle();
    bindTreeFilter();
    bindTreeFolders();
    bindMobileMenu();
    bindEditPage();
    bindCodeCopy();
    bindPrefixDatalistToggle();
    formatLocalDatetimes();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
