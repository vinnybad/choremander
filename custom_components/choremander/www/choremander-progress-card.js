/**
 * Choremander Progress Card
 * A custom Lovelace card for displaying a child's progress toward rewards.
 */

const LitElement = customElements.get("hui-masonry-view")
  ? Object.getPrototypeOf(customElements.get("hui-masonry-view"))
  : Object.getPrototypeOf(customElements.get("hui-view"));

const html = LitElement.prototype.html;
const css = LitElement.prototype.css;

class ChoremandorProgressCard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
    };
  }

  static getConfigElement() {
    return document.createElement("choremander-progress-card-editor");
  }

  static getStubConfig() {
    return {
      child_entity: "",
      overview_entity: "sensor.choremander_overview",
      title: "Reward Progress",
    };
  }

  setConfig(config) {
    if (!config.child_entity) {
      throw new Error("Please define a child_entity");
    }
    this.config = {
      overview_entity: "sensor.choremander_overview",
      title: "Reward Progress",
      ...config,
    };
  }

  getCardSize() {
    return 4;
  }

  render() {
    if (!this.hass || !this.config) {
      return html``;
    }

    const childEntity = this.hass.states[this.config.child_entity];
    const overviewEntity = this.hass.states[this.config.overview_entity];

    if (!childEntity) {
      return html`
        <ha-card header="${this.config.title}">
          <div class="card-content">
            <div class="error">Child entity not found: ${this.config.child_entity}</div>
          </div>
        </ha-card>
      `;
    }

    const childName = childEntity.attributes.child_name || "Child";
    const childAvatar = childEntity.attributes.avatar || "mdi:account-circle";
    const currentPoints = parseInt(childEntity.state) || 0;
    const pointsName = childEntity.attributes.unit_of_measurement || "Stars";

    // Get rewards from overview entity
    let rewards = [];
    if (overviewEntity && overviewEntity.attributes.rewards) {
      rewards = overviewEntity.attributes.rewards;
    }

    // Sort rewards by how close the child is to achieving them (percentage, descending)
    const sortedRewards = [...rewards].sort((a, b) => {
      const progressA = Math.min(currentPoints / a.cost, 1);
      const progressB = Math.min(currentPoints / b.cost, 1);
      return progressB - progressA;
    });

    return html`
      <ha-card>
        <div class="card-header">
          <div class="header-content">
            <ha-icon icon="${childAvatar}" class="child-avatar"></ha-icon>
            <div class="header-text">
              <div class="title">${this.config.title || childName}</div>
              <div class="subtitle">${childName}'s Progress</div>
            </div>
          </div>
          <div class="points-display">
            <ha-icon icon="mdi:star" class="points-icon"></ha-icon>
            <span class="points-value">${currentPoints}</span>
            <span class="points-label">${pointsName}</span>
          </div>
        </div>

        <div class="card-content">
          ${sortedRewards.length === 0
            ? html`<div class="no-rewards">No rewards available</div>`
            : sortedRewards.map((reward) => this.renderReward(reward, currentPoints))}
        </div>
      </ha-card>
    `;
  }

  renderReward(reward, currentPoints) {
    const progress = Math.min(currentPoints / reward.cost, 1);
    const progressPercent = Math.round(progress * 100);
    const canAfford = currentPoints >= reward.cost;
    const remaining = Math.max(reward.cost - currentPoints, 0);
    const rewardIcon = reward.icon || "mdi:gift";

    return html`
      <div class="reward-item ${canAfford ? "affordable" : ""}">
        <div class="reward-header">
          <div class="reward-info">
            <ha-icon icon="${rewardIcon}" class="reward-icon ${canAfford ? "celebrate" : ""}"></ha-icon>
            <span class="reward-name">${reward.name}</span>
          </div>
          <div class="reward-progress-text">
            <span class="current ${canAfford ? "highlight" : ""}">${currentPoints}</span>
            <span class="separator">/</span>
            <span class="target">${reward.cost}</span>
          </div>
        </div>

        <div class="progress-container">
          <div
            class="progress-bar ${canAfford ? "complete" : ""}"
            style="width: ${progressPercent}%"
          ></div>
        </div>

        ${canAfford
          ? html`<div class="affordable-badge">
              <ha-icon icon="mdi:check-circle" class="check-icon"></ha-icon>
              <span>Ready to claim!</span>
            </div>`
          : html`<div class="remaining-text">${remaining} more ${remaining === 1 ? "point" : "points"} needed</div>`}
      </div>
    `;
  }

  static get styles() {
    return css`
      :host {
        --card-primary-color: var(--primary-color, #03a9f4);
        --card-success-color: var(--success-color, #4caf50);
        --card-warning-color: var(--warning-color, #ff9800);
        --progress-background: var(--secondary-background-color, #e0e0e0);
        --text-primary: var(--primary-text-color, #212121);
        --text-secondary: var(--secondary-text-color, #757575);
      }

      ha-card {
        overflow: hidden;
      }

      .card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 16px 16px 8px 16px;
        background: linear-gradient(135deg, var(--card-primary-color) 0%, var(--card-primary-color) 100%);
        color: white;
      }

      .header-content {
        display: flex;
        align-items: center;
        gap: 12px;
      }

      .child-avatar {
        --mdc-icon-size: 40px;
        opacity: 0.9;
      }

      .header-text {
        display: flex;
        flex-direction: column;
      }

      .title {
        font-size: 1.2rem;
        font-weight: 500;
      }

      .subtitle {
        font-size: 0.85rem;
        opacity: 0.85;
      }

      .points-display {
        display: flex;
        align-items: center;
        gap: 4px;
        background: rgba(255, 255, 255, 0.2);
        padding: 8px 12px;
        border-radius: 20px;
      }

      .points-icon {
        --mdc-icon-size: 20px;
        color: #ffd700;
      }

      .points-value {
        font-size: 1.4rem;
        font-weight: bold;
      }

      .points-label {
        font-size: 0.8rem;
        opacity: 0.85;
      }

      .card-content {
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 16px;
      }

      .no-rewards {
        text-align: center;
        color: var(--text-secondary);
        padding: 20px;
        font-style: italic;
      }

      .reward-item {
        background: var(--card-background-color, #fff);
        border: 1px solid var(--divider-color, #e0e0e0);
        border-radius: 12px;
        padding: 12px;
        transition: all 0.3s ease;
      }

      .reward-item.affordable {
        border-color: var(--card-success-color);
        background: linear-gradient(135deg, rgba(76, 175, 80, 0.05) 0%, rgba(76, 175, 80, 0.1) 100%);
        box-shadow: 0 2px 8px rgba(76, 175, 80, 0.15);
      }

      .reward-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;
      }

      .reward-info {
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .reward-icon {
        --mdc-icon-size: 24px;
        color: var(--card-primary-color);
      }

      .reward-icon.celebrate {
        color: var(--card-success-color);
        animation: pulse 1.5s ease-in-out infinite;
      }

      @keyframes pulse {
        0%, 100% {
          transform: scale(1);
        }
        50% {
          transform: scale(1.15);
        }
      }

      .reward-name {
        font-weight: 500;
        color: var(--text-primary);
      }

      .reward-progress-text {
        font-family: monospace;
        font-size: 1rem;
        display: flex;
        align-items: baseline;
        gap: 2px;
      }

      .current {
        color: var(--card-primary-color);
        font-weight: bold;
      }

      .current.highlight {
        color: var(--card-success-color);
      }

      .separator {
        color: var(--text-secondary);
      }

      .target {
        color: var(--text-secondary);
      }

      .progress-container {
        height: 8px;
        background: var(--progress-background);
        border-radius: 4px;
        overflow: hidden;
        margin-bottom: 6px;
      }

      .progress-bar {
        height: 100%;
        background: linear-gradient(90deg, var(--card-primary-color) 0%, var(--card-primary-color) 100%);
        border-radius: 4px;
        transition: width 0.5s ease-out;
      }

      .progress-bar.complete {
        background: linear-gradient(90deg, var(--card-success-color) 0%, #81c784 100%);
      }

      .remaining-text {
        font-size: 0.8rem;
        color: var(--text-secondary);
        text-align: right;
      }

      .affordable-badge {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 4px;
        background: var(--card-success-color);
        color: white;
        padding: 4px 12px;
        border-radius: 12px;
        font-size: 0.85rem;
        font-weight: 500;
        margin-top: 4px;
      }

      .check-icon {
        --mdc-icon-size: 16px;
      }

      .error {
        color: var(--error-color, #f44336);
        padding: 16px;
        text-align: center;
      }
    `;
  }
}

