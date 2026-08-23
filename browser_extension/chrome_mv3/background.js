const NATIVE_HOST_NAME = "com.selfboss.native_host";
const STATUS_ALARM_BASE_NAME = "selfboss-status-poll";
const STATUS_POLL_MINUTES = 0.5;
const EVALUATION_DEBOUNCE_MS = 2000;
const BROWSER_BLOCKING_MODES = new Set(["real_hosts_blocking", "full_enforcement"]);
const BROWSER_ID = "chrome";
const YOUTUBE_ROUTE_CHANGE_MESSAGE = "selfboss_youtube_route_change";
const OPEN_SELF_BOSS_MESSAGE = "selfboss_open_desktop";
const OPEN_SELF_BOSS_TIMEOUT_MS = 5000;
const SELF_BOSS_DNR_RULE_ID_START = 10000;
const SELF_BOSS_DNR_RULE_LIMIT = 100;
const IS_INCOGNITO_CONTEXT = Boolean(
  chrome.extension && chrome.extension.inIncognitoContext,
);
const EXTENSION_CONTEXT = IS_INCOGNITO_CONTEXT ? "incognito" : "regular";
const STATUS_ALARM_NAME = `${STATUS_ALARM_BASE_NAME}-${EXTENSION_CONTEXT}`;

let lastNativeStatus = {
  connected: false,
  hello: null,
  selfBossStatus: null,
  blockedDomainsSnapshot: null,
  lastUrlEvaluation: null,
  error: null,
  browser_integration: null,
};
let nativePort = null;
const evaluatedTabUrls = new Map();
const pendingUrlEvaluations = [];
let activeDnrRuleIds = [];
let dnrStatus = {
  supported: "unknown",
  session_rule_count: 0,
  last_update_status: "unknown",
  last_error: "",
};
let lastStatusSignature = null;
let hasReceivedStatus = false;
let pendingOpenSelfBossResponse = null;
let youtubeSpaContentScriptSeen = false;
let browserIntegrationStatus = {
  browser: BROWSER_ID,
  context: EXTENSION_CONTEXT,
  extension_connected: false,
  incognito_allowed: "unknown",
  last_heartbeat_at: null,
  last_heartbeat_age_seconds: null,
  trusted_browser: true,
  browser_high_safety: "partial",
};

function setNativeStatus(update, options = {}) {
  lastNativeStatus = {
    ...lastNativeStatus,
    ...update,
    browser_integration: currentBrowserIntegrationStatus(),
  };
  if (options.log !== false) {
    console.log(`SelfBoss native host status [${EXTENSION_CONTEXT}]`, lastNativeStatus);
  }
}

function currentBrowserIntegrationStatus() {
  if (!browserIntegrationStatus.last_heartbeat_at) {
    return { ...browserIntegrationStatus };
  }
  return {
    ...browserIntegrationStatus,
    last_heartbeat_age_seconds: Math.max(
      0,
      Math.floor((Date.now() - Date.parse(browserIntegrationStatus.last_heartbeat_at)) / 1000),
    ),
  };
}

function markNativeHeartbeat() {
  browserIntegrationStatus = {
    ...browserIntegrationStatus,
    extension_connected: true,
    last_heartbeat_at: new Date().toISOString(),
  };
}

function markNativeDisconnected() {
  browserIntegrationStatus = {
    ...browserIntegrationStatus,
    extension_connected: false,
  };
}

function refreshIncognitoAllowed(callback) {
  const extensionApi = chrome.extension;
  if (
    !extensionApi ||
    typeof extensionApi.isAllowedIncognitoAccess !== "function"
  ) {
    browserIntegrationStatus = {
      ...browserIntegrationStatus,
      incognito_allowed: "unknown",
    };
    callback("unknown");
    return;
  }

  extensionApi.isAllowedIncognitoAccess((isAllowed) => {
    const runtimeError = chrome.runtime.lastError;
    const incognitoAllowed = runtimeError ? "unknown" : Boolean(isAllowed);
    browserIntegrationStatus = {
      ...browserIntegrationStatus,
      incognito_allowed: incognitoAllowed,
    };
    callback(incognitoAllowed);
  });
}

function heartbeatBrowserBlockingFromStatus(status) {
  if (!status || status.ok !== true) {
    return "not_implemented";
  }
  return canBrowserBlockingBeActive(status) ? "active" : "evaluation_only";
}

