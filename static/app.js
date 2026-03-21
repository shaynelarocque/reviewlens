/* ReviewLens AI */

// ── Theme ───────────────────────────────────────────────────────────

(function initTheme() {
  var saved = localStorage.getItem("rl-theme");
  if (saved === "light") {
    document.documentElement.classList.add("light");
  } else if (saved === "dark") {
    document.documentElement.classList.remove("light");
  } else if (window.matchMedia("(prefers-color-scheme: light)").matches) {
    document.documentElement.classList.add("light");
  }
})();

function toggleTheme() {
  var html = document.documentElement;
  html.classList.toggle("light");
  localStorage.setItem("rl-theme", html.classList.contains("light") ? "light" : "dark");
}

// ── Modal ───────────────────────────────────────────────────────────

function openModal() {
  document.getElementById("modal-backdrop").classList.add("open");
  document.getElementById("analysis-modal").classList.add("open");
}

function closeModal() {
  document.getElementById("modal-backdrop").classList.remove("open");
  document.getElementById("analysis-modal").classList.remove("open");
}

function switchTab(btn, tabId) {
  // Deactivate all tabs
  var tabs = btn.parentElement.querySelectorAll(".modal-tab");
  tabs.forEach(function (t) { t.classList.remove("active"); });
  btn.classList.add("active");

  // Deactivate all content
  var contents = btn.closest(".modal").querySelectorAll(".modal-tab-content");
  contents.forEach(function (c) { c.classList.remove("active"); });
  document.getElementById(tabId).classList.add("active");
}

// Close modal on Escape
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") closeModal();
});

// ── File upload drag & drop ─────────────────────────────────────────

(function () {
  var dropZone = document.getElementById("file-drop");
  var fileInput = document.getElementById("file");
  if (!dropZone || !fileInput) return;

  dropZone.addEventListener("click", function () {
    fileInput.click();
  });

  dropZone.addEventListener("dragover", function (e) {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });

  dropZone.addEventListener("dragleave", function () {
    dropZone.classList.remove("drag-over");
  });

  dropZone.addEventListener("drop", function (e) {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      showFileName(e.dataTransfer.files[0].name);
    }
  });

  fileInput.addEventListener("change", function () {
    if (fileInput.files.length) {
      showFileName(fileInput.files[0].name);
    }
  });

  function showFileName(name) {
    var content = dropZone.querySelector(".file-drop-content");
    var selected = dropZone.querySelector(".file-selected");
    var nameEl = dropZone.querySelector(".file-name");
    content.style.display = "none";
    selected.style.display = "flex";
    nameEl.textContent = name;
  }
})();

function clearFile() {
  var dropZone = document.getElementById("file-drop");
  var fileInput = document.getElementById("file");
  var content = dropZone.querySelector(".file-drop-content");
  var selected = dropZone.querySelector(".file-selected");
  content.style.display = "";
  selected.style.display = "none";
  fileInput.value = "";
}

// ── HTMX loading states ─────────────────────────────────────────────

document.addEventListener("htmx:beforeRequest", function (e) {
  var form = e.detail.elt;
  var btn = form.querySelector("button[type=submit]");
  if (btn) {
    btn.disabled = true;
    var text = btn.querySelector(".btn-text");
    var spinner = btn.querySelector(".btn-spinner");
    if (text) text.style.display = "none";
    if (spinner) spinner.style.display = "inline-flex";
  }
});

document.addEventListener("htmx:afterRequest", function (e) {
  var form = e.detail.elt;
  var btn = form.querySelector("button[type=submit]");
  if (btn) {
    btn.disabled = false;
    var text = btn.querySelector(".btn-text");
    var spinner = btn.querySelector(".btn-spinner");
    if (text) text.style.display = "";
    if (spinner) spinner.style.display = "none";
  }
});

// ── Chat ────────────────────────────────────────────────────────────

var chatMessages = document.getElementById("chat-messages");
var chatInput = document.getElementById("chat-input");
var chatForm = document.getElementById("chat-form");
var sendBtn = document.getElementById("send-btn");
var eventSource = null;

function scrollToBottom() {
  if (chatMessages) {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }
}

if (chatInput) {
  chatInput.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 150) + "px";
  });

  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      chatForm.dispatchEvent(new Event("submit"));
    }
  });
}

function sendMessage(e) {
  e.preventDefault();
  if (!chatInput) return;
  var message = chatInput.value.trim();
  if (!message) return;

  chatInput.disabled = true;
  sendBtn.disabled = true;

  // Remove old follow-ups
  var old = chatMessages.querySelectorAll(".follow-ups");
  old.forEach(function (el) { el.remove(); });

  // User bubble
  var userDiv = document.createElement("div");
  userDiv.className = "message user-message";
  userDiv.innerHTML = '<div class="message-content"><p>' + escapeHtml(message) + "</p></div>";
  chatMessages.appendChild(userDiv);

  // Thinking
  var thinkingDiv = document.createElement("div");
  thinkingDiv.id = "thinking-indicator";
  thinkingDiv.className = "message assistant-message thinking";
  thinkingDiv.innerHTML =
    '<div class="message-content">' +
    '<div class="thinking-dots"><span></span><span></span><span></span></div>' +
    "</div>";
  chatMessages.appendChild(thinkingDiv);
  scrollToBottom();

  var formData = new FormData();
  formData.append("message", message);

  fetch("/chat/" + SESSION_ID + "/send", { method: "POST", body: formData })
    .then(function () { listenForResponse(); })
    .catch(function (err) {
      removeThinking();
      appendError("Failed to send: " + err.message);
      chatInput.disabled = false;
      sendBtn.disabled = false;
    });

  chatInput.value = "";
  chatInput.style.height = "auto";
}

