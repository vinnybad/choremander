/**
 * Choremander Streak Card
 * A Lovelace card showing how many days in a row a child has kept going, the
 * recent days they were active, and the milestones they have reached.
 *
 * Streaks come from the integration, which derives them from the approved
 * completion history. The dot strip is drawn from the same source, so the
 * number and the dots can never tell different stories.
 *
 * Version: 0.0.1 - Initial release
 * Last Updated: 2026-08-01
 */

const LitElement = customElements.get("hui-masonry-view")
  ? Object.getPrototypeOf(customElements.get("hui-masonry-view"))
  : Object.getPrototypeOf(customElements.get("hui-view"));

const html = LitElement.prototype.html;
const css = LitElement.prototype.css;

/**
 * The integration publishes the last 30 days of activity per child. Asking for
 * a longer strip than that would draw days as "inactive" purely because they
 * were never sent, which is worse than not drawing them at all.
 */
const MAX_STREAK_DAYS = 30;

/**
 * Milestones, easiest first. Order is what decides which locked one is shown
 * as the next thing to aim for.
 *
 * These are derived entirely from figures the integration already tracks, so
 * there is no separate badge state that could disagree with the chore history.
 */
const ACHIEVEMENTS = [
  { id: "first", emoji: "🌟", label: "First!", hint: "Finish a chore", metric: "chores", goal: 1 },
  { id: "streak3", emoji: "⚡", label: "3 in a row", hint: "A 3 day streak", metric: "best", goal: 3 },
  { id: "ten", emoji: "🏅", label: "10 done", hint: "Finish 10 chores", metric: "chores", goal: 10 },
  { id: "points50", emoji: "🎯", label: "50 earned", hint: "Earn 50", metric: "earned", goal: 50 },
  { id: "streak7", emoji: "🔥", label: "A week!", hint: "A 7 day streak", metric: "best", goal: 7 },
  { id: "fifty", emoji: "🥈", label: "50 done", hint: "Finish 50 chores", metric: "chores", goal: 50 },
  { id: "streak14", emoji: "💫", label: "Two weeks", hint: "A 14 day streak", metric: "best", goal: 14 },
  { id: "points250", emoji: "💰", label: "250 earned", hint: "Earn 250", metric: "earned", goal: 250 },
  { id: "hundred", emoji: "🥇", label: "100 done", hint: "Finish 100 chores", metric: "chores", goal: 100 },
  { id: "streak30", emoji: "💎", label: "A month!", hint: "A 30 day streak", metric: "best", goal: 30 },
];

/** How the current streak is dressed up, longest first. */
const STREAK_TIERS = [
  { min: 14, emoji: "🔥🔥", tone: "blazing" },
  { min: 7, emoji: "🔥", tone: "hot" },
  { min: 3, emoji: "⚡", tone: "warm" },
  { min: 1, emoji: "✨", tone: "starting" },
  { min: 0, emoji: "💤", tone: "resting" },
];

class ChoremanderStreakCard extends LitElement {
  /** Accepted values for the `density` option, loosest first. */
  static DENSITIES = ["default", "cozy", "compact"];