function sendBrowserHeartbeat(status) {
  if (!nativePort) {
    return;
  }
  refreshIncognitoAllowed((incognitoAllowed) => {
    if (!nativePort) {
      return;
    }
    nativePort.postMessage({
      type: "browser_heartbeat",
      browser: BROWSER_ID,
      context: EXTENSION_CONTEXT,
      incognito_allowed: incognitoAllowed,
      browser_blocking: heartbeatBrowserBlockingFromStatus(status),
      browser_blocking_available: canBrowserBlockingBeActive(status),
      dnr_supported: dnrStatus.supported,
      dnr_session_rule_count: dnrStatus.session_rule_count,
      dnr_last_update_status: dnrStatus.last_update_status,
      dnr_last_error: dnrStatus.last_error,
      youtube_spa_content_script_seen: youtubeSpaContentScriptSeen,
      extension_version:
        chrome.runtime && chrome.runtime.getManifest
          ? chrome.runtime.getManifest().version || ""
          : "",
    });
  });
}

function setupStatusAlarm() {
  chrome.alarms.create(STATUS_ALARM_NAME, {
    periodInMinutes: STATUS_POLL_MINUTES,
  });
}

function connectToNativeHost() {
  if (nativePort) {
    return true;
  }

  try {
    nativePort = chrome.runtime.connectNative(NATIVE_HOST_NAME);
  } catch (error) {
    markNativeDisconnected();
    setNativeStatus({
      connected: false,
      hello: null,
      selfBossStatus: null,
      lastUrlEvaluation: null,
      error: String(error),
    });
    return false;
  }

  nativePort.onMessage.addListener((message) => {
    markNativeHeartbeat();
    if (
      message &&
      message.native_host === "connected" &&
      message.app === "SelfBoss" &&
      !Object.prototype.hasOwnProperty.call(message, "browser_blocking")
    ) {
      setNativeStatus({
        connected: Boolean(message.ok),
        hello: message,
        error: null,
      }, { log: false });
      nativePort.postMessage({ type: "get_status" });
      return;
    }

    if (message && Object.prototype.hasOwnProperty.call(message, "decision")) {
      handleUrlEvaluation(message);
      return;
    }

    if (message && Object.prototype.hasOwnProperty.call(message, "domains")) {
      handleBlockedDomainsSnapshot(message);
      return;
    }

    if (message && Object.prototype.hasOwnProperty.call(message, "heartbeat_saved")) {
      handleBrowserHeartbeatResponse(message);
      return;
    }

    if (message && message.action === "open_selfboss") {
      handleOpenSelfBossResponse(message);
      return;
    }

    handleSelfBossStatus(message);
  });

  nativePort.onDisconnect.addListener(() => {
    const runtimeError = chrome.runtime.lastError;
    nativePort = null;
    pendingUrlEvaluations.length = 0;
    finishOpenSelfBossRequest({
      ok: false,
      action: "open_selfboss",
      reason: "Native host disconnected.",
    });
    markNativeDisconnected();
    clearDnrSessionRules("native_disconnect");
    setNativeStatus({
      connected: false,
      error: runtimeError ? runtimeError.message : null,
    });
  });

  nativePort.postMessage({ type: "hello" });
  return true;
}

function requestStatus() {
  if (!connectToNativeHost()) {
    return;
  }
  nativePort.postMessage({ type: "get_status" });
}

function requestBlockedDomainsSnapshot() {
  if (!connectToNativeHost()) {
    clearDnrSessionRules("native_unavailable");
    return;
  }
  nativePort.postMessage({
    type: "get_blocked_domains_snapshot",
    browser: BROWSER_ID,
    context: EXTENSION_CONTEXT,
  });
}

function normalizeSignatureValue(value) {
  if (value === undefined || value === null) {
    return "unknown";
  }
  return String(value);
}

function buildStatusSignature(status) {
  if (!status || !status.ok) {
    return "unavailable";
  }
  return [
    "mode",
    normalizeSignatureValue(status.enforcement_mode),
    "level",
    normalizeSignatureValue(status.access_level),
    "day",
    normalizeSignatureValue(status.day_active),
    "high",
    normalizeSignatureValue(status.high_active),
    "safe",
    normalizeSignatureValue(status.safe_mode_active),
    "recovery",
    normalizeSignatureValue(status.recovery_mode_active),
    "browser",
    normalizeSignatureValue(status.browser_blocking),
  ].join(":");
}