// Card Editor for visual configuration in Lovelace UI
class ChoremandorProgressCardEditor extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
    };
  }

  setConfig(config) {
    this.config = config;
  }

  configChanged(newConfig) {
    const event = new Event("config-changed", {
      bubbles: true,
      composed: true,
    });
    event.detail = { config: newConfig };
    this.dispatchEvent(event);
  }

  get _child_entity() {
    return this.config?.child_entity || "";
  }

  get _overview_entity() {
    return this.config?.overview_entity || "sensor.choremander_overview";
  }

  get _title() {
    return this.config?.title || "";
  }

  render() {
    if (!this.hass || !this.config) {
      return html``;
    }

    // Filter entities to only show child point sensors
    const entities = Object.keys(this.hass.states)
      .filter((eid) => eid.endsWith("_points") && this.hass.states[eid].attributes.child_id)
      .sort();

    return html`
      <div class="card-config">
        <div class="form-row">
          <label>Child Entity</label>
          <select
            @change="${(e) => this.configChanged({ ...this.config, child_entity: e.target.value })}"
          >
            <option value="">Select a child...</option>
            ${entities.map(
              (entity) => html`
                <option value="${entity}" ?selected="${this._child_entity === entity}">
                  ${this.hass.states[entity].attributes.child_name || entity}
                </option>
              `
            )}
          </select>
        </div>

        <div class="form-row">
          <label>Overview Entity</label>
          <input
            type="text"
            .value="${this._overview_entity}"
            @input="${(e) => this.configChanged({ ...this.config, overview_entity: e.target.value })}"
          />
        </div>

        <div class="form-row">
          <label>Title (optional)</label>
          <input
            type="text"
            .value="${this._title}"
            placeholder="Reward Progress"
            @input="${(e) => this.configChanged({ ...this.config, title: e.target.value })}"
          />
        </div>
      </div>
    `;
  }

  static get styles() {
    return css`
      .card-config {
        padding: 16px;
      }

      .form-row {
        margin-bottom: 16px;
      }

      .form-row label {
        display: block;
        margin-bottom: 4px;
        font-weight: 500;
      }

      .form-row input,
      .form-row select {
        width: 100%;
        padding: 8px;
        border: 1px solid var(--divider-color, #e0e0e0);
        border-radius: 4px;
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color);
        box-sizing: border-box;
      }

      .form-row input:focus,
      .form-row select:focus {
        outline: none;
        border-color: var(--primary-color);
      }
    `;
  }
}

// Register the custom elements
customElements.define("choremander-progress-card", ChoremandorProgressCard);
customElements.define("choremander-progress-card-editor", ChoremandorProgressCardEditor);

// Register the card with Home Assistant
window.customCards = window.customCards || [];
window.customCards.push({
  type: "choremander-progress-card",
  name: "Choremander Progress Card",
  description: "Shows a child's progress toward available rewards",
  preview: true,
  documentationURL: "https://github.com/your-repo/choremander",
});

console.info(
  "%c CHOREMANDER-PROGRESS-CARD %c Loaded ",
  "color: white; background: #03a9f4; font-weight: bold;",
  "color: #03a9f4; background: white; font-weight: bold;"
);
