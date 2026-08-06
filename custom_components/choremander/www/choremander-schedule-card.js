/**
 * Choremander Schedule Card
 * A Lovelace card showing which days each chore falls on and whose turn it is,
 * across the fortnight the integration publishes, with the rules editable from
 * the grid itself.
 *
 * The rota is resolved server-side: `sensor.choremander_schedule` carries a
 * real roster for a real date, so this card never repeats the rotation
 * arithmetic in `schedule.py`. Every edit goes back through
 * `choremander.set_chore_schedule`, which is the only writer.
 *
 * Version: 0.0.1 - Initial release
 * Last Updated: 2026-08-05
 */

const LitElement = customElements.get("hui-masonry-view")
  ? Object.getPrototypeOf(customElements.get("hui-masonry-view"))
  : Object.getPrototypeOf(customElements.get("hui-view"));

const html = LitElement.prototype.html;
const css = LitElement.prototype.css;

/**
 * Lowercase and Monday first, exactly as the integration stores a weekday, so a
 * name picked in this card can be sent straight back as a `day_overrides` key.
 */
const DAY_NAMES = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
];

const DAY_INITIALS = ["M", "T", "W", "T", "F", "S", "S"];

/**
 * The rotation cadences, with "leave unchanged" first.
 *
 * The schedule sensor publishes the *resolution* of a rule, never the rule, so
 * this card cannot show a parent which cadence a chore is already on. Offering
 * "leave unchanged" is how the form stays honest about that: an untouched
 * control sends no field at all, and the service leaves what it cannot see
 * alone rather than resetting it to whatever the form happened to display.
 */
const ROTATIONS = [
  { value: "", label: "Leave unchanged" },
  { value: "none", label: "No rotation - everyone assigned" },
  { value: "occurrence", label: "Take turns each time" },
  { value: "week", label: "A whole week each" },
];

