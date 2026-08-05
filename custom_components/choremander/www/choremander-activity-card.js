/**
 * Choremander Activity Card
 * A Lovelace card showing what has actually been happening: chores done and
 * approved, rewards claimed, and the bonuses and penalties a parent handed out.
 *
 * Every row is driven by the structured `event_type` the integration stores,
 * never by parsing a sentence. That is what lets the wording here change, or be
 * translated, without rewriting stored history.
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
 * How each stored event type is presented.
 *
 * `group` drives the filter chips, `tone` picks the colour, `icon` the glyph.
 * An event type absent from this map still renders - see _presentation - so a
 * card from an older release does not blank out when the integration starts
 * recording something new.
 */
const EVENT_PRESENTATION = {
  chore_approved: { icon: "mdi:check-circle", tone: "good", group: "chores" },
  chore_pending: { icon: "mdi:clock-outline", tone: "pending", group: "chores" },
  chore_rejected: { icon: "mdi:close-circle", tone: "bad", group: "chores" },
  reward_claimed: { icon: "mdi:gift", tone: "reward", group: "rewards" },
  reward_refunded: { icon: "mdi:gift-open", tone: "pending", group: "rewards" },
  points_added: { icon: "mdi:star-plus", tone: "good", group: "adjustments" },
  points_removed: { icon: "mdi:star-minus", tone: "bad", group: "adjustments" },
};

/**
 * The integration publishes at most this many events (ACTIVITY_FEED_LIMIT in
 * const.py). Asking for more cannot show more, so the option is clamped here
 * rather than quietly doing nothing.
 */
const MAX_FEED_ITEMS = 40;

const FILTERS = [
  { id: "all", label: "All" },
  { id: "chores", label: "Chores" },
  { id: "rewards", label: "Rewards" },
  { id: "adjustments", label: "Bonuses" },
];