function compactStatusForLog(status) {
  return {
    connected: Boolean(status && status.ok),
    enforcement_mode: status ? status.enforcement_mode : "unknown",
    access_level: status ? status.access_level : "unknown",
    day_active: status ? status.day_active : "unknown",
    high_active: status ? status.high_active : "unknown",
    safe_mode_active: status ? status.safe_mode_active : "unknown",
    recovery_mode_active: status ? status.recovery_mode_active : "unknown",
    browser_blocking: status ? status.browser_blocking : "unknown",
    browser_integration: currentBrowserIntegrationStatus(),
    status_error: status ? status.status_error : null,
  };
}

function handleSelfBossStatus(message) {
  const status = message || { ok: false };
  const signature = buildStatusSignature(status);
  const shouldSweep = !hasReceivedStatus || signature !== lastStatusSignature;
  const sweepReason = hasReceivedStatus ? "state_change_sweep" : "initial_sweep";
  const shouldRunPeriodicSweep =
    !shouldSweep && canBrowserBlockingBeActive(status);

  setNativeStatus({
    connected: Boolean(status.ok),
    selfBossStatus: status,
    error: status.status_error ? status.status_error : null,
  }, { log: false });
  sendBrowserHeartbeat(status);
  requestBlockedDomainsSnapshot();

  if (shouldSweep) {
    console.log(
      `SelfBoss native host status [${EXTENSION_CONTEXT}]`,
      compactStatusForLog(status),
    );
    lastStatusSignature = signature;
    sweepOpenTabs(sweepReason);
  } else if (shouldRunPeriodicSweep) {
    sweepOpenTabs("periodic_enforcement_sweep");
  }
  hasReceivedStatus = true;
}

function handleBlockedDomainsSnapshot(message) {
  setNativeStatus({
    connected: Boolean(message.ok),
    blockedDomainsSnapshot: message,
    error: message.status_error || null,
  }, { log: false });
  updateDnrSessionRulesFromSnapshot(message);
}

function handleBrowserHeartbeatResponse(message) {
  setNativeStatus({
    connected: Boolean(message.ok),
    error: message.ok ? null : message.error,
  }, { log: false });
  if (message.heartbeat_saved !== true) {
    console.log(`SelfBoss browser heartbeat not saved [${EXTENSION_CONTEXT}]`, {
      error: message.error || "unknown",
    });
  }
}

function handleOpenSelfBossResponse(message) {
  const response = {
    ok: Boolean(message.ok),
    action: "open_selfboss",
    reason: typeof message.reason === "string" && message.reason.trim()
      ? message.reason.trim()
      : "LoopGuard open request finished.",
  };
  finishOpenSelfBossRequest(response);
  console.log(`SelfBoss open desktop request [${EXTENSION_CONTEXT}]`, response);
}

function finishOpenSelfBossRequest(response) {
  if (!pendingOpenSelfBossResponse) {
    return;
  }
  clearTimeout(pendingOpenSelfBossResponse.timeoutId);
  pendingOpenSelfBossResponse.sendResponse(response);
  pendingOpenSelfBossResponse = null;
}

function requestOpenSelfBoss(sendResponse) {
  if (pendingOpenSelfBossResponse) {
    sendResponse({
      ok: false,
      action: "open_selfboss",
      reason: "Open LoopGuard request is already pending.",
    });
    return false;
  }
  if (!connectToNativeHost()) {
    sendResponse({
      ok: false,
      action: "open_selfboss",
      reason: "Native host is unavailable.",
    });
    return false;
  }

  const timeoutId = setTimeout(() => {
    if (!pendingOpenSelfBossResponse) {
      return;
    }
    pendingOpenSelfBossResponse.sendResponse({
      ok: false,
      action: "open_selfboss",
      reason: "Open LoopGuard request timed out.",
    });
    pendingOpenSelfBossResponse = null;
  }, OPEN_SELF_BOSS_TIMEOUT_MS);

  pendingOpenSelfBossResponse = { sendResponse, timeoutId };
  nativePort.postMessage({ type: "request_open_selfboss" });
  return true;
}

function truthyStatusValue(value) {
  return value === true || value === "true";
}

