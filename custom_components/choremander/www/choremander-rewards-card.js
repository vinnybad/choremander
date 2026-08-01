/**
 * Choremander Rewards Card
 * A Lovelace card for displaying available rewards.
 * Shows rewards in a vertical list with star cost, name, description, and progress gauges.
 * Supports regular rewards, Jackpot rewards, and Dynamic rewards (goal-based pricing).
 *
 * Version: 0.0.8
 * Last Updated: 2026-08-01
 */

const LitElement = customElements.get("hui-masonry-view")
  ? Object.getPrototypeOf(customElements.get("hui-masonry-view"))
  : Object.getPrototypeOf(customElements.get("hui-view"));

const html = LitElement.prototype.html;
const css = LitElement.prototype.css;

class ChoremanderRewardsCard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
      _pending: { type: Object },
      _loading: { type: Object },
      _celebrating: { type: Object },
      _confetti: { type: Array },
    };
  }

  constructor() {
    super();
    // { reward, choices: [child], childId } - the reward awaiting confirmation.
    // childId is preselected when only one child can claim, and chosen in the
    // dialog otherwise, because claim_reward always charges exactly one child.
    this._pending = null;
    this._loading = {};
    this._celebrating = null;
    this._confetti = [];
    this._audioContext = null;
  }

  static get styles() {
    return css`
      :host {
        display: block;
        --reward-purple: #9b59b6;
        --reward-purple-light: #a569bd;
        --reward-gold: #f1c40f;
        --reward-gold-dark: #d4a80a;
        --text-primary: var(--primary-text-color, #212121);
        --text-secondary: var(--secondary-text-color, #757575);
        --card-bg: var(--card-background-color, #fff);
        --divider: var(--divider-color, #e0e0e0);
      }

      ha-card {
        overflow: hidden;
      }

      .card-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px 20px;
        background: linear-gradient(135deg, var(--reward-purple) 0%, var(--reward-purple-light) 100%);
        color: white;
      }

      .header-content {
        display: flex;
        align-items: center;
        gap: 12px;
      }

      .header-icon {
        --mdc-icon-size: 32px;
        opacity: 0.95;
      }

      .header-title {
        font-size: 1.3rem;
        font-weight: 600;
      }

      .reward-count {
        background: rgba(255, 255, 255, 0.2);
        padding: 4px 12px;
        border-radius: 16px;
        font-size: 0.9rem;
        font-weight: 500;
      }

      .card-content {
        padding: 16px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }

      /* Individual reward row */
      .reward-row {
        display: flex;
        align-items: flex-start;
        gap: 16px;
        padding: 16px;
        background: var(--card-bg);
        border: 1px solid var(--divider);
        border-radius: 12px;
        transition: box-shadow 0.2s ease, transform 0.15s ease;
      }

      .reward-row:hover {
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
        transform: translateY(-1px);
      }

      /* Cost badge - prominently displayed */
      .cost-badge {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        min-width: 70px;
        padding: 12px 8px;
        background: linear-gradient(135deg, var(--reward-gold) 0%, var(--reward-gold-dark) 100%);
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(241, 196, 15, 0.3);
        flex-shrink: 0;
      }

      .cost-badge ha-icon {
        --mdc-icon-size: 24px;
        color: white;
        filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.2));
        margin-bottom: 4px;
      }

      .cost-value {
        font-size: 1.4rem;
        font-weight: 700;
        color: white;
        text-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
        line-height: 1;
      }

      .cost-label {
        font-size: 0.65rem;
        font-weight: 600;
        color: rgba(255, 255, 255, 0.9);
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-top: 2px;
      }

      /* Reward details */
      .reward-details {
        display: flex;
        flex-direction: column;
        gap: 6px;
        flex: 1;
        min-width: 0;
        padding-top: 4px;
      }

      .reward-name {
        font-size: 1.15rem;
        font-weight: 600;
        color: var(--text-primary);
        line-height: 1.3;
      }

      .reward-description {
        font-size: 0.9rem;
        color: var(--text-secondary);
        line-height: 1.4;
      }

      /* Child assignment indicator */
      .assigned-children {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        margin-top: 4px;
      }

      .child-badge {
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        background: rgba(155, 89, 182, 0.15);
        border-radius: 12px;
        font-size: 0.75rem;
        color: var(--reward-purple);
        font-weight: 500;
      }

      .child-badge.all-children {
        background: rgba(46, 204, 113, 0.15);
        color: #27ae60;
      }

      /* Progress bar styles */
      .progress-section {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 8px;
        width: 100%;
      }

      .progress-bar-container {
        display: flex;
        align-items: center;
        gap: 10px;
        width: 100%;
      }

      .progress-bar {
        flex: 1;
        height: 14px;
        background: rgba(0, 0, 0, 0.08);
        border-radius: 7px;
        overflow: hidden;
        position: relative;
        border: 2px solid rgba(52, 152, 219, 0.4);
        box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.1);
      }

      .progress-fill {
        height: 100%;
        border-radius: 7px;
        background: linear-gradient(90deg, #3498db 0%, #2ecc71 100%);
        transition: width 0.4s ease;
        position: relative;
        overflow: hidden;
      }

      .progress-fill::after {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 50%;
        height: 100%;
        background: linear-gradient(
          90deg,
          transparent,
          rgba(255, 255, 255, 0.4),
          transparent
        );
        animation: shimmer 2s infinite;
      }

      @keyframes shimmer {
        0% { left: -100%; }
        100% { left: 200%; }
      }

      .progress-text {
        font-size: 0.85rem;
        font-weight: 600;
        color: var(--text-secondary);
        white-space: nowrap;
        min-width: 70px;
        text-align: right;
      }

      .shortfall-label {
        display: flex;
        align-items: center;
        gap: 3px;
        font-size: 0.78rem;
        font-weight: 500;
        color: var(--text-secondary);
        opacity: 0.75;
        margin-top: 2px;
      }

      .shortfall-label ha-icon {
        --mdc-icon-size: 13px;
        color: var(--reward-gold-dark);
      }

      /* Jackpot reward styles */
      .reward-row.jackpot {
        border: 2px solid var(--reward-gold);
        background: linear-gradient(135deg, rgba(241, 196, 15, 0.08) 0%, rgba(255, 255, 255, 0) 100%);
        position: relative;
        overflow: hidden;
      }

      .reward-row.jackpot::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: linear-gradient(
          45deg,
          transparent 40%,
          rgba(241, 196, 15, 0.1) 50%,
          transparent 60%
        );
        animation: jackpot-shine 40s ease-in-out infinite;
        pointer-events: none;
      }

      /* Shine 3 times (each ~2.5s = 7.5s total), then wait ~32.5s before repeating */
      @keyframes jackpot-shine {
        0% { transform: translateX(-100%); }
        2.5% { transform: translateX(100%); }
        5% { transform: translateX(-100%); }
        7.5% { transform: translateX(100%); }
        10% { transform: translateX(-100%); }
        12.5% { transform: translateX(100%); }
        15%, 100% { transform: translateX(-100%); opacity: 0; }
      }

      .jackpot-label {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 10px;
        background: linear-gradient(135deg, var(--reward-gold) 0%, #e67e22 100%);
        border-radius: 12px;
        font-size: 0.7rem;
        font-weight: 700;
        color: white;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 4px;
        box-shadow: 0 2px 6px rgba(241, 196, 15, 0.4);
      }

      .jackpot-label span {
        font-size: 0.8rem;
      }

      /* Multi-child progress bar for jackpot */
      .jackpot-progress-bar {
        flex: 1;
        height: 18px;
        background: rgba(0, 0, 0, 0.08);
        border-radius: 9px;
        overflow: hidden;
        position: relative;
        display: flex;
        border: 2px solid rgba(241, 196, 15, 0.5);
        box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.1);
      }

      .jackpot-segment {
        height: 100%;
        transition: width 0.4s ease;
        position: relative;
        overflow: hidden;
      }

      .jackpot-segment::after {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(
          90deg,
          transparent,
          rgba(255, 255, 255, 0.4),
          transparent
        );
        animation: jackpot-segment-shimmer 40s infinite;
      }

      /* Shimmer 3 times then wait ~30s before repeating */
      @keyframes jackpot-segment-shimmer {
        0% { left: -100%; }
        2.5% { left: 200%; }
        5% { left: -100%; }
        7.5% { left: 200%; }
        10% { left: -100%; }
        12.5% { left: 200%; }
        15%, 100% { left: -100%; opacity: 0; }
      }

      .jackpot-segment:first-child {
        border-radius: 9px 0 0 9px;
      }

      .jackpot-segment:last-child {
        border-radius: 0 9px 9px 0;
      }

      .jackpot-segment:only-child {
        border-radius: 9px;
      }

      /* Fun kid-friendly colors for segments */
      .jackpot-segment.color-0 { background: linear-gradient(180deg, #ff6b9d 0%, #e91e63 100%); }
      .jackpot-segment.color-1 { background: linear-gradient(180deg, #64b5f6 0%, #2196f3 100%); }
      .jackpot-segment.color-2 { background: linear-gradient(180deg, #81c784 0%, #4caf50 100%); }
      .jackpot-segment.color-3 { background: linear-gradient(180deg, #ffb74d 0%, #ff9800 100%); }
      .jackpot-segment.color-4 { background: linear-gradient(180deg, #ba68c8 0%, #9c27b0 100%); }
      .jackpot-segment.color-5 { background: linear-gradient(180deg, #4dd0e1 0%, #00bcd4 100%); }

      .jackpot-breakdown {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: 4px;
        font-size: 0.8rem;
        color: var(--text-secondary);
      }

      .jackpot-child-contribution {
        display: inline-flex;
        align-items: center;
        gap: 3px;
        padding: 2px 8px;
        background: rgba(0, 0, 0, 0.05);
        border-radius: 10px;
        font-weight: 500;
      }

      .jackpot-child-contribution .color-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
      }

      .jackpot-child-contribution .color-dot.color-0 { background: #e91e63; }
      .jackpot-child-contribution .color-dot.color-1 { background: #2196f3; }
      .jackpot-child-contribution .color-dot.color-2 { background: #4caf50; }
      .jackpot-child-contribution .color-dot.color-3 { background: #ff9800; }
      .jackpot-child-contribution .color-dot.color-4 { background: #9c27b0; }
      .jackpot-child-contribution .color-dot.color-5 { background: #00bcd4; }

      .jackpot-total {
        font-weight: 700;
        color: var(--reward-gold-dark);
        padding: 2px 8px;
        background: rgba(241, 196, 15, 0.15);
        border-radius: 10px;
      }

      /* Cost badge for jackpot - special styling */
      .reward-row.jackpot .cost-badge {
        background: linear-gradient(135deg, var(--reward-gold) 0%, #e67e22 100%);
        box-shadow: 0 3px 10px rgba(241, 196, 15, 0.4);
      }

      /* Dynamic reward styles */
      .reward-row.dynamic {
        border: 1px dashed rgba(52, 152, 219, 0.5);
      }

      .cost-badge.dynamic-cost {
        background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
        box-shadow: 0 2px 8px rgba(52, 152, 219, 0.3);
        position: relative;
      }

      .dynamic-indicator {
        font-size: 0.9rem;
        margin-top: 2px;
        filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.3));
      }

      /* Reward icon (if available) */
      .reward-icon-container {
        width: 44px;
        height: 44px;
        border-radius: 50%;
        background: linear-gradient(135deg, var(--reward-purple) 0%, var(--reward-purple-light) 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
      }

      .reward-icon-container ha-icon {
        --mdc-icon-size: 24px;
        color: white;
      }

      /* The claim affordance is a real <button>, so undo the UA chrome. */
      .reward-claim-button {
        border: none;
        padding: 0;
        font: inherit;
        color: inherit;
        cursor: pointer;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
      }

      .reward-claim-button:hover:not(:disabled) {
        transform: scale(1.08);
        box-shadow: 0 4px 12px rgba(155, 89, 182, 0.4);
      }

      .reward-claim-button:active:not(:disabled) {
        transform: scale(0.95);
      }

      .reward-claim-button:focus-visible {
        outline: 3px solid var(--reward-gold);
        outline-offset: 2px;
      }

      .reward-claim-button:disabled {
        cursor: default;
        opacity: 0.4;
      }

      .reward-claim-button.loading {
        opacity: 0.7;
      }

      .claim-spinner {
        animation: spin 1s linear infinite;
      }

      @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
      }

      /* Empty state */
      .empty-state {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 48px 24px;
        color: var(--text-secondary);
        text-align: center;
      }

      .empty-state ha-icon {
        --mdc-icon-size: 56px;
        margin-bottom: 16px;
        opacity: 0.5;
        color: var(--reward-purple);
      }

      .empty-state .message {
        font-size: 1.1rem;
        font-weight: 500;
        margin-bottom: 4px;
        color: var(--text-primary);
      }

      .empty-state .submessage {
        font-size: 0.9rem;
        opacity: 0.8;
      }

      /* Error state */
      .error-state {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 40px 20px;
        color: var(--error-color, #f44336);
        text-align: center;
      }

      .error-state ha-icon {
        --mdc-icon-size: 48px;
        margin-bottom: 16px;
      }

      /* Responsive adjustments */
      @media (max-width: 400px) {
        .card-header {
          padding: 14px 16px;
        }

        .header-title {
          font-size: 1.15rem;
        }

        .card-content {
          padding: 12px;
          gap: 10px;
        }

        .reward-row {
          padding: 12px;
          gap: 12px;
        }

        .cost-badge {
          min-width: 60px;
          padding: 10px 6px;
        }

        .cost-badge ha-icon {
          --mdc-icon-size: 20px;
        }

        .cost-value {
          font-size: 1.2rem;
        }

        .reward-name {
          font-size: 1.05rem;
        }

        .reward-description {
          font-size: 0.85rem;
        }

        .reward-icon-container {
          width: 38px;
          height: 38px;
        }

        .reward-icon-container ha-icon {
          --mdc-icon-size: 20px;
        }
      }

      /* Celebration overlay */
      .celebration-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0, 0, 0, 0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 9999;
        animation: fade-in 0.3s ease;
      }

      @keyframes fade-in {
        from { opacity: 0; }
        to { opacity: 1; }
      }

      .celebration-content {
        background: white;
        border-radius: 30px;
        padding: 40px 50px;
        text-align: center;
        animation: pop-in 0.5s cubic-bezier(0.68, -0.55, 0.265, 1.55);
        position: relative;
        overflow: hidden;
      }

      @keyframes pop-in {
        from { transform: scale(0); opacity: 0; }
        to { transform: scale(1); opacity: 1; }
      }

      .celebration-stars {
        font-size: 4rem;
        margin-bottom: 16px;
        animation: star-bounce 0.6s ease infinite;
      }

      @keyframes star-bounce {
        0%, 100% { transform: scale(1) rotate(0deg); }
        50% { transform: scale(1.2) rotate(10deg); }
      }

      .celebration-title {
        font-size: 2.5rem;
        font-weight: bold;
        color: var(--reward-purple);
        margin-bottom: 8px;
      }

      .celebration-message {
        font-size: 1.3rem;
        color: #666;
        margin-bottom: 16px;
      }

      .celebration-points {
        font-size: 1.8rem;
        font-weight: bold;
        color: var(--reward-gold-dark);
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
      }

      .celebration-points ha-icon {
        --mdc-icon-size: 28px;
        color: var(--reward-gold);
      }

      /* Confetti */
      .confetti-container {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 10000;
        overflow: hidden;
      }

      .confetti {
        position: absolute;
        width: 10px;
        height: 10px;
        animation: confetti-fall 3s linear forwards;
      }

      @keyframes confetti-fall {
        0% { transform: translateY(-100px) rotate(0deg); opacity: 1; }
        100% { transform: translateY(100vh) rotate(720deg); opacity: 0; }
      }

      /* Confirmation dialog */
      .confirm-overlay {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 9999;
      }

      .confirm-dialog {
        background: var(--card-background-color, #fff);
        border-radius: 16px;
        padding: 24px;
        max-width: 320px;
        width: 90%;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        display: flex;
        flex-direction: column;
        gap: 16px;
      }

      .confirm-dialog .dialog-icon {
        display: flex;
        justify-content: center;
        --mdc-icon-size: 48px;
        color: var(--reward-purple);
      }

      .confirm-dialog .dialog-title {
        font-size: 1.15rem;
        font-weight: 600;
        color: var(--primary-text-color);
        text-align: center;
      }

      .confirm-dialog .dialog-body {
        font-size: 0.9rem;
        color: var(--secondary-text-color);
        text-align: center;
        line-height: 1.5;
      }

      .confirm-dialog .dialog-buttons {
        display: flex;
        gap: 10px;
      }

      .confirm-dialog button {
        flex: 1;
        padding: 10px;
        border: none;
        border-radius: 8px;
        font-size: 0.95rem;
        font-weight: 600;
        cursor: pointer;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
      }

      .confirm-dialog button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
      }

      .confirm-dialog button:active {
        transform: scale(0.97);
      }

      .confirm-dialog .btn-cancel {
        background: var(--secondary-background-color, #f5f5f5);
        color: var(--primary-text-color);
      }

      .confirm-dialog .btn-confirm {
        background: linear-gradient(135deg, var(--reward-purple) 0%, var(--reward-purple-light) 100%);
        color: white;
      }

      .confirm-dialog .dialog-reward-name {
        margin-bottom: 12px;
      }

      .confirm-dialog button:disabled {
        opacity: 0.45;
        cursor: default;
      }

      .confirm-dialog button:disabled:hover {
        transform: none;
        box-shadow: none;
      }

      /* Claimant picker, shown when the card is not scoped to one child and
         more than one of them can afford the reward. */
      .claimant-picker {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-bottom: 12px;
      }

      .claimant-prompt {
        font-size: 0.85rem;
        color: var(--secondary-text-color);
        margin-bottom: 2px;
      }

      .confirm-dialog .claimant-option {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 9px 12px;
        border: 2px solid var(--divider-color, #e0e0e0);
        border-radius: 8px;
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color);
        font-size: 0.9rem;
      }

      .confirm-dialog .claimant-option.selected {
        border-color: var(--reward-purple);
        background: rgba(155, 89, 182, 0.1);
      }

      .dialog-points-summary {
        background: var(--secondary-background-color, #f5f5f5);
        border-radius: 8px;
        padding: 10px 14px;
        display: flex;
        flex-direction: column;
        gap: 6px;
        font-size: 0.9rem;
      }

      .dialog-points-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        color: var(--primary-text-color);
      }

      .dialog-points-row.cost {
        color: var(--secondary-text-color);
        border-top: 1px solid var(--divider-color, #e0e0e0);
        padding-top: 6px;
      }

      .dialog-points-row.remaining {
        font-weight: 700;
        border-top: 1px solid var(--divider-color, #e0e0e0);
        padding-top: 6px;
        color: var(--reward-purple);
      }

      .points-val {
        display: flex;
        align-items: center;
        gap: 4px;
        font-weight: 600;
      }

      .points-val.cost {
        color: #e74c3c;
      }

      .points-val.remaining {
        color: var(--reward-purple);
      }
    `;
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error("Please define an entity (choremander overview sensor)");
    }
    this.config = {
      title: "Rewards",
      child_id: null, // Optional: filter rewards for a specific child
      show_child_badges: true, // Show which children can claim each reward
      claim_sound: "fanfare", // "none" to silence, or any fartN / fart_random
      ...config,
    };
  }

  getCardSize() {
    return 3;
  }

  static getConfigElement() {
    return document.createElement("choremander-rewards-card-editor");
  }

  static getStubConfig() {
    return {
      entity: "sensor.choremander_overview",
      title: "Rewards",
      child_id: null,
      show_child_badges: true,
      claim_sound: "fanfare",
    };
  }

  render() {
    if (!this.hass || !this.config) {
      return html``;
    }

    const entity = this.hass.states[this.config.entity];

    if (!entity) {
      return html`
        <ha-card>
          <div class="error-state">
            <ha-icon icon="mdi:alert-circle"></ha-icon>
            <div>Entity not found: ${this.config.entity}</div>
          </div>
        </ha-card>
      `;
    }

    if (entity.state === "unavailable" || entity.state === "unknown") {
      return html`
        <ha-card>
          <div class="error-state">
            <ha-icon icon="mdi:alert-circle"></ha-icon>
            <div>Choremander is unavailable</div>
          </div>
        </ha-card>
      `;
    }

    const allRewards = entity.attributes.rewards || [];
    const children = entity.attributes.children || [];
    const pointsIcon = entity.attributes.points_icon || "mdi:star";
    const pointsName = entity.attributes.points_name || "Stars";

    // Filter rewards based on child_id if configured
    let rewards = allRewards;
    if (this.config.child_id) {
      rewards = allRewards.filter((reward) => {
        const assignedTo = reward.assigned_to || [];
        // Show reward if assigned_to is empty (available to all) OR contains the child_id
        return assignedTo.length === 0 || assignedTo.includes(this.config.child_id);
      });
    }

    // Sort claimable rewards to the top, then jackpot first, then cost ascending.
    //
    // Affordability comes from _getClaimContext so the ordering agrees with the
    // dimming on each row. Computing it separately here is how the two ended up
    // disagreeing about which child's balance to compare against.
    const sortedRewards = [...rewards].sort((a, b) => {
      const aCtx = this._getClaimContext(a, children);
      const bCtx = this._getClaimContext(b, children);

      if (aCtx.canClaim !== bCtx.canClaim) return aCtx.canClaim ? -1 : 1;
      if (a.is_jackpot !== b.is_jackpot) return a.is_jackpot ? -1 : 1;
      return aCtx.displayCost - bCtx.displayCost;
    });

    // Create a map of child IDs to names for badge display
    const childMap = {};
    children.forEach((child) => {
      childMap[child.id] = child.name;
    });

    return html`
      <ha-card>
        <div class="card-header">
          <div class="header-content">
            <ha-icon class="header-icon" icon="mdi:gift-outline"></ha-icon>
            <span class="header-title">${this.config.title}</span>
          </div>
          ${rewards.length > 0
            ? html`<span class="reward-count">${rewards.length} ${rewards.length === 1 ? "reward" : "rewards"}</span>`
            : ""}
        </div>

        <div class="card-content">
          ${sortedRewards.length === 0
            ? this._renderEmptyState()
            : sortedRewards.map((reward) => this._renderRewardRow(reward, pointsIcon, pointsName, childMap, children))}
        </div>
      </ha-card>

      ${this._celebrating ? this._renderCelebration() : ""}
      ${this._confetti.length > 0 ? this._renderConfetti() : ""}

      ${this._pending ? this._renderConfirmDialog() : ""}
    `;
  }

  _renderEmptyState() {
    return html`
      <div class="empty-state">
        <ha-icon icon="mdi:gift-off-outline"></ha-icon>
        <div class="message">No Rewards Available</div>
        <div class="submessage">Add rewards in Choremander settings</div>
      </div>
    `;
  }

  _renderRewardRow(reward, pointsIcon, pointsName, childMap, children) {
    const rewardIcon = reward.icon || "mdi:gift";
    const hasDescription = reward.description && reward.description.trim().length > 0;
    const assignedTo = reward.assigned_to || [];
    const showChildBadges = this.config.show_child_badges !== false;
    const isJackpot = reward.is_jackpot || false;
    // Dynamic pricing is the default (override_point_value = false means dynamic)
    const isDynamicPricing = !reward.override_point_value;
    const claim = this._getClaimContext(reward, children);
    const displayCost = claim.displayCost;
    const relevantChildren = claim.eligible;

    // Calculate progress
    let currentStars = 0;
    let childContributions = [];

    if (isJackpot) {
      // Jackpot: calculate weighted contributions based on each child's expected share
      const childDailyPoints = reward.child_daily_points || {};
      const daysToGoal = reward.days_to_goal || 30;
      const totalDailyPoints = Object.values(childDailyPoints).reduce((sum, dp) => sum + dp, 0);

      relevantChildren.forEach((child, index) => {
        const points = child.points || 0;
        const dailyPoints = childDailyPoints[child.id] || 0;
        const expectedContribution = dailyPoints * daysToGoal;

        // Calculate weighted progress (0-100% of their expected share)
        let weightedProgress = 0;
        if (expectedContribution > 0) {
          weightedProgress = Math.min((points / expectedContribution) * 100, 100);
        }

        // Their "share" of the total goal (what % of the jackpot they're responsible for)
        let shareOfGoal = 0;
        if (totalDailyPoints > 0) {
          shareOfGoal = (dailyPoints / totalDailyPoints) * 100;
        }

        currentStars += points;
        childContributions.push({
          name: child.name,
          points: points,
          colorIndex: index % 6,
          expectedContribution: Math.round(expectedContribution),
          weightedProgress: weightedProgress,
          shareOfGoal: shareOfGoal,
          dailyPoints: dailyPoints
        });
      });
    } else {
      // Regular reward: progress for the scoped child, or for whoever is
      // closest to claiming it when the card covers the whole family. The
      // leader is what the affordable-first sort keys on, so the bar and the
      // ordering describe the same child.
      currentStars = claim.leader ? (claim.leader.points || 0) : 0;
    }

    const percentage = Math.min((currentStars / displayCost) * 100, 100);
    const canAfford = claim.canClaim;

    return html`
      <div class="reward-row ${isJackpot ? 'jackpot' : ''} ${isDynamicPricing ? 'dynamic' : ''}">
        <div class="cost-badge ${isDynamicPricing ? 'dynamic-cost' : ''}">
          <ha-icon icon="${pointsIcon}"></ha-icon>
          <span class="cost-value">${displayCost}</span>
          <span class="cost-label">${pointsName}</span>
          ${isDynamicPricing ? html`<span class="dynamic-indicator" title="Goal-based reward (${reward.days_to_goal || 30} days)">&#127919;</span>` : ''}
        </div>
        <div class="reward-details">
          ${isJackpot ? html`<div class="jackpot-label"><span>&#127920;</span> JACKPOT</div>` : ''}
          <div class="reward-name">${reward.name}</div>
          ${hasDescription
            ? html`<div class="reward-description">${reward.description}</div>`
            : ""}

          ${isJackpot
            ? this._renderJackpotProgress(reward, childContributions, currentStars, pointsIcon, displayCost)
            : this._renderRegularProgress(currentStars, displayCost, percentage, pointsIcon)}

          ${!canAfford && claim.shortfall !== null
            ? html`
                <div class="shortfall-label">
                  <ha-icon icon="${pointsIcon}"></ha-icon>
                  ${claim.shortfall} more ${pointsName} needed
                </div>
              `
            : ""}

          ${showChildBadges && !isJackpot
            ? html`
                <div class="assigned-children">
                  ${assignedTo.length === 0
                    ? html`<span class="child-badge all-children">All Children</span>`
                    : assignedTo.map(
                        (childId) =>
                          html`<span class="child-badge">${childMap[childId] || childId}</span>`
                      )}
                </div>
              `
            : ""}
        </div>
        ${this._renderClaimButton(reward, rewardIcon, claim)}
      </div>
    `;
  }

  /**
   * The claim affordance.
   *
   * A real <button> rather than a clickable <div>: it has to be reachable by
   * keyboard and announce itself, and `disabled` is what actually stops a
   * child hammering an unaffordable reward.
   */
  _renderClaimButton(reward, rewardIcon, claim) {
    const isLoading = Boolean(this._loading[reward.id]);
    const disabled = !claim.canClaim || isLoading;

    let label = `Claim ${reward.name}`;
    if (isLoading) {
      label = `Claiming ${reward.name}`;
    } else if (!claim.canClaim) {
      label = claim.eligible.length === 0
        ? `${reward.name} is not assigned to anyone`
        : `Not enough points to claim ${reward.name}`;
    }

    return html`
      <button
        class="reward-icon-container reward-claim-button ${claim.canClaim ? "can-afford" : "cannot-afford"} ${isLoading ? "loading" : ""}"
        ?disabled="${disabled}"
        aria-label="${label}"
        title="${label}"
        @click="${() => this._handleRedeem(reward, claim)}"
      >
        ${isLoading
          ? html`<ha-icon class="claim-spinner" icon="mdi:loading"></ha-icon>`
          : html`<ha-icon icon="${rewardIcon}"></ha-icon>`}
      </button>
    `;
  }

  _renderRegularProgress(currentStars, cost, percentage, pointsIcon) {
    return html`
      <div class="progress-section">
        <div class="progress-bar-container">
          <div class="progress-bar">
            <div class="progress-fill" style="width: ${percentage}%"></div>
          </div>
          <span class="progress-text">${currentStars}/${cost} <ha-icon icon="${pointsIcon}" style="--mdc-icon-size: 14px;"></ha-icon></span>
        </div>
      </div>
    `;
  }

  /**
   * Get the display cost for a reward based on child context.
   * For non-jackpot rewards, each child has their own calculated cost.
   * For jackpot rewards, all children share the same cost.
   */
  _getDisplayCost(reward, children) {
    const calculatedCosts = reward.calculated_costs || {};

    // If override_point_value is true, use manual cost
    if (reward.override_point_value) {
      return reward.cost;
    }

    // If filtering by a specific child, get that child's cost
    if (this.config.child_id && calculatedCosts[this.config.child_id]) {
      return calculatedCosts[this.config.child_id];
    }

    // For jackpot rewards, all children have the same cost - return first value
    if (reward.is_jackpot) {
      const costs = Object.values(calculatedCosts);
      if (costs.length > 0) {
        return costs[0];
      }
    }

    // For non-jackpot without specific child filter, get the first relevant child's cost
    const assignedTo = reward.assigned_to || [];
    if (assignedTo.length === 0) {
      // Available to all children - get first child's cost
      if (children.length > 0 && calculatedCosts[children[0].id]) {
        return calculatedCosts[children[0].id];
      }
    } else {
      // Get first assigned child's cost
      for (const childId of assignedTo) {
        if (calculatedCosts[childId]) {
          return calculatedCosts[childId];
        }
      }
    }

    // Fallback to manual cost
    return reward.cost;
  }

  /**
   * Per-child cost for a reward.
   *
   * Dynamic pricing is the default, and it prices each child separately, so the
   * cost a child is charged is not necessarily the cost shown on the badge.
   */
  _getCostForChild(reward, child) {
    if (reward.override_point_value) {
      return reward.cost;
    }
    const calculated = reward.calculated_costs || {};
    const own = calculated[child.id];
    return own === undefined ? reward.cost : own;
  }

  /**
   * Everything the claim UI needs to know about one reward.
   *
   * Ordering, dimming, the shortfall label and the confirm dialog each used to
   * decide independently who was claiming and whether they could afford it, and
   * they disagreed with each other and with the backend. This is the one place
   * that decides, and it follows the rule the backend actually enforces:
   * `async_claim_reward` charges a single child their own effective cost.
   *
   * That rule applies to jackpots too. A jackpot's progress bar pools every
   * child's points, but there is no pooled-spend service, so a jackpot is only
   * claimable when one child holds the whole cost on their own.
   */
  _getClaimContext(reward, children) {
    const assignedTo = reward.assigned_to || [];
    const eligible = assignedTo.length === 0
      ? children
      : children.filter((c) => assignedTo.includes(c.id));

    // A card scoped to a child never claims on anyone else's behalf.
    const candidates = this.config.child_id
      ? eligible.filter((c) => c.id === this.config.child_id)
      : eligible;

    const affordable = candidates.filter(
      (c) => (c.points || 0) >= this._getCostForChild(reward, c),
    );

    // Whoever is closest to claiming - the child the progress bar describes.
    const leader = [...candidates].sort(
      (a, b) =>
        (this._getCostForChild(reward, a) - (a.points || 0)) -
        (this._getCostForChild(reward, b) - (b.points || 0)),
    )[0] || null;

    const shortfall = leader
      ? Math.max(this._getCostForChild(reward, leader) - (leader.points || 0), 0)
      : null;

    return {
      eligible,
      candidates,
      affordable,
      leader,
      shortfall,
      canClaim: affordable.length > 0,
      displayCost: this._getDisplayCost(reward, children),
    };
  }

  _handleRedeem(reward, claim) {
    if (!claim.canClaim) return;
    this._pending = {
      reward,
      choices: claim.affordable,
      // Only one candidate can pay, so there is nothing to ask about.
      childId: claim.affordable.length === 1 ? claim.affordable[0].id : null,
    };
  }

  _cancelRedeem() {
    this._pending = null;
  }

  _selectClaimant(childId) {
    this._pending = { ...this._pending, childId };
  }

  _renderConfirmDialog() {
    const entity = this.hass.states[this.config.entity];
    const pointsIcon = entity?.attributes?.points_icon || "mdi:star";
    const pointsName = entity?.attributes?.points_name || "Stars";

    const { reward, choices, childId } = this._pending;
    const child = choices.find((c) => c.id === childId) || null;
    const cost = child ? this._getCostForChild(reward, child) : null;

    return html`
      <div class="confirm-overlay" @click="${this._cancelRedeem}">
        <div
          class="confirm-dialog"
          role="dialog"
          aria-modal="true"
          aria-label="Claim ${reward.name}"
          @click="${(e) => e.stopPropagation()}"
        >
          <ha-icon class="dialog-icon" icon="mdi:gift-outline"></ha-icon>
          <div class="dialog-title">Claim Reward?</div>
          <div class="dialog-body">
            <div class="dialog-reward-name">Claim <strong>${reward.name}</strong>?</div>

            ${choices.length > 1
              ? html`
                  <div class="claimant-picker">
                    <div class="claimant-prompt">Who is claiming it?</div>
                    ${choices.map(
                      (candidate) => html`
                        <button
                          class="claimant-option ${candidate.id === childId ? "selected" : ""}"
                          @click="${() => this._selectClaimant(candidate.id)}"
                        >
                          <span>${candidate.name}</span>
                          <span class="points-val">
                            <ha-icon icon="${pointsIcon}" style="--mdc-icon-size:14px;"></ha-icon>
                            ${candidate.points || 0}
                          </span>
                        </button>
                      `,
                    )}
                  </div>
                `
              : ""}

            ${child
              ? html`
                  <div class="dialog-points-summary">
                    <div class="dialog-points-row">
                      <span>${choices.length > 1 ? `${child.name}'s ${pointsName}` : `Your ${pointsName}`}</span>
                      <span class="points-val">
                        <ha-icon icon="${pointsIcon}" style="--mdc-icon-size:14px;"></ha-icon>
                        ${child.points || 0}
                      </span>
                    </div>
                    <div class="dialog-points-row cost">
                      <span>Cost</span>
                      <span class="points-val cost">
                        <ha-icon icon="${pointsIcon}" style="--mdc-icon-size:14px;"></ha-icon>
                        &minus;${cost}
                      </span>
                    </div>
                    <div class="dialog-points-row remaining">
                      <span>Remaining</span>
                      <span class="points-val remaining">
                        <ha-icon icon="${pointsIcon}" style="--mdc-icon-size:14px;"></ha-icon>
                        ${(child.points || 0) - cost}
                      </span>
                    </div>
                  </div>
                `
              : ""}
          </div>
          <div class="dialog-buttons">
            <button class="btn-cancel" @click="${this._cancelRedeem}">Cancel</button>
            <button class="btn-confirm" ?disabled="${!child}" @click="${this._confirmRedeem}">
              Claim!
            </button>
          </div>
        </div>
      </div>
    `;
  }

  async _confirmRedeem() {
    const pending = this._pending;
    if (!this.hass || !pending || !pending.childId) return;

    const { reward, childId } = pending;
    const child = pending.choices.find((c) => c.id === childId);
    const cost = this._getCostForChild(reward, child);

    this._pending = null;
    this._loading = { ...this._loading, [reward.id]: true };

    try {
      // child_id is vol.Required on choremander.claim_reward, and the backend
      // deducts from exactly this child - never send the call without it.
      await this.hass.callService("choremander", "claim_reward", {
        reward_id: reward.id,
        child_id: childId,
      });

      this._celebrating = { name: reward.name, cost };
      this._spawnConfetti();
      this._playClaimSound();

      setTimeout(() => {
        this._celebrating = null;
        this._confetti = [];
      }, 3000);
    } catch (error) {
      console.error("[Choremander] Failed to claim reward:", error);
      this.hass.callService("persistent_notification", "create", {
        title: "Choremander Error",
        message: `Failed to claim ${reward.name}: ${this._describeError(error)}`,
        notification_id: `choremander_claim_error_${reward.id}`,
      });
    } finally {
      this._loading = { ...this._loading, [reward.id]: false };
    }
  }

  /**
   * Home Assistant rejects service calls with a plain `{code, message}` object
   * rather than an Error, and voluptuous failures carry no `message` at all.
   */
  _describeError(error) {
    return (
      error?.message ||
      error?.error?.message ||
      (typeof error === "string" ? error : "") ||
      "unknown error"
    );
  }

  _spawnConfetti() {
    const confetti = [];
    for (let i = 0; i < 50; i++) {
      confetti.push({
        x: Math.random() * 100,
        delay: Math.random() * 0.5,
        size: Math.random() * 8 + 6,
        round: Math.random() > 0.5,
      });
    }
    this._confetti = confetti;
    setTimeout(() => {
      this._confetti = [];
    }, 3500);
  }

  /**
   * Play the configured claim sound.
   *
   * Mirrors the child card's convention: a `claim_sound` config option where
   * "none" silences it, so a dashboard on a shared tablet can stay quiet.
   * Fart sounds are the shipped audio files; everything else is the synthesized
   * fanfare.
   */
  _playClaimSound() {
    const sound = this.config.claim_sound ?? "fanfare";
    if (!sound || sound === "none") return;

    if (sound.startsWith("fart")) {
      const file = sound === "fart_random"
        ? `fart${Math.floor(Math.random() * 10) + 1}.mp3`
        : `${sound}.mp3`;
      try {
        new Audio(`/local/choremander/${file}`).play().catch(() => {});
      } catch (e) {
        console.warn("[Choremander] Error playing sound:", e);
      }
      return;
    }

    this._playFanfareSound();
  }

  _playFanfareSound() {
    try {
      if (!this._audioContext) {
        this._audioContext = new (window.AudioContext || window.webkitAudioContext)();
      }
      if (this._audioContext.state === 'suspended') {
        this._audioContext.resume();
      }
      const ctx = this._audioContext;
      const startTime = ctx.currentTime;
      const masterGain = ctx.createGain();
      masterGain.connect(ctx.destination);
      masterGain.gain.value = 0.2;
      const pattern = [
        { freq: 392.00, duration: 0.1, delay: 0 },
        { freq: 392.00, duration: 0.1, delay: 0.12 },
        { freq: 392.00, duration: 0.15, delay: 0.24 },
        { freq: 329.63, duration: 0.15, delay: 0.42 },
        { freq: 392.00, duration: 0.15, delay: 0.6 },
        { freq: 523.25, duration: 0.4, delay: 0.78 },
      ];
      pattern.forEach(({ freq, duration, delay }) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(masterGain);
        osc.frequency.value = freq;
        osc.type = 'sawtooth';
        const noteStart = startTime + delay;
        gain.gain.setValueAtTime(0.5, noteStart);
        gain.gain.setValueAtTime(0.5, noteStart + duration * 0.8);
        gain.gain.exponentialRampToValueAtTime(0.01, noteStart + duration);
        osc.start(noteStart);
        osc.stop(noteStart + duration + 0.05);
      });
    } catch (e) {
      console.warn('[Choremander] Error playing sound:', e);
    }
  }

  _renderCelebration() {
    const entity = this.hass.states[this.config.entity];
    const pointsIcon = entity?.attributes?.points_icon || "mdi:star";
    // { name, cost } captured at claim time - `cost` is what the claiming child
    // was actually charged, which under dynamic pricing is not `reward.cost`.
    const claimed = this._celebrating;

    return html`
      <div class="celebration-overlay" @click="${() => { this._celebrating = null; }}">
        <div class="celebration-content" @click="${(e) => e.stopPropagation()}">
          <div class="celebration-stars">&#127873;&#127775;&#127873;</div>
          <div class="celebration-title">AWESOME!</div>
          <div class="celebration-message">You claimed <strong>${claimed.name}</strong>!</div>
          <div class="celebration-points">
            <ha-icon icon="${pointsIcon}"></ha-icon>
            &minus;${claimed.cost}
          </div>
        </div>
      </div>
    `;
  }

  _renderConfetti() {
    const colors = ["#ff6b9d", "#9b59b6", "#3498db", "#2ecc71", "#f1c40f", "#e67e22"];
    return html`
      <div class="confetti-container">
        ${this._confetti.map((piece, index) => html`
          <div class="confetti" style="
            left: ${piece.x}%;
            animation-delay: ${piece.delay}s;
            background: ${colors[index % colors.length]};
            border-radius: ${piece.round ? '50%' : '0'};
            width: ${piece.size}px;
            height: ${piece.size}px;
          "></div>
        `)}
      </div>
    `;
  }

  _renderJackpotProgress(reward, childContributions, totalStars, pointsIcon, displayCost) {
    const cost = displayCost || reward.cost;

    // For weighted display: each child's segment shows their progress toward their expected share
    // The meter shows how much of their "responsibility" each child has fulfilled
    const hasWeightedData = childContributions.some(c => c.expectedContribution > 0);

    // Calculate weighted segments - each segment width = (child's share of goal) * (their progress %)
    // This way, if a child has 50% share and is at 100% progress, they fill 50% of the bar
    const segments = childContributions.map((contrib) => {
      let width = 0;
      if (hasWeightedData && contrib.shareOfGoal > 0) {
        // Weighted: segment width = share of goal * progress percentage
        // e.g., 40% share at 50% progress = 20% of bar filled
        width = (contrib.shareOfGoal / 100) * (contrib.weightedProgress / 100) * 100;
      } else {
        // Fallback: raw contribution relative to cost
        width = Math.min((contrib.points / cost) * 100, 100);
      }
      return {
        ...contrib,
        width: width
      };
    });

    // Calculate total percentage for display
    const totalPercentage = Math.min((totalStars / cost) * 100, 100);

    return html`
      <div class="progress-section">
        <div class="progress-bar-container">
          <div class="jackpot-progress-bar">
            ${segments.map((seg) => html`
              <div class="jackpot-segment color-${seg.colorIndex}"
                   style="width: ${seg.width}%"></div>
            `)}
          </div>
          <span class="progress-text">${totalStars}/${cost} <ha-icon icon="${pointsIcon}" style="--mdc-icon-size: 14px;"></ha-icon></span>
        </div>
        <div class="jackpot-breakdown">
          ${childContributions.map((contrib) => html`
            <span class="jackpot-child-contribution" title="${hasWeightedData ? `Expected: ${contrib.expectedContribution}, Progress: ${Math.round(contrib.weightedProgress)}%` : ''}">
              <span class="color-dot color-${contrib.colorIndex}"></span>
              ${contrib.name}: ${hasWeightedData ? html`${Math.round(contrib.weightedProgress)}%` : html`${contrib.points} <ha-icon icon="${pointsIcon}" style="--mdc-icon-size: 12px;"></ha-icon>`}
            </span>
          `)}
          ${childContributions.length > 1 ? html`
            <span class="jackpot-total">${totalStars}/${cost} total</span>
          ` : ''}
        </div>
      </div>
    `;
  }
}

