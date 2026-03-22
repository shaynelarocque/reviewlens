/* ReviewLens AI */

// ── Logging ─────────────────────────────────────────────────────────

var _log = {
  info: function () { console.log("[RL]", ...arguments); },
  warn: function () { console.warn("[RL]", ...arguments); },
  error: function () { console.error("[RL]", ...arguments); },
};

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
  var tabs = btn.parentElement.querySelectorAll(".modal-tab");
  tabs.forEach(function (t) { t.classList.remove("active"); });
  btn.classList.add("active");

  var contents = btn.closest(".modal").querySelectorAll(".modal-tab-content");
  contents.forEach(function (c) { c.classList.remove("active"); });
  document.getElementById(tabId).classList.add("active");
}

document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") closeModal();
});

// ── Session menu ────────────────────────────────────────────────────

function toggleSessionMenu(e, btn) {
  e.preventDefault();
  e.stopPropagation();
  var wrap = btn.closest(".session-menu-wrap");
  var wasOpen = wrap.classList.contains("open");

  document.querySelectorAll(".session-menu-wrap.open").forEach(function (el) {
    el.classList.remove("open");
  });

  if (!wasOpen) wrap.classList.add("open");
}

function archiveSession(e, sessionId) {
  e.preventDefault();
  e.stopPropagation();
  _log.info("Archiving session:", sessionId);
  fetch("/api/sessions/" + sessionId, { method: "DELETE" })
    .then(function (r) {
      if (r.ok) {
        var wrap = document.querySelector(".session-menu-wrap.open");
        if (wrap) {
          var item = wrap.closest(".session-item-wrap");
          item.style.transition = "opacity 0.2s, max-height 0.2s";
          item.style.opacity = "0";
          item.style.maxHeight = item.offsetHeight + "px";
          item.style.overflow = "hidden";
          setTimeout(function () { item.style.maxHeight = "0"; }, 10);
          setTimeout(function () {
            item.remove();
            if (window.location.pathname.indexOf(sessionId) !== -1) {
              window.location.href = "/";
            }
          }, 220);
        }
      }
    });
}

document.addEventListener("click", function () {
  document.querySelectorAll(".session-menu-wrap.open").forEach(function (el) {
    el.classList.remove("open");
  });
});

document.querySelectorAll(".session-menu-wrap").forEach(function (wrap) {
  var leaveTimer = null;
  wrap.addEventListener("mouseleave", function () {
    leaveTimer = setTimeout(function () { wrap.classList.remove("open"); }, 300);
  });
  wrap.addEventListener("mouseenter", function () {
    if (leaveTimer) { clearTimeout(leaveTimer); leaveTimer = null; }
  });
});

// ── File upload drag & drop ─────────────────────────────────────────

(function () {
  var dropZone = document.getElementById("file-drop");
  var fileInput = document.getElementById("file");
  if (!dropZone || !fileInput) return;

  dropZone.addEventListener("click", function () { fileInput.click(); });

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
    if (fileInput.files.length) showFileName(fileInput.files[0].name);
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
var _sending = false;
var _toolCalls = [];

_log.info("Chat init — SESSION_ID:", typeof SESSION_ID !== "undefined" ? SESSION_ID : "(none)",
          "chatForm:", !!chatForm, "chatInput:", !!chatInput);

function scrollToBottom() {
  if (chatMessages) chatMessages.scrollTop = chatMessages.scrollHeight;
}

if (chatInput) {
  chatInput.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 150) + "px";
  });

  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      _doSend();
    }
  });
}

// The form's onsubmit calls this
function sendMessage(e) {
  if (e) e.preventDefault();
  _doSend();
}

function sendFollowUp(btn) {
  var question = btn.getAttribute("data-question");
  _log.info("Follow-up clicked:", question);
  if (chatInput) {
    chatInput.value = question;
    _doSend();
  }
}

function _doSend() {
  if (!chatInput || !SESSION_ID) {
    _log.error("Cannot send — chatInput:", !!chatInput, "SESSION_ID:", typeof SESSION_ID !== "undefined" ? SESSION_ID : "(undefined)");
    return;
  }
  var message = chatInput.value.trim();
  if (!message) {
    _log.warn("Empty message, ignoring");
    return;
  }
  if (_sending) {
    _log.warn("Already sending, ignoring");
    return;
  }

  _sending = true;
  _toolCalls = [];
  _log.info("Sending message:", message.substring(0, 80));

  chatInput.disabled = true;
  if (sendBtn) sendBtn.disabled = true;

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

  var url = "/chat/" + SESSION_ID + "/send";
  var formData = new FormData();
  formData.append("message", message);

  _log.info("POST", url);

  fetch(url, { method: "POST", body: formData })
    .then(function (resp) {
      _log.info("POST response:", resp.status, resp.statusText);
      if (!resp.ok) {
        throw new Error("Server returned " + resp.status);
      }
      listenForResponse();
    })
    .catch(function (err) {
      _log.error("POST failed:", err.name, err.message);
      removeThinking();
      appendError("Failed to send: " + err.message);
      _sending = false;
      chatInput.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
    });

  chatInput.value = "";
  chatInput.style.height = "auto";
}

