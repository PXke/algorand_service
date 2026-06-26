(function () {
  browser.runtime.onMessage.addListener((request, sender, sendResponse) => {
    return respondToBackground({ ...request, site: "telegram" }, sender, sendResponse);
  });
})();
