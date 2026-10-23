// Offline presentation runtime. It reads only the document it is embedded in:
// there is no network access, no module loading and no service worker.
(function () {
  "use strict";

  var data = JSON.parse(document.getElementById("deck-data").textContent);
  var viewport = document.querySelector(".viewport");
  var scalers = Array.prototype.slice.call(document.querySelectorAll(".scaler"));
  var pages = Array.prototype.slice.call(document.querySelectorAll('.viewport [data-kind="index"], .viewport [data-kind="slide"]'));
  var sequence = pages.map(function (page) { return page.dataset.pageId; });
  var notesPanel = document.getElementById("notes");
  var notesBody = document.getElementById("notes-body");
  var notesToggle = document.getElementById("notes-toggle");
  var position = document.getElementById("position");
  var current = sequence[0];
  var opener = null;

  function byPage(pageId) {
    return document.querySelector('[data-page-id="' + CSS.escape(pageId) + '"]');
  }

  function dialogOf(pageId) {
    var page = byPage(pageId);
    return page ? page.closest("dialog") : null;
  }

  function fit() {
    scalers.forEach(function (scaler) {
      var frame = scaler.parentElement;
      var width = scaler.offsetWidth;
      var height = scaler.offsetHeight;
      if (!width || !height) { return; }
      var inDialog = scaler.closest("dialog") !== null;
      var available = inDialog
        ? { w: window.innerWidth * 0.94, h: window.innerHeight * 0.94 }
        : { w: viewport.clientWidth - 16, h: viewport.clientHeight - 16 };
      var scale = Math.min(available.w / width, available.h / height);
      scaler.style.transform = "scale(" + scale + ")";
      frame.style.width = Math.round(width * scale) + "px";
      frame.style.height = Math.round(height * scale) + "px";
    });
  }

  function showNotes(pageId) {
    var text = data.notes[pageId] || "";
    notesBody.textContent = text;
    notesPanel.hidden = !(notesToggle.getAttribute("aria-pressed") === "true");
  }

  function activate(pageId) {
    var page = byPage(pageId);
    if (!page) { return; }
    var parent = page.parentElement;
    Array.prototype.forEach.call(parent.children, function (child) {
      if (child.classList.contains("page")) { child.hidden = child !== page; }
    });
    current = pageId;
    var dialog = dialogOf(pageId);
    var scope = dialog
      ? Array.prototype.map.call(dialog.querySelectorAll(".page"), function (item) { return item.dataset.pageId; })
      : sequence;
    var index = scope.indexOf(pageId);
    if (index >= 0 && position) {
      position.textContent = data.labels.position
        .replace("{current}", String(index + 1))
        .replace("{total}", String(scope.length));
    }
    showNotes(pageId);
    fit();
  }

  function closeDialog(dialog) {
    if (dialog && dialog.open) { dialog.close(); }
  }

  function go(pageId, trigger) {
    var dialog = dialogOf(pageId);
    var active = document.querySelector("dialog.support[open]");
    if (dialog) {
      if (active && active !== dialog) { closeDialog(active); }
      if (!dialog.open) {
        opener = trigger || document.activeElement;
        dialog.showModal();
      }
      activate(pageId);
      return;
    }
    if (active) { closeDialog(active); }
    activate(pageId);
  }

  function step(delta) {
    var dialog = document.querySelector("dialog.support[open]");
    var scope = dialog
      ? Array.prototype.map.call(dialog.querySelectorAll(".page"), function (page) { return page.dataset.pageId; })
      : sequence;
    var index = scope.indexOf(current);
    if (index < 0) { return; }
    var next = scope[index + delta];
    if (next) { go(next); }
  }

  document.addEventListener("click", function (event) {
    var control = event.target.closest("[data-action-id]");
    if (!control || control.disabled) { return; }
    if (control.tagName === "A") { return; }
    event.preventDefault();
    var target = control.dataset.target;
    if (target) { go(target, control); }
  });

  document.addEventListener("keydown", function (event) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) { return; }
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
    if (typing) { return; }
    if (event.key === "Escape") {
      var dialog = document.querySelector("dialog.support[open]");
      if (dialog) {
        event.preventDefault();
        closeDialog(dialog);
      }
      return;
    }
    if (event.key === " " && event.target.closest("button, a, [role='button']")) { return; }
    if (event.key === "ArrowRight" || event.key === "PageDown" || event.key === " ") {
      event.preventDefault();
      step(1);
    } else if (event.key === "ArrowLeft" || event.key === "PageUp") {
      event.preventDefault();
      step(-1);
    } else if (event.key === "Home" && !document.querySelector("dialog.support[open]")) {
      event.preventDefault();
      go(sequence[0]);
    }
  });

  Array.prototype.forEach.call(document.querySelectorAll("dialog.support"), function (dialog) {
    dialog.addEventListener("close", function () {
      var origin = dialog.dataset.origin;
      var restore = origin && dialogOf(current) === dialog;
      if (restore) { activate(origin); }
      if (restore && opener && document.contains(opener)) { opener.focus({ preventScroll: true }); }
      opener = null;
    });
  });

  notesToggle.addEventListener("click", function () {
    var pressed = notesToggle.getAttribute("aria-pressed") === "true";
    notesToggle.setAttribute("aria-pressed", pressed ? "false" : "true");
    showNotes(current);
  });

  window.addEventListener("resize", fit);
  document.fonts.ready.then(fit);
  activate(sequence[0]);
  document.documentElement.dataset.runtimeReady = "1";
})();