function listenForResponse() {
  if (eventSource) {
    _log.info("Closing existing EventSource");
    eventSource.close();
  }

  var url = "/chat/" + SESSION_ID + "/stream";
  _log.info("Opening SSE:", url);
  eventSource = new EventSource(url);

  eventSource.addEventListener("tool", function (e) {
    var data;
    try { data = JSON.parse(e.data); } catch (_) { data = { summary: e.data, tool_name: "tool" }; }
    _toolCalls.push(data);
    _log.info("SSE tool event:", data.summary || data.tool_name);

    var thinking = document.getElementById("thinking-indicator");
    if (thinking) {
      var content = thinking.querySelector(".message-content");
      var items = "";
      _toolCalls.forEach(function (tc) {
        var name = (tc.tool_name || "tool").replace(/_/g, " ");
        name = name.charAt(0).toUpperCase() + name.slice(1);
        items += '<div class="tool-call-item">' +
          '<span class="tool-call-name">' + escapeHtml(name) + '</span>' +
          '<span class="tool-call-summary">' + escapeHtml(tc.summary || "") + '</span>' +
          '</div>';
      });
      content.innerHTML =
        '<div class="thinking-dots"><span></span><span></span><span></span></div>' +
        '<details class="tool-accordion" open>' +
        '<summary class="tool-accordion-header">' +
        '<svg class="tool-accordion-chevron" width="12" height="12" viewBox="0 0 24 24" ' +
        'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
        '<polyline points="6 9 12 15 18 9"/></svg>' +
        ' ' + _toolCalls.length + ' tool call' + (_toolCalls.length !== 1 ? 's' : '') +
        '</summary><div class="tool-accordion-body">' + items + '</div></details>';
      scrollToBottom();
    }
  });

  eventSource.addEventListener("message", function (e) {
    _log.info("SSE message event received (" + e.data.length + " chars)");
    removeThinking();
    var temp = document.createElement("div");
    temp.innerHTML = e.data;
    while (temp.firstChild) chatMessages.appendChild(temp.firstChild);
    runChartScripts();
    scrollToBottom();
    _sending = false;
    chatInput.disabled = false;
    if (sendBtn) sendBtn.disabled = false;
    chatInput.focus();
  });

  eventSource.addEventListener("done", function () {
    _log.info("SSE done");
    eventSource.close();
    eventSource = null;
    removeThinking();
    _sending = false;
    chatInput.disabled = false;
    if (sendBtn) sendBtn.disabled = false;
  });

  eventSource.addEventListener("error", function (e) {
    _log.error("SSE error event:", e);
  });

  eventSource.onerror = function (e) {
    _log.error("SSE connection error — readyState:", eventSource.readyState);
    eventSource.close();
    eventSource = null;
    removeThinking();
    _sending = false;
    chatInput.disabled = false;
    if (sendBtn) sendBtn.disabled = false;
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

function toggleChartData(id) {
  var el = document.getElementById(id);
  if (!el) return;
  var btn = el.previousElementSibling;
  if (el.style.display === "none") {
    el.style.display = "block";
    if (btn && btn.classList.contains("chart-data-toggle")) btn.textContent = "Hide data";
  } else {
    el.style.display = "none";
    if (btn && btn.classList.contains("chart-data-toggle")) btn.textContent = "View data";
  }
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
runChartScripts();

// ── Scraping status poll ────────────────────────────────────────────

(function () {
  var scrapingView = document.getElementById("scraping-view");
  if (!scrapingView || !SESSION_ID) return;

  var stepNav = document.getElementById("step-navigating");
  var stepIdx = document.getElementById("step-indexing");

  setTimeout(function () {
    if (stepNav) stepNav.classList.add("active");
  }, 3000);

  var poller = setInterval(function () {
    fetch("/api/status/" + SESSION_ID)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        _log.info("Scrape poll:", data.status);
        if (data.status === "ready") {
          if (stepNav) stepNav.classList.add("done");
          if (stepIdx) { stepIdx.classList.add("active"); stepIdx.classList.add("done"); }
          clearInterval(poller);
          setTimeout(function () { window.location.reload(); }, 600);
        } else if (data.status === "error") {
          clearInterval(poller);
          window.location.reload();
        }
      })
      .catch(function () {});
  }, 3000);

  setTimeout(function () { clearInterval(poller); }, 360000);
})();