function canBrowserBlockingBeActive(status) {
  if (!status || status.ok !== true) {
    return false;
  }
  return (
    BROWSER_BLOCKING_MODES.has(status.enforcement_mode) &&
    truthyStatusValue(status.day_active) &&
    !truthyStatusValue(status.safe_mode_active) &&
    !truthyStatusValue(status.recovery_mode_active)
  );
}

function validDnrDomain(domain) {
  if (typeof domain !== "string") {
    return false;
  }
  const cleaned = domain.trim().toLowerCase();
  if (!cleaned || cleaned.length > 253) {
    return false;
  }
  if (cleaned.includes("/") || cleaned.includes(":") || cleaned.includes("*")) {
    return false;
  }
  const labels = cleaned.split(".");
  return labels.length >= 2 && labels.every(Boolean);
}

function normalizedSnapshotDomains(snapshot) {
  const domains = Array.isArray(snapshot && snapshot.domains) ? snapshot.domains : [];
  const seen = new Set();
  const normalized = [];
  for (const domain of domains) {
    if (!validDnrDomain(domain)) {
      continue;
    }
    const cleaned = domain.trim().toLowerCase();
    if (seen.has(cleaned)) {
      continue;
    }
    seen.add(cleaned);
    normalized.push(cleaned);
    if (normalized.length >= SELF_BOSS_DNR_RULE_LIMIT) {
      break;
    }
  }
  return normalized;
}

function canonicalAllowedUrl(value) {
  if (typeof value !== "string") {
    return "";
  }
  try {
    const parsed = new URL(value.trim());
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "";
    }
    parsed.protocol = parsed.protocol.toLowerCase();
    parsed.hostname = parsed.hostname.toLowerCase().replace(/\.$/, "");
    parsed.hash = "";
    if (
      (parsed.protocol === "http:" && parsed.port === "80") ||
      (parsed.protocol === "https:" && parsed.port === "443")
    ) {
      parsed.port = "";
    }
    if (!parsed.pathname) {
      parsed.pathname = "/";
    }
    return parsed.toString();
  } catch (_error) {
    return "";
  }
}

function normalizedSnapshotAllowedUrls(snapshot) {
  const urls = Array.isArray(snapshot && snapshot.allowed_urls)
    ? snapshot.allowed_urls
    : [];
  const seen = new Set();
  const normalized = [];
  for (const url of urls) {
    const canonical = canonicalAllowedUrl(url);
    if (!canonical || seen.has(canonical)) {
      continue;
    }
    seen.add(canonical);
    normalized.push(canonical);
    if (normalized.length >= SELF_BOSS_DNR_RULE_LIMIT) {
      break;
    }
  }
  return normalized;
}

