// Stand-in for a custom card installed by something other than Choremander -
// HACS puts real ones here. It exists so dev/fixtures/lovelace_resources can
// point at a module that genuinely loads. Lovelace resources are fetched on
// every dashboard, so a 404 here would break the clean-console assertion in
// e2e/specs/kid-dashboard.spec.ts even though this card sits on the parent
// dashboard.
//
// Its reason for existing is issue #2 - Choremander used to write to Lovelace's
// resource collection before it had been loaded from disk, which truncated
// .storage/lovelace_resources to Choremander's own cards and erased everyone
// else's. The dev environment had no third-party resources at all, so nothing
// caught it. Now it has one, and tests/e2e/test_lovelace_resources.py checks
// that it survives startup.

class ThirdPartyCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
  }

  getCardSize() {
    return 1;
  }
}

customElements.define("third-party-card", ThirdPartyCard);
