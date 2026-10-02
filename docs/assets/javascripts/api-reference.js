let scalarLoader;
let apiReference;
let paletteObserver;
let apiPath;

function disposeReference() {
  paletteObserver?.disconnect();
  apiReference?.destroy();
  apiReference = undefined;
}

// Dispose before instant navigation replaces the page and its head styles.
location$.subscribe((url) => {
  if (url.pathname !== apiPath) disposeReference();
});

document$.subscribe(async () => {
  disposeReference();
  const container = document.getElementById("landing-api-reference");
  if (!container) return;

  try {
    const { createApiReference } = await (scalarLoader ??= import(
      "https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.72.4/esm.js"
    ));
    if (!container.isConnected) return;
    apiPath = location.pathname;

    const configuration = {
      url: document.getElementById("landing-openapi").href,
      theme: "none",
      layout: "classic",
      showSidebar: false,
      hideDarkModeToggle: true,
      withDefaultFonts: false,
      hideTestRequestButton: true,
      hideClientButton: true,
      showDeveloperTools: "never",
      telemetry: false,
      agent: { disabled: true },
    };
    const render = () => {
      apiReference?.destroy();
      apiReference = createApiReference(container, {
        ...configuration,
        forceDarkModeState: document.body.dataset.mdColorScheme === "slate" ? "dark" : "light",
      });
    };
    render();
    paletteObserver = new MutationObserver(render);
    paletteObserver.observe(document.body, {
      attributes: true,
      attributeFilter: ["data-md-color-scheme"],
    });
  } catch (error) {
    container.textContent = "The API reference could not load. Use the OpenAPI download above.";
    console.error(error);
  }
});