function selfBossDnrRuleIds() {
  return Array.from(
    { length: SELF_BOSS_DNR_RULE_LIMIT },
    (_value, index) => SELF_BOSS_DNR_RULE_ID_START + index,
  );
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function buildDnrSessionRules(domains, allowedUrls = []) {
  const rules = [];
  for (const url of allowedUrls) {
    if (rules.length >= SELF_BOSS_DNR_RULE_LIMIT) {
      break;
    }
    rules.push({
      id: SELF_BOSS_DNR_RULE_ID_START + rules.length,
      priority: 2,
      action: { type: "allow" },
      condition: {
        regexFilter: `^${escapeRegex(url)}$`,
        resourceTypes: ["main_frame"],
      },
    });
  }
  for (const domain of domains) {
    if (rules.length >= SELF_BOSS_DNR_RULE_LIMIT) {
      break;
    }
    rules.push({
      id: SELF_BOSS_DNR_RULE_ID_START + rules.length,
      priority: 1,
      action: { type: "block" },
      condition: {
        requestDomains: [domain],
        resourceTypes: ["main_frame"],
      },
    });
  }
  return rules;
}

function dnrApiAvailable() {
  return (
    chrome.declarativeNetRequest &&
    typeof chrome.declarativeNetRequest.updateSessionRules === "function"
  );
}

function setDnrStatus(update) {
  dnrStatus = {
    ...dnrStatus,
    ...update,
  };
}

function updateDnrSessionRulesFromSnapshot(snapshot) {
  if (!dnrApiAvailable()) {
    setDnrStatus({
      supported: false,
      session_rule_count: 0,
      last_update_status: "unavailable",
      last_error: "declarativeNetRequest API unavailable",
    });
    console.log(`SelfBoss DNR unavailable [${EXTENSION_CONTEXT}]`, {
      reason: "declarativeNetRequest API unavailable",
    });
    return;
  }
  if (!snapshot || snapshot.ok !== true || snapshot.browser_blocking !== "active") {
    clearDnrSessionRules("snapshot_inactive");
    return;
  }

  const domains = normalizedSnapshotDomains(snapshot);
  const allowedUrls = normalizedSnapshotAllowedUrls(snapshot);
  if (domains.length === 0 && allowedUrls.length === 0) {
    clearDnrSessionRules("snapshot_empty");
    return;
  }

  const addRules = buildDnrSessionRules(domains, allowedUrls);
  chrome.declarativeNetRequest.updateSessionRules(
    {
      removeRuleIds: selfBossDnrRuleIds(),
      addRules,
    },
    () => {
      const runtimeError = chrome.runtime.lastError;
      if (runtimeError) {
        setDnrStatus({
          supported: true,
          session_rule_count: activeDnrRuleIds.length,
          last_update_status: "error",
          last_error: runtimeError.message || "DNR update failed",
        });
        console.log(`SelfBoss DNR update failed [${EXTENSION_CONTEXT}]`, {
          error: runtimeError.message,
          requested_domains: domains.length,
        });
        setNativeStatus({ error: runtimeError.message }, { log: false });
        return;
      }
      activeDnrRuleIds = addRules.map((rule) => rule.id);
      setDnrStatus({
        supported: true,
        session_rule_count: activeDnrRuleIds.length,
        last_update_status:
          activeDnrRuleIds.length > 0 ? "active" : "supported_no_rules",
        last_error: "",
      });
      console.log(`SelfBoss DNR session rules updated [${EXTENSION_CONTEXT}]`, {
        domains: domains.length,
        allowed_urls: allowedUrls.length,
        browser_blocking: snapshot.browser_blocking,
      });
    },
  );
}

function clearDnrSessionRules(reason) {
  if (!dnrApiAvailable()) {
    setDnrStatus({
      supported: false,
      session_rule_count: 0,
      last_update_status: "unavailable",
      last_error: "declarativeNetRequest API unavailable",
    });
    return;
  }
  chrome.declarativeNetRequest.updateSessionRules(
    {
      removeRuleIds: selfBossDnrRuleIds(),
    },
    () => {
      const runtimeError = chrome.runtime.lastError;
      if (runtimeError) {
        setDnrStatus({
          supported: true,
          session_rule_count: activeDnrRuleIds.length,
          last_update_status: "error",
          last_error: runtimeError.message || "DNR clear failed",
        });
        console.log(`SelfBoss DNR clear failed [${EXTENSION_CONTEXT}]`, {
          reason,
          error: runtimeError.message,
        });
        setNativeStatus({ error: runtimeError.message }, { log: false });
        return;
      }
      if (activeDnrRuleIds.length > 0) {
        console.log(`SelfBoss DNR session rules cleared [${EXTENSION_CONTEXT}]`, {
          reason,
        });
      }
      activeDnrRuleIds = [];
      setDnrStatus({
        supported: true,
        session_rule_count: 0,
        last_update_status: "cleared",
        last_error: "",
      });
    },
  );
}

function isEvaluatableUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch (_error) {
    return false;
  }
}

function isYouTubeUrl(url) {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    return (
      (parsed.protocol === "http:" || parsed.protocol === "https:") &&
      (host === "youtube.com" || host.endsWith(".youtube.com"))
    );
  } catch (_error) {
    return false;
  }
}

function evaluationContextFromUrl(tabId, url, reason, incognito) {
  try {
    const parsed = new URL(url);
    return {
      tabId,
      url,
      reason,
      host: parsed.hostname.toLowerCase(),
      incognito: Boolean(incognito),
      context: EXTENSION_CONTEXT,
    };
  } catch (_error) {
    return {
      tabId,
      url,
      reason,
      host: "",
      incognito: Boolean(incognito),
      context: EXTENSION_CONTEXT,
    };
  }
}

