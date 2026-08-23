document.documentElement.dataset.selfbossBlockedPage = "true";

const params = new URLSearchParams(window.location.search);

function setText(id, value, fallback) {
  const element = document.getElementById(id);
  if (!element) {
    return;
  }
  const text = typeof value === "string" && value.trim() ? value.trim() : fallback;
  element.textContent = text;
}

setText("blocked-host", params.get("host"), "Unknown");
setText("blocked-level", params.get("level"), "Unknown");
setText("blocked-url-family", params.get("url_family"), "Unknown");
setText("blocked-path-kind", params.get("path_kind"), "Unknown");
setText("blocked-reason", params.get("reason"), "Blocked by LoopGuard.");

const openButton = document.getElementById("open-selfboss");
const openStatus = document.getElementById("open-status");

function setOpenStatus(value) {
  if (!openStatus) {
    return;
  }
  openStatus.textContent = value;
}

if (openButton) {
  openButton.addEventListener("click", () => {
    openButton.disabled = true;
    setOpenStatus("Opening LoopGuard...");
    chrome.runtime.sendMessage(
      { type: "selfboss_open_desktop" },
      (response) => {
        const runtimeError = chrome.runtime.lastError;
        openButton.disabled = false;
        if (runtimeError) {
          setOpenStatus(runtimeError.message || "LoopGuard could not be opened.");
          return;
        }
        const reason =
          response && typeof response.reason === "string" && response.reason.trim()
            ? response.reason.trim()
            : "LoopGuard open request finished.";
        setOpenStatus(reason);
      },
    );
  });
}