class ChoremanderScheduleCard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
      _week: { type: Number },
      _cell: { type: Object },
      _rule: { type: Object },
      _saving: { type: Boolean },
    };
  }

  constructor() {
    super();
    this._week = 0;
    this._cell = null;
    this._rule = null;
    this._saving = false;

    // Overrides written during this card's lifetime, per chore. `day_overrides`
    // and `date_overrides` are whole-dictionary fields - sending one key
    // replaces the lot - and nothing published to the frontend says what a
    // chore's dictionaries already hold. Remembering what this card wrote is
    // the most it can carry forward, so at least two edits in a row do not
    // undo each other.
    this._written = { days: {}, dates: {} };
  }

  static get styles() {
    return css`
      :host {
        display: block;
        --chm-schedule-marker: var(--chm-color-orange, #e67e22);
        --chm-schedule-today: var(--chm-color-blue, #3498db);
      }

      ha-card {
        overflow: hidden;
      }

      /* ---------------------------------------------------------- header */

      .card-header {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 16px 20px;
        background: linear-gradient(
          135deg,
          var(--chm-color-blue, #3498db) 0%,
          var(--chm-color-purple, #8e44ad) 100%
        );
        color: white;
      }

      .header-icon {
        --mdc-icon-size: 28px;
        flex-shrink: 0;
        opacity: 0.95;
      }

      .header-title {
        font-size: 1.3rem;
        font-weight: 700;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .week-nav {
        display: flex;
        align-items: center;
        gap: 4px;
        margin-left: auto;
      }

      .week-label {
        min-width: 96px;
        text-align: center;
        font-size: 0.95rem;
        font-weight: 600;
      }

      .nav {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        border: none;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.18);
        color: inherit;
        cursor: pointer;
        transition: background 0.15s ease;
      }

      .nav:hover:not(:disabled) {
        background: rgba(255, 255, 255, 0.32);
      }

      .nav:disabled {
        opacity: 0.35;
        cursor: not-allowed;
      }

      .nav ha-icon {
        --mdc-icon-size: 20px;
      }

      /* ------------------------------------------------------------ grid */

      .grid {
        display: grid;
        grid-template-columns: minmax(84px, 1.4fr) repeat(7, minmax(0, 1fr));
        gap: 4px;
        padding: 12px 16px 4px;
      }

      .day-head {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 2px;
        padding-bottom: 4px;
        color: var(--secondary-text-color);
        font-size: 0.75rem;
      }

      .day-head .letter {
        font-weight: 700;
        color: var(--primary-text-color);
      }

      .day-head.today .letter,
      .day-head.today .date {
        color: var(--chm-schedule-today);
      }

      .chore-name {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 4px 6px;
        border: none;
        border-radius: 8px;
        background: transparent;
        color: var(--primary-text-color);
        font: inherit;
        font-weight: 500;
        text-align: left;
        cursor: pointer;
        overflow: hidden;
      }

      .chore-name span {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .chore-name:hover {
        background: var(--secondary-background-color);
      }

      .chore-name ha-icon {
        --mdc-icon-size: 16px;
        color: var(--secondary-text-color);
        flex-shrink: 0;
      }

      .cell {
        position: relative;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: center;
        gap: 2px;
        min-height: 40px;
        padding: 4px 2px;
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        background: var(--card-background-color);
        cursor: pointer;
        transition: border-color 0.15s ease, background 0.15s ease;
      }

      .cell:hover {
        border-color: var(--primary-color);
        background: var(--secondary-background-color);
      }

      .cell.today {
        border-color: var(--chm-schedule-today);
      }

      .cell.empty::after {
        content: "·";
        color: var(--secondary-text-color);
        font-size: 1.2rem;
        line-height: 1;
      }

      /* A cell that does not repeat the way the rest of the chore's week does.
         It is the exception the parent typed, and it stays visibly an
         exception. */
      .cell.exception {
        border-style: dashed;
        border-color: var(--chm-schedule-marker);
      }

      .marker {
        position: absolute;
        top: 2px;
        right: 4px;
        color: var(--chm-schedule-marker);
        font-size: 0.9rem;
        line-height: 1;
      }

      .initials {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 22px;
        height: 22px;
        padding: 0 4px;
        border-radius: 11px;
        background: var(--primary-color);
        color: var(--text-primary-color, white);
        font-size: 0.72rem;
        font-weight: 700;
      }

      /* ------------------------------------------------------- add a chore */

      .add-row {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 12px 16px 16px;
      }

      .add-row ha-icon {
        --mdc-icon-size: 20px;
        color: var(--secondary-text-color);
      }

      select,
      input[type="text"] {
        flex: 1;
        min-width: 0;
        padding: 8px;
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font-size: 0.95em;
        box-sizing: border-box;
      }

      /* ---------------------------------------------------------- states */

      .empty,
      .error,
      .hint {
        padding: 24px 20px;
        color: var(--secondary-text-color);
        text-align: center;
      }

      .error {
        color: var(--error-color, #db4437);
      }

      .empty strong {
        display: block;
        margin-bottom: 4px;
        color: var(--primary-text-color);
      }

      .hint {
        padding: 0 20px 16px;
        font-size: 0.85em;
      }

      /* --------------------------------------------------------- dialogs */

      .dialog-overlay {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        background: rgba(0, 0, 0, 0.5);
        z-index: 9999;
      }

      .dialog-content {
        width: min(92vw, 380px);
        max-height: 86vh;
        overflow-y: auto;
        padding: 20px;
        border-radius: 16px;
        background: var(--card-background-color, white);
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
      }

      .dialog-title {
        font-size: 1.15rem;
        font-weight: 700;
        color: var(--primary-text-color);
      }

      .dialog-subtitle {
        margin-bottom: 16px;
        color: var(--secondary-text-color);
        font-size: 0.9rem;
      }

      .field-label {
        margin: 12px 0 6px;
        color: var(--secondary-text-color);
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }

      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }

      .chip {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 6px 12px;
        border: 1px solid var(--divider-color);
        border-radius: 18px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font: inherit;
        font-size: 0.9rem;
        cursor: pointer;
      }

      .chip.on {
        border-color: var(--primary-color);
        background: var(--primary-color);
        color: var(--text-primary-color, white);
      }

      .chip .order {
        font-size: 0.75rem;
        opacity: 0.85;
      }

      .scope {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }

      .scope-option {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 12px;
        border: 1px solid var(--divider-color);
        border-radius: 10px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        font: inherit;
        text-align: left;
        cursor: pointer;
      }

      .scope-option.on {
        border-color: var(--primary-color);
        color: var(--primary-color);
      }

      .scope-option ha-icon {
        --mdc-icon-size: 20px;
      }

      .dialog-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 20px;
      }

      .dialog-button {
        padding: 8px 18px;
        border: none;
        border-radius: 8px;
        font: inherit;
        font-weight: 500;
        cursor: pointer;
      }

      .dialog-button.cancel {
        background: var(--secondary-background-color);
        color: var(--primary-text-color);
      }

      .dialog-button.save {
        background: var(--primary-color);
        color: var(--text-primary-color, white);
      }

      .dialog-button:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }

      .note {
        margin-top: 10px;
        color: var(--secondary-text-color);
        font-size: 0.8rem;
      }

      @media (max-width: 500px) {
        .grid {
          grid-template-columns: minmax(64px, 1.2fr) repeat(7, minmax(0, 1fr));
          padding: 10px 8px 4px;
        }

        .week-label {
          min-width: 76px;
          font-size: 0.85rem;
        }
      }
    `;
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error("Please define an entity (Choremander schedule sensor)");
    }
    this.config = {
      title: "Weekly Schedule",
      // A second entity because the schedule sensor carries ids and nothing
      // else: the chore roster, and with it every chore that has no rule yet,
      // only exists on the overview sensor.
      chores_entity: "sensor.choremander_overview",
      ...config,
    };
  }

  getCardSize() {
    return 4;
  }

  static getConfigElement() {
    return document.createElement("choremander-schedule-card-editor");
  }

  static getStubConfig() {
    return {
      entity: "sensor.choremander_schedule",
      chores_entity: "sensor.choremander_overview",
      title: "Weekly Schedule",
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
    return ["window_start", "days", "chores"];
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

  // ------------------------------------------------------------- calendar

  /** The Home Assistant timezone, falling back to the browser's. */
  get _timeZone() {
    return (
      this.hass?.config?.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone
    );
  }

  /** Today as the integration writes a date, so the two can be compared. */
  get _todayKey() {
    return new Date().toLocaleDateString("en-CA", { timeZone: this._timeZone });
  }

  /**
   * Turn `window_start` plus an offset into a local Date.
   *
   * Built from the three numbers rather than parsed from the string: `new
   * Date("2026-08-03")` is UTC midnight, which is the previous day for every
   * timezone west of Greenwich, and the whole grid would be labelled a day
   * early. From here on the arithmetic is purely calendar arithmetic, so the
   * day a cell shows is the day the integration resolved.
   */
  _dayAt(windowStart, offset) {
    const [year, month, day] = String(windowStart)
      .slice(0, 10)
      .split("-")
      .map((part) => Number(part));
    const date = new Date(year, (month || 1) - 1, day || 1);
    date.setDate(date.getDate() + offset);
    return date;
  }

  _dateKey(date) {
    return date.toLocaleDateString("en-CA");
  }

  _shortDate(date) {
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  _longDate(date) {
    return date.toLocaleDateString(undefined, {
      weekday: "long",
      month: "short",
      day: "numeric",
    });
  }

  // -------------------------------------------------------------- reading

  /** The chores the household has, whether or not they carry a rule. */
  _choreRoster() {
    const entity = this.hass.states[this.config.chores_entity];
    return entity?.attributes?.chores || [];
  }

  _childRoster() {
    const entity = this.hass.states[this.config.chores_entity];
    return entity?.attributes?.children || [];
  }

  _choreName(choreId) {
    const chore = this._choreRoster().find((item) => item.id === choreId);
    return chore?.name || choreId;
  }

  _childName(childId) {
    const child = this._childRoster().find((item) => item.id === childId);
    return child?.name || childId;
  }

  /** Two letters at most, which is all a cell in a seven column grid can hold. */
  _initials(childId) {
    const name = this._childName(childId);
    const letters = String(name)
      .split(/\s+/)
      .filter((word) => word)
      .map((word) => word[0])
      .join("");
    return (letters || String(childId).slice(0, 2)).slice(0, 2).toUpperCase();
  }

  /** The child ids on `choreIndex` on the day at `offset`. */
  _rosterAt(attributes, choreIndex, offset) {
    const children = attributes.children || [];
    const day = (attributes.days || [])[offset] || {};
    return (day[String(choreIndex)] || [])
      .map((index) => children[index])
      .filter((id) => id);
  }

  /**
   * The cells that read as exceptions rather than as the rule.
   *
   * The sensor publishes resolved days, never the rules behind them, so
   * "overridden" cannot be read off the payload directly. What can be read is
   * repetition: a chore whose two published weeks are identical bar a day or
   * two is a weekly pattern with exceptions in it, and those days are the
   * exceptions. A chore whose weeks differ broadly is a rotation working as
   * intended - marking every cell of it would be noise, so nothing is marked.
   *
   * One-off overrides written from this card are added outright, because there
   * the card knows rather than infers.
   */
  _exceptions(attributes) {
    const marked = new Set();
    const days = attributes.days || [];
    const chores = attributes.chores || [];

    chores.forEach((choreId, index) => {
      const written = this._written.dates[choreId] || {};
      Object.keys(written).forEach((dateKey) => {
        const offset = this._offsetOf(attributes, dateKey);
        if (offset !== -1) marked.add(`${index}:${offset}`);
      });

      if (days.length < 14) return;

      const differing = [];
      for (let weekday = 0; weekday < 7; weekday++) {
        const first = this._rosterAt(attributes, index, weekday).join(",");
        const second = this._rosterAt(attributes, index, weekday + 7).join(",");
        if (first !== second) differing.push(weekday);
      }

      // Three or more days apart is a chore that changes hands week by week,
      // not a week with exceptions in it.
      if (!differing.length || differing.length > 2) return;
      differing.forEach((weekday) => {
        marked.add(`${index}:${weekday}`);
        marked.add(`${index}:${weekday + 7}`);
      });
    });

    return marked;
  }

  /** Where a date sits in the published window, or -1 if it is outside it. */
  _offsetOf(attributes, dateKey) {
    const total = attributes.days?.length || 0;
    for (let offset = 0; offset < total; offset++) {
      if (this._dateKey(this._dayAt(attributes.window_start, offset)) === dateKey) {
        return offset;
      }
    }
    return -1;
  }

  /**
   * The weekdays a chore currently falls on, read back off the grid.
   *
   * The rule editor has to open showing something, and this is the only
   * evidence available. It is exact for a chore with fixed due days, which is
   * the common case; a rotation shows the same weekdays either way, since a
   * rotation changes who is on a day and not which days exist.
   */
  _observedDueDays(attributes, choreIndex) {
    const total = attributes.days?.length || 0;
    const found = new Set();
    for (let offset = 0; offset < total; offset++) {
      if (this._rosterAt(attributes, choreIndex, offset).length) {
        const day = this._dayAt(attributes.window_start, offset);
        found.add(DAY_NAMES[(day.getDay() + 6) % 7]);
      }
    }
    return DAY_NAMES.filter((name) => found.has(name));
  }

  // -------------------------------------------------------------- writing

  /**
   * Send one schedule change.
   *
   * Only the fields being edited are ever passed: the service leaves an absent
   * field alone and clears an empty one, so a call that moves a Saturday must
   * not mention the rotation it knows nothing about.
   */
  async _write(data) {
    this._saving = true;
    try {
      await this.hass.callService("choremander", "set_chore_schedule", data);
      return true;
    } catch (error) {
      console.error("Failed to save the chore schedule:", error);
      // The grid is about to redraw from the sensor and would simply show the
      // old answer, which reads as nothing having happened. Say so instead.
      this.hass.callService("persistent_notification", "create", {
        title: "Error Saving Schedule",
        message: `Failed to save the schedule: ${error.message}`,
        notification_id: "choremander_schedule_error",
      });
      return false;
    } finally {
      this._saving = false;
    }
  }

  // --------------------------------------------------------------- render

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

    if (entity.state === "unavailable" || entity.state === "unknown") {
      // Not an error, and emphatically not "nothing is scheduled": while the
      // integration is restarting every chore behaves the way it did before
      // schedules existed, and saying that is kinder than an empty grid.
      return html`
        <ha-card>
          <div class="empty">
            <strong>${this.config.entity} is not available right now</strong>
            Until it comes back, every chore is on for whoever it is assigned to.
          </div>
        </ha-card>
      `;
    }

    const missing = this._missingData(entity);
    if (missing.length) {
      return this._renderConfigError(missing);
    }

    const attributes = entity.attributes;
    const windowDays = attributes.window_days || attributes.days?.length || 7;
    const weeks = Math.max(1, Math.ceil(windowDays / 7));
    const week = Math.min(Math.max(this._week, 0), weeks - 1);
    const first = this._dayAt(attributes.window_start, week * 7);
    const last = this._dayAt(attributes.window_start, week * 7 + 6);

    return html`
      <ha-card>
        <div class="card-header">
          <ha-icon class="header-icon" icon="mdi:calendar-week"></ha-icon>
          <div class="header-title">${this.config.title}</div>
          <div class="week-nav">
            <button
              class="nav prev"
              title="Previous week"
              ?disabled="${week === 0}"
              @click="${() => (this._week = week - 1)}"
            >
              <ha-icon icon="mdi:chevron-left"></ha-icon>
            </button>
            <span class="week-label"
              >${this._shortDate(first)} - ${this._shortDate(last)}</span
            >
            <button
              class="nav next"
              title="Next week"
              ?disabled="${week >= weeks - 1}"
              @click="${() => (this._week = week + 1)}"
            >
              <ha-icon icon="mdi:chevron-right"></ha-icon>
            </button>
          </div>
        </div>

        ${(attributes.chores || []).length
          ? this._renderGrid(attributes, week)
          : this._renderEmpty()}
        ${this._renderAdd(attributes)}
        ${this._cell ? this._renderCellDialog() : ""}
        ${this._rule ? this._renderRuleDialog() : ""}
      </ha-card>
    `;
  }

  _renderEmpty() {
    return html`
      <div class="empty">
        <strong>Nothing is on a schedule yet</strong>
        Every chore is on every day, for whoever it is assigned to. Put one on a
        schedule below to give it days of its own.
      </div>
    `;
  }

  _renderGrid(attributes, week) {
    const exceptions = this._exceptions(attributes);
    const todayKey = this._todayKey;

    return html`
      <div class="grid">
        <div class="corner"></div>
        ${DAY_INITIALS.map((letter, index) => {
          const day = this._dayAt(attributes.window_start, week * 7 + index);
          const isToday = this._dateKey(day) === todayKey;
          return html`
            <div class="day-head ${isToday ? "today" : ""}">
              <span class="letter">${letter}</span>
              <span class="date">${day.getDate()}</span>
            </div>
          `;
        })}
        ${(attributes.chores || []).map((choreId, choreIndex) =>
          this._renderRow(attributes, week, choreId, choreIndex, exceptions, todayKey)
        )}
      </div>
    `;
  }

  _renderRow(attributes, week, choreId, choreIndex, exceptions, todayKey) {
    const cells = [];
    for (let column = 0; column < 7; column++) {
      const offset = week * 7 + column;
      const day = this._dayAt(attributes.window_start, offset);
      const dateKey = this._dateKey(day);
      const roster = this._rosterAt(attributes, choreIndex, offset);
      const isException = exceptions.has(`${choreIndex}:${offset}`);

      cells.push(html`
        <button
          class="cell ${roster.length ? "" : "empty"} ${isException ? "exception" : ""} ${dateKey === todayKey ? "today" : ""}"
          data-chore-id="${choreId}"
          data-date="${dateKey}"
          title="${this._choreName(choreId)} - ${this._longDate(day)}"
          @click="${() => this._openCell(attributes, choreIndex, offset)}"
        >
          ${isException ? html`<span class="marker">•</span>` : ""}
          ${roster.map(
            (childId) => html`
              <span class="initials" data-child-id="${childId}" title="${this._childName(childId)}"
                >${this._initials(childId)}</span
              >
            `
          )}
        </button>
      `);
    }

    return html`
      <button
        class="chore-name"
        data-chore-id="${choreId}"
        title="Edit how ${this._choreName(choreId)} rotates"
        @click="${() => this._openRule(attributes, choreId)}"
      >
        <ha-icon icon="mdi:pencil"></ha-icon>
        <span>${this._choreName(choreId)}</span>
      </button>
      ${cells}
    `;
  }

  _renderAdd(attributes) {
    const scheduled = new Set(attributes.chores || []);
    const roster = this._choreRoster();
    const unscheduled = roster.filter((chore) => !scheduled.has(chore.id));

    if (!roster.length) {
      return html`
        <div class="hint">
          ${this.config.chores_entity} carries no chore list, so there is nothing
          here to put on a schedule. This card reads that from
          ${this.constructor.getStubConfig().chores_entity}.
        </div>
      `;
    }

    if (!unscheduled.length) {
      return html`<div class="hint">Every chore is on a schedule.</div>`;
    }

    return html`
      <div class="add-row">
        <ha-icon icon="mdi:calendar-plus"></ha-icon>
        <select
          class="add-select"
          .value="${""}"
          @change="${(event) => {
            const choreId = event.target.value;
            event.target.value = "";
            if (choreId) this._openRule(attributes, choreId);
          }}"
        >
          <option value="">Put a chore on the schedule...</option>
          ${unscheduled.map(
            (chore) => html`<option value="${chore.id}">${chore.name}</option>`
          )}
        </select>
      </div>
    `;
  }

  // --------------------------------------------------------- cell editing

  _openCell(attributes, choreIndex, offset) {
    const day = this._dayAt(attributes.window_start, offset);
    this._cell = {
      choreId: (attributes.chores || [])[choreIndex],
      day,
      dateKey: this._dateKey(day),
      weekday: DAY_NAMES[(day.getDay() + 6) % 7],
      selected: this._rosterAt(attributes, choreIndex, offset),
      // One day or every one of that weekday - the choice every calendar app
      // asks when a repeating thing is edited, so there is nothing new to
      // learn here.
      scope: "date",
      children: attributes.children || [],
    };
  }

  _renderCellDialog() {
    const cell = this._cell;
    const weekdayLabel = cell.day.toLocaleDateString(undefined, { weekday: "long" });

    return html`
      <div class="dialog-overlay cell-dialog" @click="${() => (this._cell = null)}">
        <div class="dialog-content" @click="${(event) => event.stopPropagation()}">
          <div class="dialog-title">${this._choreName(cell.choreId)}</div>
          <div class="dialog-subtitle">${this._longDate(cell.day)}</div>

          <div class="field-label">Who is on it</div>
          <div class="chips">
            ${cell.children.map(
              (childId) => html`
                <button
                  class="chip ${cell.selected.includes(childId) ? "on" : ""}"
                  data-child-id="${childId}"
                  @click="${() => this._toggleCellChild(childId)}"
                >
                  ${this._childName(childId)}
                </button>
              `
            )}
          </div>
          ${cell.selected.length
            ? ""
            : html`<div class="note">Nobody, which is a day off rather than a
                deleted rule.</div>`}

          <div class="field-label">Change</div>
          <div class="scope">
            <button
              class="scope-option ${cell.scope === "date" ? "on" : ""}"
              data-scope="date"
              @click="${() => (this._cell = { ...cell, scope: "date" })}"
            >
              <ha-icon icon="mdi:calendar-today"></ha-icon>
              Just ${this._shortDate(cell.day)}
            </button>
            <button
              class="scope-option ${cell.scope === "day" ? "on" : ""}"
              data-scope="day"
              @click="${() => (this._cell = { ...cell, scope: "day" })}"
            >
              <ha-icon icon="mdi:calendar-refresh"></ha-icon>
              Every ${weekdayLabel}
            </button>
          </div>

          <div class="dialog-actions">
            <button
              class="dialog-button cancel"
              ?disabled="${this._saving}"
              @click="${() => (this._cell = null)}"
            >
              Cancel
            </button>
            <button
              class="dialog-button save"
              ?disabled="${this._saving}"
              @click="${this._saveCell}"
            >
              ${this._saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _toggleCellChild(childId) {
    const cell = this._cell;
    const selected = cell.selected.includes(childId)
      ? cell.selected.filter((id) => id !== childId)
      : [...cell.selected, childId];
    this._cell = { ...cell, selected };
  }

  async _saveCell() {
    const cell = this._cell;
    const bucket = cell.scope === "date" ? "dates" : "days";
    const key = cell.scope === "date" ? cell.dateKey : cell.weekday;
    const field = cell.scope === "date" ? "date_overrides" : "day_overrides";

    const overrides = {
      ...(this._written[bucket][cell.choreId] || {}),
      [key]: cell.selected,
    };

    if (await this._write({ chore_id: cell.choreId, [field]: overrides })) {
      this._written[bucket][cell.choreId] = overrides;
      this._cell = null;
    }
  }

  // --------------------------------------------------------- rule editing

  _openRule(attributes, choreId) {
    const choreIndex = (attributes.chores || []).indexOf(choreId);
    this._rule = {
      choreId,
      dueDays:
        choreIndex === -1 ? [] : this._observedDueDays(attributes, choreIndex),
      rotation: "",
      order: [],
      anchor: "",
      children: (attributes.children || []).length
        ? attributes.children
        : this._childRoster().map((child) => child.id),
    };
  }

  _renderRuleDialog() {
    const rule = this._rule;

    return html`
      <div class="dialog-overlay rule-dialog" @click="${() => (this._rule = null)}">
        <div class="dialog-content" @click="${(event) => event.stopPropagation()}">
          <div class="dialog-title">${this._choreName(rule.choreId)}</div>
          <div class="dialog-subtitle">Which days it falls on, and whose turn it is</div>

          <div class="field-label">Due days</div>
          <div class="chips">
            ${DAY_NAMES.map(
              (name, index) => html`
                <button
                  class="chip day-toggle ${rule.dueDays.includes(name) ? "on" : ""}"
                  data-day="${name}"
                  @click="${() => this._toggleDueDay(name)}"
                >
                  ${DAY_INITIALS[index]}
                </button>
              `
            )}
          </div>
          ${rule.dueDays.length
            ? ""
            : html`<div class="note">
                No days picked means every day, which takes this chore off the
                schedule entirely.
              </div>`}

          <div class="field-label">Rotation</div>
          <select
            class="rotation-select"
            @change="${(event) => (this._rule = { ...rule, rotation: event.target.value })}"
          >
            ${ROTATIONS.map(
              (option) => html`
                <option value="${option.value}" ?selected="${rule.rotation === option.value}">
                  ${option.label}
                </option>
              `
            )}
          </select>

          <div class="field-label">Order of turns</div>
          <div class="chips">
            ${rule.children.map((childId) => {
              const position = rule.order.indexOf(childId);
              return html`
                <button
                  class="chip order-toggle ${position === -1 ? "" : "on"}"
                  data-child-id="${childId}"
                  @click="${() => this._toggleOrder(childId)}"
                >
                  ${this._childName(childId)}
                  ${position === -1
                    ? ""
                    : html`<span class="order">${position + 1}</span>`}
                </button>
              `;
            })}
          </div>

          <div class="field-label">Counting from</div>
          <input
            class="anchor-input"
            type="text"
            placeholder="2026-08-03 (optional)"
            .value="${rule.anchor}"
            @input="${(event) => (this._rule = { ...rule, anchor: event.target.value })}"
          />

          <div class="note">
            Rotation, order and start date are only sent when you set them here.
            Anything left alone stays as it is.
          </div>

          <div class="dialog-actions">
            <button
              class="dialog-button cancel"
              ?disabled="${this._saving}"
              @click="${() => (this._rule = null)}"
            >
              Cancel
            </button>
            <button
              class="dialog-button save"
              ?disabled="${this._saving}"
              @click="${this._saveRule}"
            >
              ${this._saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      </div>
    `;
  }

  _toggleDueDay(name) {
    const rule = this._rule;
    const dueDays = rule.dueDays.includes(name)
      ? rule.dueDays.filter((day) => day !== name)
      : DAY_NAMES.filter((day) => day === name || rule.dueDays.includes(day));
    this._rule = { ...rule, dueDays };
  }

  /** Add a child to the end of the order, or take them out of it. */
  _toggleOrder(childId) {
    const rule = this._rule;
    const order = rule.order.includes(childId)
      ? rule.order.filter((id) => id !== childId)
      : [...rule.order, childId];
    this._rule = { ...rule, order };
  }

  async _saveRule() {
    const rule = this._rule;
    const data = { chore_id: rule.choreId, due_days: rule.dueDays };

    // The three controls below default to "not mentioned", so a parent who
    // came here to move a due day does not also rewrite a rotation this card
    // was never told about.
    if (rule.rotation) data.rotation = rule.rotation;
    if (rule.order.length) data.rotation_order = rule.order;
    if (rule.anchor.trim()) data.rotation_anchor = rule.anchor.trim();

    if (await this._write(data)) {
      this._rule = null;
    }
  }
}

// Card Editor
class ChoremanderScheduleCardEditor extends LitElement {
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

    return html`
      <div class="form-group">
        <label>Schedule Entity</label>
        <input
          type="text"
          .value="${this.config.entity || ""}"
          @input="${(event) => this._updateConfig("entity", event.target.value)}"
          placeholder="sensor.choremander_schedule"
        />
        <small>The sensor carrying the resolved schedule</small>
      </div>

      <div class="form-group">
        <label>Chores Entity</label>
        <input
          type="text"
          .value="${this.config.chores_entity || ""}"
          @input="${(event) => this._updateConfig("chores_entity", event.target.value)}"
          placeholder="sensor.choremander_overview"
        />
        <small>Where the chore and child names come from</small>
      </div>

      <div class="form-group">
        <label>Card Title</label>
        <input
          type="text"
          .value="${this.config.title || ""}"
          @input="${(event) => this._updateConfig("title", event.target.value)}"
          placeholder="Weekly Schedule"
        />
        <small>Optional custom title for the card</small>
      </div>
    `;
  }

  _updateConfig(key, value) {
    const newConfig = { ...this.config, [key]: value };
    if (value === undefined || value === "") {
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
customElements.define("choremander-schedule-card", ChoremanderScheduleCard);
customElements.define("choremander-schedule-card-editor", ChoremanderScheduleCardEditor);

// Register with Home Assistant
window.customCards = window.customCards || [];
window.customCards.push({
  type: "choremander-schedule-card",
  name: "Choremander Schedule Card",
  description: "The week's chore rota: which days each chore falls on and whose turn it is",
  preview: true,
});

console.info(
  "%c CHOREMANDER-SCHEDULE-CARD %c v0.0.1 ",
  "background: #3498db; color: white; font-weight: bold; border-radius: 4px 0 0 4px;",
  "background: #8e44ad; color: white; font-weight: bold; border-radius: 0 4px 4px 0;"
);