  /** Accepted values for the `achievements` option. */
  static ACHIEVEMENT_MODES = ["next", "earned", "all", "none"];

  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
    };
  }

  static get styles() {
    return css`
      :host {
        display: block;
        --chm-streak-fire: var(--chm-color-orange, #ff6b35);
        /* A darker variant for the streak count, which is text rather than a
           dot: #ff6b35 measures about 2.8:1 on the card background and misses
           even the 3:1 large-text minimum. */
        --chm-streak-fire-text: var(--chm-color-orange-text, #c9431a);
        --chm-streak-today: var(--chm-color-green, #2ecc71);
        --chm-streak-idle: var(--divider-color, #e0e0e0);
        --text-primary: var(--primary-text-color, #212121);
        --text-secondary: var(--secondary-text-color, #757575);
        --divider: var(--divider-color, #e0e0e0);
      }

      ha-card {
        overflow: hidden;
      }

      /* ---------------------------------------------------------- header */

      .card-header {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: var(--chm-header-padding, 16px 20px);
        background: linear-gradient(
          135deg,
          var(--chm-color-orange, #e67e22) 0%,
          var(--chm-color-yellow, #f39c12) 100%
        );
        color: white;
      }

      .header-icon {
        --mdc-icon-size: var(--chm-header-icon-size, 32px);
        opacity: 0.95;
        flex-shrink: 0;
      }

      .header-title {
        font-size: var(--chm-header-title-font-size, 1.4rem);
        font-weight: 700;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      /* ----------------------------------------------------------- child */

      .child {
        padding: var(--chm-streak-child-padding, 16px 20px);
        border-bottom: 1px solid var(--divider);
      }

      .child:last-child {
        border-bottom: none;
      }

      .child-top {
        display: flex;
        align-items: center;
        gap: var(--chm-streak-top-gap, 14px);
      }

      .avatar {
        flex-shrink: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        width: var(--chm-streak-avatar-size, 52px);
        height: var(--chm-streak-avatar-size, 52px);
        border-radius: 50%;
        background: linear-gradient(
          135deg,
          var(--chm-color-purple, #9b59b6) 0%,
          var(--chm-color-pink, #ff6b9d) 100%
        );
        color: white;
        --mdc-icon-size: var(--chm-streak-avatar-glyph-size, 30px);
      }

      .child-headline {
        flex: 1;
        min-width: 0;
      }

      .child-name {
        font-size: var(--chm-streak-name-font-size, 1.1rem);
        font-weight: 700;
        color: var(--text-primary);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .streak-line {
        display: flex;
        align-items: baseline;
        gap: 6px;
        min-width: 0;
        overflow: hidden;
        margin-top: 2px;
        color: var(--text-secondary);
        font-size: var(--chm-streak-sub-font-size, 0.9rem);
      }

      .streak-count {
        font-size: var(--chm-streak-count-font-size, 1.9rem);
        font-weight: 800;
        line-height: 1;
        font-variant-numeric: tabular-nums;
        color: var(--text-primary);
      }

      .streak-count.blazing,
      .streak-count.hot {
        color: var(--chm-streak-fire-text);
      }

      .streak-emoji {
        font-size: var(--chm-streak-emoji-font-size, 1.2rem);
      }

      .best {
        flex-shrink: 0;
        text-align: right;
        color: var(--text-secondary);
        font-size: var(--chm-streak-sub-font-size, 0.9rem);
      }

      .best strong {
        display: block;
        color: var(--text-primary);
        font-size: var(--chm-streak-best-font-size, 1.1rem);
        font-variant-numeric: tabular-nums;
      }

      /* ------------------------------------------------------------ dots */

      /* The strip reads left to right as a timeline, so it must stay one row:
         wrapping turns it into a grid with no row boundary and moves today away
         from the right edge. A 30-day strip does not fit a narrow card at full
         size, so the dots shrink instead - see .dot's flex below. */
      .dots {
        display: flex;
        align-items: center;
        gap: var(--chm-streak-dot-gap, 5px);
        margin-top: var(--chm-streak-dots-margin-top, 14px);
        flex-wrap: nowrap;
      }

      .dot {
        /* border-box because .dot.today-idle adds a border, and a shadow root
           has no reset - content-box would render that dot 4px wider than the
           rest of the strip and break its cadence. */
        box-sizing: border-box;
        width: var(--chm-streak-dot-size, 12px);
        height: var(--chm-streak-dot-size, 12px);
        border-radius: 50%;
        background: var(--chm-streak-idle);
        /* Shrink rather than wrap or overflow, down to a floor where a dot is
           still visible. aspect-ratio keeps them round as they narrow. */
        flex: 0 1 var(--chm-streak-dot-size, 12px);
        min-width: 4px;
        aspect-ratio: 1;
      }

      .dot.active {
        background: var(--chm-streak-fire);
      }

      /* Today carries a ring as well as a colour. Orange and green differ by
         about 1.35:1 in luminance and sit on the axis red-green colourblindness
         collapses, so hue alone cannot carry "did I do it today?". */
      .dot.today {
        background: var(--chm-streak-today);
        box-shadow: 0 0 0 2px var(--card-background-color, #fff),
          0 0 0 4px var(--chm-streak-today);
        transform: scale(1.15);
      }

      /* An inactive today is worth distinguishing from a day already missed:
         there is still time to fix it. */
      .dot.today-idle {
        background: transparent;
        border: 2px solid var(--chm-streak-today);
      }

      /* ---------------------------------------------------- achievements */

      .achievements-label {
        margin: var(--chm-streak-achievements-label-margin, 16px 0 8px);
        font-size: var(--chm-streak-achievements-label-font-size, 0.72rem);
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-secondary);
      }

      .achievements {
        display: flex;
        gap: var(--chm-streak-badge-gap, 8px);
        flex-wrap: wrap;
      }

      .badge {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 2px;
        min-width: var(--chm-streak-badge-size, 66px);
        padding: var(--chm-streak-badge-padding, 8px 6px);
        border: 1px solid var(--chm-streak-badge-border, #f1c40f);
        border-radius: var(--chm-streak-badge-radius, 12px);
        background: var(--chm-streak-badge-background, linear-gradient(160deg, #fff9e6 0%, #fdefc4 100%));
      }

      /* Not opacity on the container: at 0.5 the label lands near 1.9:1, and in
         the default "next" mode the locked badge is the single most important
         thing on the card - it is what the child is being asked to aim for. */
      .badge.locked {
        border-color: var(--divider);
        border-style: dashed;
        background: transparent;
      }

      .badge-emoji {
        font-size: var(--chm-streak-badge-emoji-size, 1.5rem);
        line-height: 1.1;
      }

      .badge.locked .badge-emoji {
        filter: grayscale(1);
        opacity: 0.55;
      }

      .badge-label {
        font-size: var(--chm-streak-badge-label-size, 0.68rem);
        font-weight: 700;
        color: var(--chm-streak-badge-label-color, #6b5310);
        text-align: center;
      }

      .badge.locked .badge-label {
        color: var(--text-secondary);
      }

      /* --------------------------------------------------------- empty */

      .empty {
        padding: 32px 20px;
        text-align: center;
        color: var(--text-secondary);
      }

      .empty ha-icon {
        --mdc-icon-size: 44px;
        opacity: 0.35;
      }

      .empty-text {
        margin-top: 10px;
        font-size: 0.95rem;
      }

      .error {
        padding: 16px 20px;
        color: var(--chm-color-red, #e74c3c);
      }

      /* --------------------------------------------------------- density */

      :host([density="cozy"]) {
        --chm-header-padding: 12px 16px;
        --chm-header-icon-size: 28px;
        --chm-header-title-font-size: 1.2rem;
        --chm-streak-child-padding: 12px 16px;
        --chm-streak-avatar-size: 44px;
        --chm-streak-avatar-glyph-size: 26px;
        --chm-streak-count-font-size: 1.6rem;
        --chm-streak-dots-margin-top: 10px;
        --chm-streak-achievements-label-margin: 12px 0 6px;
        --chm-streak-badge-size: 58px;
      }

      :host([density="compact"]) {
        --chm-header-padding: 8px 12px;
        --chm-header-icon-size: 22px;
        --chm-header-title-font-size: 1.05rem;
        --chm-streak-child-padding: 10px 12px;
        --chm-streak-top-gap: 10px;
        --chm-streak-avatar-size: 36px;
        --chm-streak-avatar-glyph-size: 22px;
        --chm-streak-name-font-size: 0.95rem;
        --chm-streak-sub-font-size: 0.78rem;
        --chm-streak-count-font-size: 1.35rem;
        --chm-streak-best-font-size: 0.95rem;
        --chm-streak-emoji-font-size: 1rem;
        --chm-streak-dot-size: 9px;
        --chm-streak-dot-gap: 4px;
        --chm-streak-dots-margin-top: 8px;
        --chm-streak-achievements-label-margin: 10px 0 5px;
        --chm-streak-achievements-label-font-size: 0.66rem;
        --chm-streak-badge-size: 48px;
        --chm-streak-badge-padding: 5px 4px;
        --chm-streak-badge-radius: 9px;
        --chm-streak-badge-gap: 6px;
        --chm-streak-badge-emoji-size: 1.15rem;
        --chm-streak-badge-label-size: 0.6rem;
      }

      /* --------------------------------------------------- reduced motion */

      :host([no-animations]) .dot.today {
        transform: none;
      }

      /* Only the scale goes. The ring above is what actually identifies today,
         and a request for less motion is not a request for less information. */
      @media (prefers-reduced-motion: reduce) {
        .dot.today {
          transform: none;
        }
      }
    `;
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error("Please define an entity (choremander streaks sensor)");
    }
    this.config = {
      title: "Streaks",
      child_id: null, // Optional: one child instead of the whole family
      streak_days: 14, // Days in the dot strip
      achievements: "next", // "next" | "earned" | "all" | "none"
      show_avatar: true,
      density: "default", // "default" | "cozy" | "compact"
      animations: true,
      ...config,
    };

    if (!ChoremanderStreakCard.DENSITIES.includes(this.config.density)) {
      // Coerce rather than throw - one mistyped option should not replace the
      // whole card with an error.
      console.warn(
        `[Choremander] Unknown density "${this.config.density}". ` +
          `Expected one of ${ChoremanderStreakCard.DENSITIES.join(", ")}. Falling back to "default".`
      );
      this.config = { ...this.config, density: "default" };
    }

    if (!ChoremanderStreakCard.ACHIEVEMENT_MODES.includes(this.config.achievements)) {
      console.warn(
        `[Choremander] Unknown achievements mode "${this.config.achievements}". ` +
          `Expected one of ${ChoremanderStreakCard.ACHIEVEMENT_MODES.join(", ")}. ` +
          `Falling back to "next".`
      );
      this.config = { ...this.config, achievements: "next" };
    }

    // Clamped rather than rejected: the integration only publishes 30 days of
    // activity, and drawing beyond that would render days as missed purely
    // because they were never sent.
    const requested = Math.floor(Number(this.config.streak_days));
    this.config = {
      ...this.config,
      streak_days: Number.isFinite(requested)
        ? Math.max(1, Math.min(MAX_STREAK_DAYS, requested))
        : 14,
    };

    this._syncLayoutAttributes();
  }

  /**
   * Mirror the layout options onto the host, which is what the :host([...])
   * rules select on. Attributes are removed at their default rather than set
   * to "default", so a card that never opted in matches none of those rules.
   */
  _syncLayoutAttributes() {
    if (!this.config) return;

    if (this.config.density && this.config.density !== "default") {
      this.setAttribute("density", this.config.density);
    } else {
      this.removeAttribute("density");
    }

    if (this.config.animations === false) {
      this.setAttribute("no-animations", "");
    } else {
      this.removeAttribute("no-animations");
    }
  }

  connectedCallback() {
    super.connectedCallback();
    // setConfig can run before the element is in the document, and Lovelace
    // reuses card elements across view switches, so re-assert on connect.
    this._syncLayoutAttributes();
  }

  getCardSize() {
    const sizes = { default: 4, cozy: 3, compact: 2 };
    return sizes[this.config?.density] ?? 4;
  }

  static getConfigElement() {
    return document.createElement("choremander-streak-card-editor");
  }

  static getStubConfig() {
    return {
      entity: "sensor.choremander_streaks",
      title: "Streaks",
      child_id: null,
      streak_days: 14,
    };
  }

  // ------------------------------------------------------------- helpers

  /** The Home Assistant timezone, falling back to the browser's. */
  get _timeZone() {
    return (
      this.hass?.config?.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone
    );
  }

  /**
   * The day keys the strip covers, oldest first, ending today.
   *
   * en-CA formats as YYYY-MM-DD, matching the format the integration publishes
   * `active_days` in, so the two can be compared directly. Working from a
   * midday timestamp keeps a daylight-saving change from shifting a day.
   */
  _dayWindow(count) {
    const todayKey = new Date().toLocaleDateString("en-CA", { timeZone: this._timeZone });
    const cursor = new Date(`${todayKey}T12:00:00`);

    const days = [];
    for (let offset = count - 1; offset >= 0; offset--) {
      const day = new Date(cursor);
      day.setDate(day.getDate() - offset);
      // No timeZone here, deliberately, and this is load-bearing. `cursor` is
      // already the Home Assistant calendar date reinterpreted as browser-local
      // midday, so setDate and this format call are pure calendar arithmetic
      // and the round trip cancels. Adding `{ timeZone }` to "match _dayKey"
      // would convert an already-converted date: with a large enough offset
      // every key shifts by a day, nothing matches active_days, and the whole
      // strip silently reads as inactive.
      days.push(day.toLocaleDateString("en-CA"));
    }
    return days;
  }

  _tier(streak) {
    return STREAK_TIERS.find((tier) => streak >= tier.min) || STREAK_TIERS[STREAK_TIERS.length - 1];
  }

  /** Which milestones to draw, given the child's figures and the config. */
  _achievementsFor(stats) {
    const mode = this.config.achievements;
    if (mode === "none") return [];

    const scored = ACHIEVEMENTS.map((achievement) => {
      const progress = (stats[achievement.metric] || 0) / achievement.goal;
      return { ...achievement, earned: progress >= 1, progress };
    });

    if (mode === "all") return scored;

    const earned = scored.filter((achievement) => achievement.earned);
    if (mode === "earned") return earned;

    // "next" - everything earned, plus the one they are genuinely closest to.
    //
    // Picking the first locked badge in list order instead would offer a child
    // one chore short of "10 done" and one point short of "50 earned" a three
    // day streak they have not started, which is the opposite of encouraging.
    // Progress is compared as a fraction of the goal because the ladders count
    // different things.
    const locked = scored.filter((achievement) => !achievement.earned);
    if (!locked.length) return earned;
    const nextUp = locked.reduce((closest, candidate) =>
      candidate.progress > closest.progress ? candidate : closest
    );
    return [...earned, nextUp];
  }

  _childrenToShow(entity) {
    const children = entity.attributes.children || [];
    if (!this.config.child_id) return children;
    return children.filter((child) => child.id === this.config.child_id);
  }

  // -------------------------------------------------------------- render

  render() {
    if (!this.hass || !this.config) {
      return html``;
    }

    const entity = this.hass.states[this.config.entity];
    if (!entity) {
      return html`
        <ha-card>
          <div class="error">Entity not found: ${this.config.entity}</div>
        </ha-card>
      `;
    }

    const children = this._childrenToShow(entity);

    return html`
      <ha-card>
        <div class="card-header">
          <ha-icon class="header-icon" icon="mdi:fire"></ha-icon>
          <div class="header-title">${this.config.title}</div>
        </div>
        ${children.length
          ? children.map((child) => this._renderChild(child))
          : this._renderEmpty()}
      </ha-card>
    `;
  }

  _renderChild(child) {
    const streak = child.current_streak || 0;
    const best = child.best_streak || 0;
    const tier = this._tier(streak);

    const stats = {
      chores: child.total_chores_completed || 0,
      earned: child.total_points_earned || 0,
      best,
    };
    const achievements = this._achievementsFor(stats);

    return html`
      <div class="child" data-child-id="${child.id}">
        <div class="child-top">
          ${this.config.show_avatar !== false
            ? html`<div class="avatar">
                <ha-icon icon="${child.avatar || "mdi:account-circle"}"></ha-icon>
              </div>`
            : ""}
          <div class="child-headline">
            <div class="child-name">${child.name || "Someone"}</div>
            <div class="streak-line">
              <span class="streak-count ${tier.tone}">${streak}</span>
              <span>day streak</span>
              <span class="streak-emoji">${tier.emoji}</span>
            </div>
          </div>
          <div class="best">
            best
            <strong>${best}</strong>
          </div>
        </div>

        ${this._renderDots(child)}
        ${achievements.length
          ? html`
              <div class="achievements-label">Achievements</div>
              <div class="achievements">
                ${achievements.map(
                  (achievement) => html`
                    <div
                      class="badge ${achievement.earned ? "earned" : "locked"}"
                      title="${achievement.earned ? achievement.label : achievement.hint}"
                    >
                      <span class="badge-emoji">${achievement.emoji}</span>
                      <span class="badge-label">${achievement.label}</span>
                    </div>
                  `
                )}
              </div>
            `
          : ""}
      </div>
    `;
  }

  _renderDots(child) {
    const active = new Set(child.active_days || []);
    const days = this._dayWindow(this.config.streak_days);
    const todayKey = days[days.length - 1];

    return html`
      <div class="dots" role="img" aria-label="${this._dotsLabel(child, days, active)}">
        ${days.map((day) => {
          const isToday = day === todayKey;
          const isActive = active.has(day);
          let cls = "dot";
          if (isToday && isActive) cls += " today";
          else if (isToday) cls += " today-idle";
          else if (isActive) cls += " active";
          return html`<div class="${cls}" title="${day}"></div>`;
        })}
      </div>
    `;
  }

  _dotsLabel(child, days, active) {
    const count = days.filter((day) => active.has(day)).length;
    return `${child.name || "This child"} was active on ${count} of the last ${days.length} days`;
  }

  _renderEmpty() {
    return html`
      <div class="empty">
        <ha-icon icon="mdi:fire-off"></ha-icon>
        <div class="empty-text">
          ${this.config.child_id
            ? "That child was not found."
            : "No children yet. Add one in the Choremander settings."}
        </div>
      </div>
    `;
  }
}