// Card Editor for visual configuration in Lovelace UI
class ChoremanderRewardsCardEditor extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
    };
  }

  static get styles() {
    return css`
      .form-group {
        margin-bottom: 16px;
      }

      .form-group label {
        display: block;
        margin-bottom: 4px;
        font-weight: 500;
      }

      .form-group input {
        width: 100%;
        padding: 8px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font-size: 1em;
        box-sizing: border-box;
      }

      .form-group input[type="text"]:focus,
      .form-group select:focus {
        outline: none;
        border-color: var(--primary-color);
      }

      .form-group select {
        width: 100%;
        padding: 8px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font-size: 1em;
        box-sizing: border-box;
      }

      .form-group label input[type="checkbox"] {
        margin-right: 8px;
      }

      .form-group small {
        display: block;
        margin-top: 4px;
        color: var(--secondary-text-color);
        font-size: 0.85em;
      }
    `;
  }

  setConfig(config) {
    this.config = config;
  }

  render() {
    if (!this.hass || !this.config) {
      return html``;
    }

    // Get children from the entity for the dropdown
    const entity = this.config.entity ? this.hass.states[this.config.entity] : null;
    const children = entity?.attributes?.children || [];

    return html`
      <div class="form-group">
        <label>Entity</label>
        <input
          type="text"
          .value="${this.config.entity || ""}"
          @input="${this._entityChanged}"
          placeholder="sensor.choremander_overview"
        />
        <small>The Choremander overview sensor entity</small>
      </div>

      <div class="form-group">
        <label>Title</label>
        <input
          type="text"
          .value="${this.config.title || ""}"
          @input="${this._titleChanged}"
          placeholder="Rewards"
        />
        <small>Card title displayed in the header (default: "Rewards")</small>
      </div>

      <div class="form-group">
        <label>Filter by Child</label>
        <select @change="${this._childIdChanged}">
          <option value="" ?selected="${!this.config.child_id}">All Children</option>
          ${children.map(
            (child) => html`
              <option value="${child.id}" ?selected="${this.config.child_id === child.id}">
                ${child.name}
              </option>
            `
          )}
        </select>
        <small>Only show rewards available to this child (leave empty for all rewards)</small>
      </div>

      <div class="form-group">
        <label>
          <input
            type="checkbox"
            ?checked="${this.config.show_child_badges !== false}"
            @change="${this._showChildBadgesChanged}"
          />
          Show child assignment badges
        </label>
        <small>Display which children can claim each reward</small>
      </div>
    `;
  }

  _entityChanged(e) {
    this._updateConfig("entity", e.target.value);
  }

  _titleChanged(e) {
    this._updateConfig("title", e.target.value);
  }

  _childIdChanged(e) {
    const value = e.target.value;
    this._updateConfig("child_id", value || null);
  }

  _showChildBadgesChanged(e) {
    this._updateConfig("show_child_badges", e.target.checked);
  }

  _updateConfig(key, value) {
    const newConfig = { ...this.config, [key]: value };
    if (value === undefined || value === "" || value === null) {
      delete newConfig[key];
    }
    const event = new CustomEvent("config-changed", {
      detail: { config: newConfig },
      bubbles: true,
      composed: true,
    });
    this.dispatchEvent(event);
  }
}

// Register the cards
customElements.define("choremander-rewards-card", ChoremanderRewardsCard);
customElements.define("choremander-rewards-card-editor", ChoremanderRewardsCardEditor);

// Register with Home Assistant
window.customCards = window.customCards || [];
window.customCards.push({
  type: "choremander-rewards-card",
  name: "Choremander Rewards Card",
  description: "A card displaying available rewards with star costs",
  preview: true,
});

console.info(
  "%c CHOREMANDER-REWARDS-CARD %c v0.0.7 ",
  "background: #9b59b6; color: white; font-weight: bold; border-radius: 4px 0 0 4px;",
  "background: #f1c40f; color: #333; font-weight: bold; border-radius: 0 4px 4px 0;"
);