class ChoremanderActivityCard extends LitElement {
  /** Accepted values for the `density` option, loosest first. */
  static DENSITIES = ["default", "cozy", "compact"];

  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
      _filter: { type: String },
    };
  }

  constructor() {
    super();
    this._filter = "all";
  }

  static get styles() {
    return css`
      :host {
        display: block;
        /* Fills, matching the palette the other cards define. A theme that
           sets --chm-color-* recolours every card the same way. */
        --chm-activity-good: var(--chm-color-green, #2ecc71);
        --chm-activity-bad: var(--chm-color-red, #e74c3c);
        --chm-activity-pending: var(--chm-color-orange, #e67e22);
        --chm-activity-reward: var(--chm-color-purple, #9b59b6);
        --chm-activity-blue: var(--chm-color-blue, #3498db);
        /* Darker variants for the same colours used as *text*. The fills above
           carry white glyphs and are chosen for that; at 0.95rem on the card
           background they fall well under the 4.5:1 contrast minimum. */
        --chm-activity-good-text: var(--chm-color-green-text, #1b7a45);
        --chm-activity-bad-text: var(--chm-color-red-text, #c62828);
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
        justify-content: space-between;
        gap: 12px;
        padding: var(--chm-header-padding, 16px 20px);
        background: linear-gradient(
          135deg,
          var(--chm-activity-blue) 0%,
          var(--chm-activity-blue-light, #5dade2) 100%
        );
        color: white;
      }

      .header-content {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
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

      .header-count {
        flex-shrink: 0;
        padding: 4px 12px;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.25);
        font-size: 0.85rem;
        font-weight: 600;
        white-space: nowrap;
      }

      /* --------------------------------------------------------- filters */

      .filters {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        padding: var(--chm-activity-filters-padding, 12px 20px 4px);
      }

      .chip {
        border: 1px solid var(--divider);
        border-radius: 999px;
        background: transparent;
        color: var(--text-secondary);
        /* The 44px WCAG 2.5.5 / Apple HIG minimum, same as the child card's
           checkbox. These are the only controls on the card; making them
           smaller than a fingertip to save a few pixels is the wrong trade. */
        min-height: 44px;
        padding: 0 16px;
        font-size: var(--chm-activity-chip-font-size, 0.85rem);
        font-weight: 600;
        font-family: inherit;
        cursor: pointer;
        transition: background 0.15s ease, color 0.15s ease;
      }

      /* Hover must not look like selection, or hovering an unselected chip
         leaves two chips highlighted and no way to tell which is active -
         which persists after a tap on touch, where :hover sticks. */
      .chip:hover {
        border-color: var(--chm-activity-blue);
        color: var(--chm-activity-blue);
      }

      .chip[aria-pressed="true"] {
        background: var(--chm-activity-blue);
        border-color: var(--chm-activity-blue);
        color: white;
      }

      .chip:focus-visible {
        outline: 2px solid var(--chm-activity-blue);
        outline-offset: 2px;
      }

      /* ----------------------------------------------------------- feed */

      .feed {
        padding: var(--chm-activity-feed-padding, 8px 20px 16px);
      }

      .day-label {
        margin: var(--chm-activity-day-label-margin, 16px 0 8px);
        font-size: var(--chm-activity-day-label-font-size, 0.75rem);
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--text-secondary);
      }

      .day-label:first-child {
        margin-top: 4px;
      }

      .event {
        display: flex;
        align-items: center;
        gap: var(--chm-activity-event-gap, 12px);
        padding: var(--chm-activity-event-padding, 10px 0);
        min-height: var(--chm-activity-event-min-height, 56px);
        border-bottom: 1px solid var(--divider);
      }

      .event:last-child {
        border-bottom: none;
      }

      .event-icon {
        flex-shrink: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        width: var(--chm-activity-icon-size, 40px);
        height: var(--chm-activity-icon-size, 40px);
        border-radius: 50%;
        color: white;
        --mdc-icon-size: var(--chm-activity-icon-glyph-size, 22px);
      }

      .event-icon.good {
        background: var(--chm-activity-good);
      }
      .event-icon.bad {
        background: var(--chm-activity-bad);
      }
      .event-icon.pending {
        background: var(--chm-activity-pending);
      }
      .event-icon.reward {
        background: var(--chm-activity-reward);
      }
      .event-icon.neutral {
        background: var(--text-secondary);
      }

      .event-body {
        flex: 1;
        min-width: 0;
      }

      .event-text {
        font-size: var(--chm-activity-text-font-size, 0.95rem);
        color: var(--text-primary);
        line-height: 1.35;
      }

      .event-text .who {
        font-weight: 700;
      }

      .event-text .what {
        font-weight: 600;
      }

      .event-meta {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        margin-top: 2px;
        font-size: var(--chm-activity-meta-font-size, 0.78rem);
        color: var(--text-secondary);
      }

      .event-reason {
        font-style: italic;
      }

      /* Dark on light amber rather than white on amber: white on the fill
         colour measures about 2.2:1, and this is small text. */
      .tag {
        padding: 1px 8px;
        border-radius: 999px;
        background: var(--chm-activity-tag-background, #ffeccc);
        color: var(--chm-activity-tag-color, #7a4b00);
        font-weight: 600;
        font-style: normal;
      }

      .points {
        flex-shrink: 0;
        display: flex;
        align-items: center;
        gap: 3px;
        font-size: var(--chm-activity-points-font-size, 0.95rem);
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        --mdc-icon-size: var(--chm-activity-points-icon-size, 16px);
      }

      .points.up {
        color: var(--chm-activity-good-text);
      }
      .points.down {
        color: var(--chm-activity-bad-text);
      }
      .points.at-stake {
        color: var(--text-secondary);
        font-weight: 600;
      }

      /* ------------------------------------------------------ empty/error */

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
        color: var(--chm-activity-bad);
      }

      /* --------------------------------------------------------- density */

      :host([density="cozy"]) {
        --chm-header-padding: 12px 16px;
        --chm-header-icon-size: 28px;
        --chm-header-title-font-size: 1.2rem;
        --chm-activity-filters-padding: 10px 16px 2px;
        --chm-activity-feed-padding: 6px 16px 12px;
        --chm-activity-event-padding: 8px 0;
        --chm-activity-event-min-height: 52px;
        --chm-activity-icon-size: 36px;
        --chm-activity-icon-glyph-size: 20px;
        --chm-activity-text-font-size: 0.9rem;
        --chm-activity-day-label-margin: 12px 0 6px;
      }

      :host([density="compact"]) {
        --chm-header-padding: 8px 12px;
        --chm-header-icon-size: 22px;
        --chm-header-title-font-size: 1.05rem;
        --chm-activity-filters-padding: 8px 12px 0;
        --chm-activity-chip-font-size: 0.78rem;
        --chm-activity-feed-padding: 4px 12px 10px;
        --chm-activity-event-gap: 10px;
        --chm-activity-event-padding: 6px 0;
        /* Kept at 44px even though the rows are not interactive: a feed whose
           lines are thinner than a fingertip is hard to track with a thumb
           while scrolling. */
        --chm-activity-event-min-height: 44px;
        --chm-activity-icon-size: 30px;
        --chm-activity-icon-glyph-size: 17px;
        --chm-activity-text-font-size: 0.85rem;
        --chm-activity-meta-font-size: 0.72rem;
        --chm-activity-points-font-size: 0.85rem;
        --chm-activity-day-label-margin: 10px 0 4px;
        --chm-activity-day-label-font-size: 0.68rem;
      }

      /* One line for the sentence at compact. The meta line beneath it can
         still wrap when a long reason accompanies a pending chore. */
      :host([density="compact"]) .event-text {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      /* --------------------------------------------------- reduced motion */

      :host([no-animations]) .chip {
        transition: none;
      }

      @media (prefers-reduced-motion: reduce) {
        .chip {
          transition: none;
        }
      }
    `;
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error("Please define an entity (choremander activity sensor)");
    }
    this.config = {
      title: "Activity",
      child_id: null, // Optional: show only one child's activity
      max_items: 20,
      show_filters: true,
      show_relative_time: true,
      density: "default", // "default" | "cozy" | "compact"
      animations: true,
      ...config,
    };

    if (!ChoremanderActivityCard.DENSITIES.includes(this.config.density)) {
      // Coerce rather than throw - one mistyped option should not replace the
      // whole feed with an error card.
      console.warn(
        `[Choremander] Unknown density "${this.config.density}". ` +
          `Expected one of ${ChoremanderActivityCard.DENSITIES.join(", ")}. Falling back to "default".`
      );
      this.config = { ...this.config, density: "default" };
    }

    // Clamped rather than rejected. A negative value is the dangerous one:
    // slice(0, -5) drops the newest five instead of keeping them, so the option
    // silently inverts. The integration publishes at most ACTIVITY_FEED_LIMIT
    // events, and asking for more than that cannot show more.
    const requested = Math.floor(Number(this.config.max_items));
    this.config = {
      ...this.config,
      max_items: Number.isFinite(requested)
        ? Math.max(1, Math.min(MAX_FEED_ITEMS, requested))
        : 20,
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
    const sizes = { default: 5, cozy: 4, compact: 3 };
    return sizes[this.config?.density] ?? 5;
  }

  static getConfigElement() {
    return document.createElement("choremander-activity-card-editor");
  }

  static getStubConfig() {
    return {
      entity: "sensor.choremander_activity",
      title: "Activity",
      child_id: null,
      max_items: 20,
    };
  }

  /**
   * The attributes this card reads.
   *
   * Every read below falls back to an empty list, so a card aimed at a
   * real-but-wrong entity draws the same "nothing here" state as one that is
   * working and genuinely has nothing to show. Declaring what the card needs is
   * what lets `_missingData` tell those two apart (issue #5).
   *
   * A `name[].field` entry means the attribute must be a list whose entries
   * carry that field.
   */
  static get requiredData() {
    return ["events"];
  }

  /** Which of `requiredData` this entity does not carry. */
  _missingData(entity) {
    // A restarting integration republishes its entities as unavailable with no
    // attributes at all. That is a moment, not a misconfiguration.
    if (entity.state === "unavailable" || entity.state === "unknown") {
      return [];
    }

    return this.constructor.requiredData.filter((path) => {
      const [name, field] = path.split("[].");
      const value = entity.attributes[name];

      if (value === undefined) return true;
      if (!field) return false;
      if (!Array.isArray(value)) return true;

      // An empty list carries no evidence either way; do not accuse it.
      return value.some((item) => !item || item[field] === undefined);
    });
  }

  _renderConfigError(missing) {
    return html`
      <ha-card>
        <div class="error config-error">
          <div>${this.config.entity} has no ${missing.join(", ")}</div>
          <div>This card reads ${this.constructor.getStubConfig().entity}</div>
        </div>
      </ha-card>
    `;
  }

  // ------------------------------------------------------------- helpers

  /** The Home Assistant timezone, falling back to the browser's. */
  get _timeZone() {
    return (
      this.hass?.config?.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone
    );
  }

  /**
   * A YYYY-MM-DD key for a date, in the house's timezone.
   *
   * en-CA formats as YYYY-MM-DD, which sorts correctly as a string and makes a
   * stable map key. Everything that buckets by day goes through here so the
   * grouping and the displayed time can never disagree about which day it is.
   */
  _dayKey(date) {
    return date.toLocaleDateString("en-CA", { timeZone: this._timeZone });
  }

  _formatTime(date) {
    return date.toLocaleTimeString(undefined, {
      timeZone: this._timeZone,
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  /** "just now" / "12m ago" / "3h ago", or null once it stops being useful. */
  _formatRelative(date) {
    const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
    if (seconds < 0) return null;
    if (seconds < 60) return "just now";
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return null;
  }

  /** Presentation for an event type, with a usable fallback for unknown ones. */
  _presentation(eventType) {
    return (
      EVENT_PRESENTATION[eventType] || {
        icon: "mdi:circle-medium",
        tone: "neutral",
        // A group no chip claims. Filing an unrecognised event under "Bonuses"
        // would make a future event type masquerade as a points adjustment.
        group: "other",
      }
    );
  }

  /**
   * The sentence for a row, built from the event type and the event's own
   * fields. Nothing here parses stored text.
   */
  _describe(event) {
    const who = html`<span class="who">${event.child_name || "Someone"}</span>`;
    const chore = event.chore_name
      ? html`<span class="what">${event.chore_name}</span>`
      : html`<span class="what">a chore</span>`;
    const reward = event.reward_name
      ? html`<span class="what">${event.reward_name}</span>`
      : html`<span class="what">a reward</span>`;

    switch (event.event_type) {
      case "chore_approved":
        return html`${who} completed ${chore}`;
      case "chore_pending":
        return html`${who} completed ${chore}`;
      case "chore_rejected":
        return html`${chore} was sent back to ${who}`;
      case "reward_claimed":
        return html`${who} claimed ${reward}`;
      case "reward_refunded":
        return html`${reward} was returned to ${who}`;
      case "points_added":
        return html`${who} got a bonus`;
      // Deliberately not "lost some ${points_name}": a family that renames the
      // currency ends up with sentences like "lost some Bucks". The amount is
      // already on the row, so the sentence only has to say what kind of event
      // it was.
      case "points_removed":
        return html`${who} had a penalty`;
      default:
        return html`${who} - ${event.event_type}`;
    }
  }

  get _pointsName() {
    const entity = this.hass?.states?.[this.config?.entity];
    return entity?.attributes?.points_name || "Stars";
  }

  get _pointsIcon() {
    const entity = this.hass?.states?.[this.config?.entity];
    return entity?.attributes?.points_icon || "mdi:star";
  }

  /**
   * The events to render: filtered first, then trimmed.
   *
   * The order matters. Trimming before filtering means a chip can come up
   * empty while matching events sit just outside the window, which reads as a
   * bug even though the data is there.
   */
  _visibleEvents(allEvents) {
    let events = allEvents;

    if (this.config.child_id) {
      events = events.filter((event) => event.child_id === this.config.child_id);
    }

    if (this._filter !== "all") {
      events = events.filter(
        (event) => this._presentation(event.event_type).group === this._filter
      );
    }

    return events.slice(0, this.config.max_items);
  }

  /** Group into [label, events] pairs, newest day first. */
  _groupByDay(events) {
    const today = this._dayKey(new Date());
    // Derived from today's calendar date, not by shifting the instant back 24
    // hours: setDate works on the browser's calendar while _dayKey formats in
    // Home Assistant's, and across a daylight-saving change in one and not the
    // other those disagree, putting "Yesterday" on the wrong day.
    const yesterdayDate = new Date(`${today}T12:00:00`);
    yesterdayDate.setDate(yesterdayDate.getDate() - 1);
    const yesterday = yesterdayDate.toLocaleDateString("en-CA");

    // A Map preserves insertion order, and the events arrive newest first, so
    // the groups come out in the right order without a second sort.
    const groups = new Map();
    for (const event of events) {
      const date = new Date(event.occurred_at);
      if (Number.isNaN(date.getTime())) continue;

      const key = this._dayKey(date);
      let label;
      if (key === today) label = "Today";
      else if (key === yesterday) label = "Yesterday";
      else {
        label = date.toLocaleDateString(undefined, {
          timeZone: this._timeZone,
          weekday: "long",
          month: "short",
          day: "numeric",
        });
      }

      if (!groups.has(key)) groups.set(key, { label, events: [] });
      groups.get(key).events.push({ ...event, _date: date });
    }
    return [...groups.values()];
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

    const missing = this._missingData(entity);
    if (missing.length) {
      return this._renderConfigError(missing);
    }

    const allEvents = entity.attributes.events || [];
    const forThisCard = this.config.child_id
      ? allEvents.filter((event) => event.child_id === this.config.child_id)
      : allEvents;
    const events = this._visibleEvents(allEvents);
    const groups = this._groupByDay(events);
    // Count what is actually drawn. _groupByDay discards anything with an
    // unreadable timestamp, and a header promising twenty events above a feed
    // showing nineteen sends people looking for the missing one.
    const shown = groups.reduce((total, group) => total + group.events.length, 0);

    return html`
      <ha-card>
        <div class="card-header">
          <div class="header-content">
            <ha-icon class="header-icon" icon="mdi:timeline-text"></ha-icon>
            <div class="header-title">${this.config.title}</div>
          </div>
          ${shown
            ? html`<div class="header-count">
                ${shown} ${shown === 1 ? "event" : "events"}
              </div>`
            : ""}
        </div>

        ${this.config.show_filters !== false ? this._renderFilters() : ""}

        ${groups.length
          ? html`<div class="feed">
              ${groups.map(
                (group) => html`
                  <div class="day-label">${group.label}</div>
                  ${group.events.map((event) => this._renderEvent(event))}
                `
              )}
            </div>`
          : this._renderEmpty(forThisCard.length > 0)}
      </ha-card>
    `;
  }

  _renderFilters() {
    return html`
      <div class="filters">
        ${FILTERS.map(
          (filter) => html`
            <button
              class="chip"
              type="button"
              aria-pressed="${this._filter === filter.id}"
              @click="${() => {
                this._filter = filter.id;
              }}"
            >
              ${filter.label}
            </button>
          `
        )}
      </div>
    `;
  }

  _renderEvent(event) {
    const presentation = this._presentation(event.event_type);
    // The integration sends the reward's own icon when the reward still
    // exists; using it costs nothing and makes a claim recognisable at a
    // glance. It falls back to the generic gift when the reward is gone.
    const icon =
      event.event_type === "reward_claimed" && event.reward_icon
        ? event.reward_icon
        : presentation.icon;
    const relative =
      this.config.show_relative_time !== false ? this._formatRelative(event._date) : null;

    return html`
      <div class="event">
        <div class="event-icon ${presentation.tone}">
          <ha-icon icon="${icon}"></ha-icon>
        </div>
        <div class="event-body">
          <div class="event-text">${this._describe(event)}</div>
          <div class="event-meta">
            <span>${relative || this._formatTime(event._date)}</span>
            ${event.event_type === "chore_pending"
              ? html`<span class="tag">waiting for approval</span>`
              : ""}
            ${event.reason ? html`<span class="event-reason">${event.reason}</span>` : ""}
          </div>
        </div>
        ${this._renderPoints(event)}
      </div>
    `;
  }

  _renderPoints(event) {
    // A pending chore has moved nobody's balance yet, so it shows what is at
    // stake rather than a change that has not happened.
    if (event.event_type === "chore_pending") {
      if (!event.pending_points) return "";
      const worth = `Worth ${event.pending_points} ${this._pointsName} once approved`;
      return html`
        <div class="points at-stake" title="${worth}" aria-label="${worth}">
          <ha-icon icon="${this._pointsIcon}"></ha-icon>${event.pending_points}
        </div>
      `;
    }

    if (!event.points) return "";
    const up = event.points > 0;
    // Without a label a screen reader announces "+5" with no unit, because the
    // only thing naming the currency is an icon.
    const label = `${up ? "+" : ""}${event.points} ${this._pointsName}`;
    return html`
      <div class="points ${up ? "up" : "down"}" aria-label="${label}">
        <ha-icon icon="${this._pointsIcon}"></ha-icon>${up ? "+" : ""}${event.points}
      </div>
    `;
  }

  /**
   * `hasEventsBehindTheFilter` counts what this card could show *before* the
   * chip narrowed it. When a card is scoped to a child who has done nothing,
   * no chip will help, and telling someone to try a different filter sends
   * them clicking forever.
   */
  _renderEmpty(hasEventsBehindTheFilter) {
    return html`
      <div class="empty">
        <ha-icon icon="mdi:timeline-text-outline"></ha-icon>
        <div class="empty-text">
          ${hasEventsBehindTheFilter
            ? "Nothing here yet - try a different filter."
            : "Nothing has happened yet. Get a chore done!"}
        </div>
      </div>
    `;
  }
}

