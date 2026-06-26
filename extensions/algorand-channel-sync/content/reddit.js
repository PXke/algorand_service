(function () {
  browser.runtime.onMessage.addListener((request, sender, sendResponse) => {
    return respondToBackground({ ...request, site: "reddit" }, sender, sendResponse);
  });
})();