// ---------------------------------------------------------------- editor

class ChoremanderStreakCardEditor extends LitElement {
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

      .form-group input,
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

      .form-group input[type="text"]:focus,
      .form-group input[type="number"]:focus,
      .form-group select:focus {
        outline: none;
        border-color: var(--primary-color);
      }

      .form-group label input[type="checkbox"] {
        width: auto;
        margin-right: 8px;
      }

      .form-group small {
        display: block;
        margin-top: 4px;
        color: var(--secondary-text-color);
        font-size: 0.85em;
      }

      .section-heading {
        margin: 24px 0 12px;
        padding-top: 12px;
        border-top: 1px solid var(--divider-color);
        font-weight: 600;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.5px;
        font-size: 0.8em;
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

    const entity = this.config.entity ? this.hass.states[this.config.entity] : null;
    const children = entity?.attributes?.children || [];

    return html`
      <div class="form-group">
        <label>Entity</label>
        <input
          type="text"
          .value="${this.config.entity || ""}"
          @input="${(e) => this._updateConfig("entity", e.target.value)}"
          placeholder="sensor.choremander_streaks"
        />
        <small>The Choremander streaks sensor entity</small>
      </div>

      <div class="form-group">
        <label>Title</label>
        <input
          type="text"
          .value="${this.config.title || ""}"
          @input="${(e) => this._updateConfig("title", e.target.value)}"
          placeholder="Streaks"
        />
        <small>Card title displayed in the header (default: "Streaks")</small>
      </div>

      <div class="form-group">
        <label>Child</label>
        <select @change="${(e) => this._updateConfig("child_id", e.target.value || null)}">
          <option value="" ?selected="${!this.config.child_id}">Everyone</option>
          ${children.map(
            (child) => html`
              <option value="${child.id}" ?selected="${this.config.child_id === child.id}">
                ${child.name}
              </option>
            `
          )}
        </select>
        <small>Show one child, or the whole family stacked</small>
      </div>

      <div class="form-group">
        <label>Days in the strip</label>
        <input
          type="number"
          min="1"
          max="30"
          .value="${this.config.streak_days ?? 14}"
          @input="${(e) => this._numberChanged("streak_days", e.target.value, 1, MAX_STREAK_DAYS, 14)}"
        />
        <small>
          How many recent days the dots cover. The integration keeps
          ${MAX_STREAK_DAYS}.
        </small>
      </div>

      <div class="form-group">
        <label>Achievements</label>
        <select @change="${(e) => this._updateConfig("achievements", e.target.value)}">
          <option value="next" ?selected="${(this.config.achievements || "next") === "next"}">
            Earned, plus the next one to aim for
          </option>
          <option value="earned" ?selected="${this.config.achievements === "earned"}">
            Only what has been earned
          </option>
          <option value="all" ?selected="${this.config.achievements === "all"}">
            The whole ladder
          </option>
          <option value="none" ?selected="${this.config.achievements === "none"}">
            Hide them
          </option>
        </select>
        <small>Milestones are worked out from chores done, points earned and best streak</small>
      </div>

      <div class="section-heading">Layout</div>

      <div class="form-group">
        <label>
          <input
            type="checkbox"
            ?checked="${this.config.show_avatar !== false}"
            @change="${(e) => this._updateConfig("show_avatar", e.target.checked)}"
          />
          Show avatars
        </label>
      </div>

      <div class="form-group">
        <label>Density</label>
        <select @change="${(e) => this._updateConfig("density", e.target.value)}">
          <option value="default" ?selected="${(this.config.density || "default") === "default"}">
            Default
          </option>
          <option value="cozy" ?selected="${this.config.density === "cozy"}">
            Cozy — a bit tighter
          </option>
          <option value="compact" ?selected="${this.config.density === "compact"}">
            Compact — fits more children on screen
          </option>
        </select>
        <small>How much space each child takes up</small>
      </div>

      <div class="form-group">
        <label>
          <input
            type="checkbox"
            ?checked="${this.config.animations !== false}"
            @change="${(e) => this._updateConfig("animations", e.target.checked)}"
          />
          Emphasise today
        </label>
        <small>Scales up today's dot. Turn off for a perfectly even strip.</small>
      </div>
    `;
  }