function handleUrlEvaluation(message) {
  const context = pendingUrlEvaluations.shift() || {};
  setNativeStatus({
    connected: Boolean(message.ok),
    lastUrlEvaluation: message,
    error: message.ok ? null : message.reason,
  }, { log: false });

  console.log(`SelfBoss URL evaluation [${EXTENSION_CONTEXT}]`, {
    decision: message.decision,
    reason: message.reason,
    access_level: message.access_level,
    enforcement_mode: message.enforcement_mode,
    browser_blocking: message.browser_blocking,
    url_family: message.url_family,
    path_kind: message.path_kind,
    matched_scope: message.matched_scope,
    reason_code: message.reason_code,
    host: context.host || "unknown",
    incognito: Boolean(context.incognito),
    context: context.context || EXTENSION_CONTEXT,
    sweep_reason: context.reason || "unknown",
  });

  if (
    message.ok === true &&
    message.decision === "block" &&
    message.browser_blocking === "active" &&
    context.tabId !== undefined
  ) {
    redirectBlockedTab(context, message);
  }
}

function buildBlockedPageUrl(context, message) {
  const params = new URLSearchParams();
  if (context.host) {
    params.set("host", context.host);
  }
  if (message.access_level) {
    params.set("level", message.access_level);
  }
  if (message.reason) {
    params.set("reason", message.reason);
  }
  if (message.url_family) {
    params.set("url_family", message.url_family);
  }
  if (message.path_kind) {
    params.set("path_kind", message.path_kind);
  }
  return chrome.runtime.getURL(`blocked.html?${params.toString()}`);
}

function redirectBlockedTab(context, message) {
  const blockedUrl = buildBlockedPageUrl(context, message);
  chrome.tabs.get(context.tabId, (tab) => {
    const runtimeError = chrome.runtime.lastError;
    if (runtimeError) {
      logRedirectSkipped(context, runtimeError.message);
      setNativeStatus({ error: runtimeError.message }, { log: false });
      return;
    }

    if (!tab || !tab.url) {
      logRedirectSkipped(context, "tab URL unavailable");
      return;
    }
    if (!isEvaluatableUrl(tab.url)) {
      logRedirectSkipped(context, "tab is no longer an http/https page");
      return;
    }
    if (tab.url !== context.url) {
      logRedirectSkipped(context, "tab URL changed before redirect");
      return;
    }

    chrome.tabs.update(context.tabId, { url: blockedUrl }, () => {
      const updateError = chrome.runtime.lastError;
      if (updateError) {
        setNativeStatus({ error: updateError.message }, { log: false });
        logRedirectSkipped(context, updateError.message);
        return;
      }
      console.log(`SelfBoss redirected blocked tab [${EXTENSION_CONTEXT}]`, {
        tab_id: context.tabId,
        host: context.host,
        access_level: message.access_level,
        reason: message.reason,
        sweep_reason: context.reason,
        incognito: Boolean(context.incognito),
        context: context.context || EXTENSION_CONTEXT,
      });
    });
  });
}

function logRedirectSkipped(context, skipReason) {
  console.log(`SelfBoss redirect skipped [${EXTENSION_CONTEXT}]`, {
    tab_id: context.tabId,
    host: context.host || "unknown",
    sweep_reason: context.reason || "unknown",
    incognito: Boolean(context.incognito),
    context: context.context || EXTENSION_CONTEXT,
    reason: skipReason,
  });
}

function isTabVisibleToCurrentContext(tab) {
  return Boolean(tab && tab.incognito) === IS_INCOGNITO_CONTEXT;
}

function sweepOpenTabs(reason) {
  if (!nativePort) {
    return;
  }

  chrome.tabs.query({}, (tabs) => {
    const runtimeError = chrome.runtime.lastError;
    if (runtimeError) {
      setNativeStatus({ error: runtimeError.message }, { log: false });
      return;
    }

    for (const tab of tabs || []) {
      if (
        !tab ||
        tab.id === undefined ||
        !tab.url ||
        !isEvaluatableUrl(tab.url) ||
        !isTabVisibleToCurrentContext(tab)
      ) {
        continue;
      }
      evaluateTabUrl(tab.id, tab.url, reason, Boolean(tab.incognito));
    }
  });
}

function evaluateCurrentTab(reason) {
  if (!connectToNativeHost()) {
    return;
  }

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs && tabs[0];
    if (!tab || !tab.url || !isEvaluatableUrl(tab.url) || !isTabVisibleToCurrentContext(tab)) {
      return;
    }
    evaluateTabUrl(tab.id, tab.url, reason, Boolean(tab.incognito));
  });
}