// ---------------------------------------------------------------- editor

class ChoremanderActivityCardEditor extends LitElement {
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
          placeholder="sensor.choremander_activity"
        />
        <small>The Choremander activity sensor entity</small>
      </div>

      <div class="form-group">
        <label>Title</label>
        <input
          type="text"
          .value="${this.config.title || ""}"
          @input="${(e) => this._updateConfig("title", e.target.value)}"
          placeholder="Activity"
        />
        <small>Card title displayed in the header (default: "Activity")</small>
      </div>

      <div class="form-group">
        <label>Filter by Child</label>
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
        <small>Show only this child's activity (leave empty for the whole family)</small>
      </div>

      <div class="form-group">
        <label>Events to show</label>
        <input
          type="number"
          min="1"
          max="40"
          .value="${this.config.max_items ?? 20}"
          @input="${(e) => this._numberChanged("max_items", e.target.value, 1, MAX_FEED_ITEMS, 20)}"
        />
        <small>The integration keeps the most recent ${MAX_FEED_ITEMS} events</small>
      </div>

      <div class="form-group">
        <label>
          <input
            type="checkbox"
            ?checked="${this.config.show_filters !== false}"
            @change="${(e) => this._updateConfig("show_filters", e.target.checked)}"
          />
          Filter buttons
        </label>
        <small>Chips to narrow the feed to chores, rewards or bonuses</small>
      </div>