function listenForResponse() {
  if (eventSource) eventSource.close();

  eventSource = new EventSource("/chat/" + SESSION_ID + "/stream");

  eventSource.addEventListener("tool", function (e) {
    var thinking = document.getElementById("thinking-indicator");
    if (thinking) {
      var content = thinking.querySelector(".message-content");
      content.innerHTML =
        '<div class="thinking-dots"><span></span><span></span><span></span></div>' +
        '<div class="tool-activity">' + escapeHtml(e.data) + "</div>";
      scrollToBottom();
    }
  });

  eventSource.addEventListener("message", function (e) {
    removeThinking();
    var temp = document.createElement("div");
    temp.innerHTML = e.data;
    while (temp.firstChild) chatMessages.appendChild(temp.firstChild);
    runChartScripts();
    scrollToBottom();
    chatInput.disabled = false;
    sendBtn.disabled = false;
    chatInput.focus();
  });

  eventSource.addEventListener("done", function () {
    eventSource.close();
    eventSource = null;
    removeThinking();
    chatInput.disabled = false;
    sendBtn.disabled = false;
  });

  eventSource.onerror = function () {
    eventSource.close();
    eventSource = null;
    removeThinking();
    chatInput.disabled = false;
    sendBtn.disabled = false;
  };
}

function removeThinking() {
  var el = document.getElementById("thinking-indicator");
  if (el) el.remove();
}

function appendError(msg) {
  var div = document.createElement("div");
  div.className = "message assistant-message error";
  div.innerHTML = '<div class="message-content"><p>' + escapeHtml(msg) + "</p></div>";
  chatMessages.appendChild(div);
  scrollToBottom();
}

function sendFollowUp(btn) {
  var question = btn.getAttribute("data-question");
  if (chatInput) {
    chatInput.value = question;
    chatForm.dispatchEvent(new Event("submit"));
  }
}

// ── Chart rendering ─────────────────────────────────────────────────

var CHART_COLORS = [
  "rgba(45, 212, 191, 0.75)",
  "rgba(245, 158, 11, 0.75)",
  "rgba(94, 234, 212, 0.65)",
  "rgba(248, 113, 113, 0.7)",
  "rgba(129, 140, 248, 0.7)",
  "rgba(52, 211, 153, 0.7)",
  "rgba(251, 191, 36, 0.7)",
];

var CHART_BORDERS = [
  "rgba(45, 212, 191, 1)",
  "rgba(245, 158, 11, 1)",
  "rgba(94, 234, 212, 1)",
  "rgba(248, 113, 113, 1)",
  "rgba(129, 140, 248, 1)",
  "rgba(52, 211, 153, 1)",
  "rgba(251, 191, 36, 1)",
];

function getChartTextColor() {
  return document.documentElement.classList.contains("light") ? "#374151" : "#b0bdd0";
}

function getChartGridColor() {
  return document.documentElement.classList.contains("light")
    ? "rgba(0, 0, 0, 0.06)"
    : "rgba(107, 125, 153, 0.08)";
}

function renderChart(canvasId, config) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var textColor = getChartTextColor();
  var gridColor = getChartGridColor();

  var datasets = config.data.datasets.map(function (ds, i) {
    var isPie = config.type === "pie" || config.type === "doughnut";
    var colors = isPie ? CHART_COLORS.slice(0, ds.data.length) : [CHART_COLORS[i % CHART_COLORS.length]];
    var borders = isPie ? CHART_BORDERS.slice(0, ds.data.length) : [CHART_BORDERS[i % CHART_BORDERS.length]];

    return {
      label: ds.label,
      data: ds.data,
      backgroundColor: colors.length === 1 ? colors[0] : colors,
      borderColor: borders.length === 1 ? borders[0] : borders,
      borderWidth: config.type === "line" ? 2 : 1,
      tension: 0.3,
      fill: config.type === "line",
    };
  });

  new Chart(canvas, {
    type: config.type,
    data: { labels: config.data.labels, datasets: datasets },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        title: {
          display: true,
          text: config.title,
          font: { size: 13, weight: "500", family: "'DM Sans', sans-serif" },
          color: textColor,
          padding: { bottom: 12 },
        },
        legend: {
          labels: { color: textColor, font: { family: "'DM Sans', sans-serif", size: 11 } },
        },
      },
      scales:
        config.type !== "pie" && config.type !== "doughnut"
          ? {
              x: {
                ticks: { color: textColor, font: { family: "'IBM Plex Mono', monospace", size: 10 } },
                grid: { color: gridColor },
              },
              y: {
                ticks: { color: textColor, font: { family: "'IBM Plex Mono', monospace", size: 10 } },
                grid: { color: gridColor },
                beginAtZero: true,
              },
            }
          : undefined,
    },
  });
}

function runChartScripts() {
  if (!chatMessages) return;
  var scripts = chatMessages.querySelectorAll("script");
  scripts.forEach(function (script) {
    if (script.textContent.indexOf("renderChart") !== -1 && !script.dataset.executed) {
      script.dataset.executed = "true";
      eval(script.textContent);
    }
  });
}

// ── Utilities ────────────────────────────────────────────────────────

function escapeHtml(str) {
  var div = document.createElement("div");
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

if (chatMessages) {
  var observer = new MutationObserver(scrollToBottom);
  observer.observe(chatMessages, { childList: true, subtree: true });
}

scrollToBottom();