  /**
   * Write a number the card will actually honour.
   *
   * The card clamps `streak_days` to the window the integration publishes, but
   * the editor is handed the raw Lovelace config rather than the card's clamped
   * copy - so without this a value of 50 stays 50 in the YAML and in this field
   * while the preview draws 30 dots, with nothing to explain the difference.
   */
  _numberChanged(key, raw, min, max, fallback) {
    // An empty field means "back to the default"; a typed 0 is a number, and
    // clamps to the minimum. Treating them the same would make 0 silently
    // produce the maximum, which is the opposite of what was asked for.
    if (String(raw).trim() === "") {
      this._updateConfig(key, fallback);
      return;
    }
    const parsed = Math.floor(Number(raw));
    const value = Number.isFinite(parsed) ? Math.max(min, Math.min(max, parsed)) : fallback;
    this._updateConfig(key, value);
  }

  /** Values the card already assumes; writing them down would just add noise. */
  static LAYOUT_DEFAULTS = {
    density: "default",
    animations: true,
    show_avatar: true,
    achievements: "next",
    streak_days: 14,
  };

  _updateConfig(key, value) {
    const newConfig = { ...this.config, [key]: value };
    const isDefault = ChoremanderStreakCardEditor.LAYOUT_DEFAULTS[key] === value;
    if (value === undefined || value === "" || value === null || isDefault) {
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
customElements.define("choremander-streak-card", ChoremanderStreakCard);
customElements.define("choremander-streak-card-editor", ChoremanderStreakCardEditor);

// Register with Home Assistant
window.customCards = window.customCards || [];
window.customCards.push({
  type: "choremander-streak-card",
  name: "Choremander Streak Card",
  description: "Day streaks, recent activity and milestones for each child",
  preview: true,
});

console.info(
  "%c CHOREMANDER-STREAK-CARD %c v0.0.1 ",
  "background: #e67e22; color: white; font-weight: bold; border-radius: 4px 0 0 4px;",
  "background: #f1c40f; color: #333; font-weight: bold; border-radius: 0 4px 4px 0;"
);