      <div class="form-group">
        <label>
          <input
            type="checkbox"
            ?checked="${this.config.show_relative_time !== false}"
            @change="${(e) => this._updateConfig("show_relative_time", e.target.checked)}"
          />
          Relative times
        </label>
        <small>Show "20m ago" for recent events instead of the clock time</small>
      </div>

      <div class="section-heading">Layout</div>

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
            Compact — fits more events on screen
          </option>
        </select>
        <small>How much space each event takes up</small>
      </div>

      <div class="form-group">
        <label>
          <input
            type="checkbox"
            ?checked="${this.config.animations !== false}"
            @change="${(e) => this._updateConfig("animations", e.target.checked)}"
          />
          Background animations
        </label>
        <small>Hover and press transitions on the filter chips</small>
      </div>
    `;
  }

  /**
   * Write a number the card will actually honour.
   *
   * The card clamps `max_items`, but the editor is handed the raw Lovelace
   * config rather than the card's clamped copy - so without this a value of 90
   * stays 90 in the YAML and in this field while the card quietly shows 40, and
   * nothing anywhere says why. Clearing the field is treated as "back to the
   * default" rather than as zero.
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
    show_filters: true,
    show_relative_time: true,
    max_items: 20,
  };

  _updateConfig(key, value) {
    const newConfig = { ...this.config, [key]: value };
    const isDefault = ChoremanderActivityCardEditor.LAYOUT_DEFAULTS[key] === value;
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
customElements.define("choremander-activity-card", ChoremanderActivityCard);
customElements.define("choremander-activity-card-editor", ChoremanderActivityCardEditor);

// Register with Home Assistant
window.customCards = window.customCards || [];
window.customCards.push({
  type: "choremander-activity-card",
  name: "Choremander Activity Card",
  description: "A timeline of chores done, rewards claimed, and bonuses given",
  preview: true,
});

console.info(
  "%c CHOREMANDER-ACTIVITY-CARD %c v0.0.1 ",
  "background: #3498db; color: white; font-weight: bold; border-radius: 4px 0 0 4px;",
  "background: #f1c40f; color: #333; font-weight: bold; border-radius: 0 4px 4px 0;"
);
