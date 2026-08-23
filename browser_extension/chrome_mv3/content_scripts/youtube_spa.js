(() => {
  "use strict";

  const MESSAGE_TYPE = "selfboss_youtube_route_change";
  const REASON = "youtube_spa_route_change";
  const FALLBACK_CHECK_MS = 2000;
  const NOTIFY_DEBOUNCE_MS = 300;

  let lastHref = window.location.href;
  let pendingNotifyId = null;

  function sendCurrentUrl() {
    chrome.runtime.sendMessage({
      type: MESSAGE_TYPE,
      url: window.location.href,
      reason: REASON,
    });
  }

  function notifyIfChanged() {
    const currentHref = window.location.href;
    if (currentHref === lastHref) {
      return;
    }
    lastHref = currentHref;
    sendCurrentUrl();
  }

  function scheduleRouteCheck() {
    if (pendingNotifyId !== null) {
      window.clearTimeout(pendingNotifyId);
    }
    pendingNotifyId = window.setTimeout(() => {
      pendingNotifyId = null;
      notifyIfChanged();
    }, NOTIFY_DEBOUNCE_MS);
  }

  function wrapHistoryMethod(methodName) {
    const originalMethod = window.history[methodName];
    if (typeof originalMethod !== "function") {
      return;
    }
    window.history[methodName] = function selfBossHistoryWrapper(...args) {
      const result = originalMethod.apply(this, args);
      scheduleRouteCheck();
      return result;
    };
  }

  wrapHistoryMethod("pushState");
  wrapHistoryMethod("replaceState");

  window.addEventListener("popstate", scheduleRouteCheck);
  window.addEventListener("hashchange", scheduleRouteCheck);
  window.setInterval(notifyIfChanged, FALLBACK_CHECK_MS);
  sendCurrentUrl();
})();