function evaluateActivatedTab(tabId, reason) {
  if (!connectToNativeHost() || tabId === undefined) {
    return;
  }

  chrome.tabs.get(tabId, (tab) => {
    const runtimeError = chrome.runtime.lastError;
    if (runtimeError) {
      setNativeStatus({ error: runtimeError.message }, { log: false });
      return;
    }
    if (!tab || !tab.url || !isEvaluatableUrl(tab.url) || !isTabVisibleToCurrentContext(tab)) {
      return;
    }
    evaluateTabUrl(tab.id, tab.url, reason, Boolean(tab.incognito));
  });
}

function evaluateTabUrl(tabId, url, reason, incognito = false) {
  if (!nativePort || tabId === undefined || !url || !isEvaluatableUrl(url)) {
    return;
  }
  const cacheKey = `${tabId}:${url}:${reason}`;
  const now = Date.now();
  const lastEvaluation = evaluatedTabUrls.get(cacheKey);
  if (lastEvaluation && now - lastEvaluation < EVALUATION_DEBOUNCE_MS) {
    return;
  }
  evaluatedTabUrls.set(cacheKey, now);
  pendingUrlEvaluations.push(
    evaluationContextFromUrl(tabId, url, reason, incognito),
  );
  nativePort.postMessage({
    type: "evaluate_url",
    url,
    tab_id: tabId,
    reason,
    browser: BROWSER_ID,
    context: EXTENSION_CONTEXT,
    incognito: Boolean(incognito),
  });
}

function initializeExtensionContext(_reason) {
  connectToNativeHost();
  setupStatusAlarm();
  requestStatus();
  requestBlockedDomainsSnapshot();
}

chrome.runtime.onInstalled.addListener(() => {
  initializeExtensionContext("installed");
});

chrome.runtime.onStartup.addListener(() => {
  initializeExtensionContext("startup");
});

chrome.action.onClicked.addListener(() => {
  initializeExtensionContext("action");
  evaluateCurrentTab("manual_check");
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== STATUS_ALARM_NAME) {
    return;
  }
  requestStatus();
});

chrome.tabs.onActivated.addListener((activeInfo) => {
  evaluateActivatedTab(activeInfo ? activeInfo.tabId : undefined, "active_tab");
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!tab.active || !changeInfo.url || !isTabVisibleToCurrentContext(tab)) {
    return;
  }
  evaluateTabUrl(tabId, changeInfo.url, "navigation", Boolean(tab.incognito));
});

chrome.tabs.onRemoved.addListener((tabId) => {
  const prefix = `${tabId}:`;
  for (const cacheKey of evaluatedTabUrls.keys()) {
    if (cacheKey.startsWith(prefix)) {
      evaluatedTabUrls.delete(cacheKey);
    }
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === OPEN_SELF_BOSS_MESSAGE) {
    const senderUrl = sender && typeof sender.url === "string" ? sender.url : "";
    if (!senderUrl.startsWith(chrome.runtime.getURL("blocked.html"))) {
      return false;
    }
    return requestOpenSelfBoss(sendResponse);
  }

  if (!message || message.type !== YOUTUBE_ROUTE_CHANGE_MESSAGE) {
    return false;
  }

  const tab = sender ? sender.tab : null;
  const tabId = tab ? tab.id : undefined;
  const url = typeof message.url === "string" ? message.url : "";
  const senderUrl = sender && typeof sender.url === "string" ? sender.url : "";
  const senderFrameId = sender ? sender.frameId : undefined;
  if (
    tabId === undefined ||
    senderFrameId !== 0 ||
    !isTabVisibleToCurrentContext(tab) ||
    !isYouTubeUrl(url) ||
    (senderUrl && !isYouTubeUrl(senderUrl))
  ) {
    console.log(`SelfBoss YouTube SPA route ignored [${EXTENSION_CONTEXT}]`, {
      reason: "invalid sender or URL",
      incognito: Boolean(tab && tab.incognito),
      context: EXTENSION_CONTEXT,
    });
    return false;
  }

  youtubeSpaContentScriptSeen = true;
  if (!connectToNativeHost()) {
    return false;
  }
  evaluateTabUrl(
    tabId,
    url,
    "youtube_spa_route_change",
    Boolean(tab.incognito),
  );
  return false;
});

initializeExtensionContext("service_worker_start");
