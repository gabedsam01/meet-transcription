// content.js
//
// Injected into https://meet.google.com/* pages. Its only job is to notice when
// the user LEAVES a call (navigates away, the URL changes off the meeting, or
// the "call ended" screen appears) and tell the service worker to stop the
// recording so the upload happens automatically.
//
// It deliberately does NOT capture audio (that is the offscreen document's job)
// and it never touches secrets.

(() => {
  "use strict";

  // A Meet meeting URL looks like https://meet.google.com/abc-defg-hij
  // The lobby / landing pages (/, /new, /_meet, /landing) are NOT live calls.
  const MEETING_PATH = /^\/[a-z]{3}-[a-z]{4}-[a-z]{3}$/i;

  let lastPath = location.pathname;
  let notified = false;

  /** @returns {boolean} true while the URL looks like an active meeting. */
  function inMeeting() {
    return MEETING_PATH.test(location.pathname);
  }

  /** Tell the background worker the call ended (once per departure). */
  function notifyCallEnded(reason) {
    if (notified) {
      return;
    }
    notified = true;
    try {
      chrome.runtime.sendMessage({
        type: "content-call-ended",
        reason,
        url: location.href,
      });
    } catch {
      /* extension context may be invalidated on navigation; ignore */
    }
  }

  /** Re-arm notification when the user (re)enters a meeting. */
  function resetIfBackInMeeting() {
    if (inMeeting()) {
      notified = false;
    }
  }

  // 1) Detect SPA URL changes. Meet is a single-page app, so we patch history
  //    and also poll as a safety net.
  function onUrlMaybeChanged() {
    if (location.pathname === lastPath) {
      return;
    }
    const wasInMeeting = MEETING_PATH.test(lastPath);
    lastPath = location.pathname;
    resetIfBackInMeeting();
    if (wasInMeeting && !inMeeting()) {
      notifyCallEnded("url-change");
    }
  }

  for (const method of ["pushState", "replaceState"]) {
    const original = history[method];
    history[method] = function patched(...args) {
      const result = original.apply(this, args);
      onUrlMaybeChanged();
      return result;
    };
  }
  window.addEventListener("popstate", onUrlMaybeChanged);
  window.addEventListener("hashchange", onUrlMaybeChanged);

  // 2) Detect the "you left the meeting / call ended" screen via DOM text.
  //    Meet's markup is volatile, so we match resilient text rather than
  //    brittle class names.
  const ENDED_TEXT = /(você saiu|call ended|you left|reunião encerrada|left the meeting)/i;
  const observer = new MutationObserver(() => {
    if (!inMeeting()) {
      return; // URL handler already covers off-meeting states
    }
    const body = document.body ? document.body.innerText || "" : "";
    if (ENDED_TEXT.test(body)) {
      notifyCallEnded("call-ended-dom");
    }
  });
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // 3) Stop on tab/window close as a last resort.
  window.addEventListener("beforeunload", () => notifyCallEnded("unload"));

  // 4) Polling safety net for URL changes the history hooks might miss.
  setInterval(onUrlMaybeChanged, 2000);
})();
