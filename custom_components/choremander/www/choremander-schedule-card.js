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
 * The same sensor carries `rules`, each chore's stored rule verbatim, and this
 * card reads every editing decision out of it rather than out of the resolved
 * grid. That distinction is the whole of the editing model: the grid says what
 * is true on a day, and cannot say why. A Saturday somebody is on might be a
 * due day or a one-off swap, and every field the service takes replaces its
 * stored value wholesale - so a card that guessed would turn "Maya covers this
 * Saturday" into "due every Saturday", and would delete the overrides it never
 * saw. Nothing below infers a rule; it either reads one or leaves the field
 * out of the call.
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

/** The cadences `schedule.py` implements, said the way a parent would say them. */
const ROTATIONS = [
  { value: "none", label: "No rotation - everyone assigned" },
  { value: "occurrence", label: "Take turns each time" },
  { value: "week", label: "A whole week each" },
];

/**
 * A rule as this card reads it: every field present, at its default if the
 * sensor left it out.
 *
 * The payload omits defaults to stay inside the recorder's attribute budget, so
 * a missing key means "the default" and never "unknown". Filling them in once,
 * here, is what lets the rest of the card compare what a parent typed against
 * what is stored without every call site repeating the fallbacks.
 */
const RULE_DEFAULTS = {
  due_days: [],
  rotation: "none",
  rotation_order: [],
  rotation_anchor: "",
  day_overrides: {},
  date_overrides: {},
};

class ChoremanderScheduleCard extends LitElement {
  /** Accepted values for the `density` option, loosest first. */
  static DENSITIES = ["default", "cozy", "compact"];

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
        padding: var(--chm-header-padding, 16px 20px);
        background: linear-gradient(
          135deg,
          var(--chm-color-blue, #3498db) 0%,
          var(--chm-color-purple, #8e44ad) 100%
        );
        color: white;
      }

      .header-icon {
        --mdc-icon-size: var(--chm-header-icon-size, 28px);
        flex-shrink: 0;
        opacity: 0.95;
      }

      .header-title {
        font-size: var(--chm-header-title-font-size, 1.3rem);
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
        gap: var(--chm-schedule-grid-gap, 4px);
        padding: var(--chm-schedule-grid-padding, 12px 16px 4px);
      }

      .day-head {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 2px;
        padding-bottom: 4px;
        color: var(--secondary-text-color);
        font-size: var(--chm-schedule-day-head-font-size, 0.75rem);
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
        font-size: var(--chm-schedule-chore-font-size, 1rem);
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
        min-height: var(--chm-schedule-cell-min-height, 40px);
        padding: var(--chm-schedule-cell-padding, 4px 2px);
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

      /* Deliberately not called "empty": that name belongs to the whole-card
         message below, whose padding would win here at equal specificity and
         blow every unoccupied cell out to 24px of its own. */
      .cell.nobody::after {
        content: "·";
        color: var(--secondary-text-color);
        font-size: 1.2rem;
        line-height: 1;
      }

      /* A day the rule names outright - a one-off swap, or a weekday the parent
         has pinned. It is the exception to the pattern, and it stays visibly an
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
        min-width: var(--chm-schedule-initials-size, 22px);
        height: var(--chm-schedule-initials-size, 22px);
        padding: 0 4px;
        /* Half the height, so the pill stays a pill at every density. */
        border-radius: calc(var(--chm-schedule-initials-size, 22px) / 2);
        background: var(--primary-color);
        color: var(--text-primary-color, white);
        font-size: var(--chm-schedule-initials-font-size, 0.72rem);
        font-weight: 700;
      }

      /* ------------------------------------------------------- add a chore */

      .add-row {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: var(--chm-schedule-add-padding, 12px 16px 16px);
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

      .empty-state,
      .error,
      .hint {
        padding: 24px 20px;
        color: var(--secondary-text-color);
        text-align: center;
      }

      .error {
        color: var(--error-color, #db4437);
      }

      .empty-state strong {
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

      /* --------------------------------------------------------- density */

      /* Only sizing, never colour or structure: the grid is seven columns of
         cells at every density, so a household that picks one is choosing how
         much of the dashboard a fortnight costs them, not a different card.
         The attribute is absent at "default", which is why these are variables
         with fallbacks rather than a third block repeating the defaults. */

      :host([density="cozy"]) {
        --chm-header-padding: 12px 16px;
        --chm-header-icon-size: 24px;
        --chm-header-title-font-size: 1.15rem;
        --chm-schedule-grid-padding: 10px 12px 4px;
        --chm-schedule-grid-gap: 3px;
        --chm-schedule-cell-min-height: 34px;
        --chm-schedule-chore-font-size: 0.95rem;
        --chm-schedule-initials-size: 20px;
        --chm-schedule-initials-font-size: 0.68rem;
        --chm-schedule-add-padding: 10px 12px 12px;
      }

      :host([density="compact"]) {
        --chm-header-padding: 8px 12px;
        --chm-header-icon-size: 20px;
        --chm-header-title-font-size: 1.05rem;
        --chm-schedule-grid-padding: 8px 10px 4px;
        --chm-schedule-grid-gap: 2px;
        --chm-schedule-cell-min-height: 28px;
        --chm-schedule-cell-padding: 2px 1px;
        --chm-schedule-day-head-font-size: 0.68rem;
        --chm-schedule-chore-font-size: 0.88rem;
        --chm-schedule-initials-size: 17px;
        --chm-schedule-initials-font-size: 0.6rem;
        --chm-schedule-add-padding: 8px 10px 10px;
      }

      @media (max-width: 500px) {
        /* Set on the host rather than on .grid so a density still wins: the
           attribute selectors above outrank :host whatever the source order. */
        :host {
          --chm-schedule-grid-padding: 10px 8px 4px;
        }

        .grid {
          grid-template-columns: minmax(64px, 1.2fr) repeat(7, minmax(0, 1fr));
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
      density: "default", // "default" | "cozy" | "compact"
      ...config,
    };

    if (!ChoremanderScheduleCard.DENSITIES.includes(this.config.density)) {
      // Coerce rather than throw - one mistyped option should not replace the
      // whole card with an error.
      console.warn(
        `[Choremander] Unknown density "${this.config.density}". ` +
          `Expected one of ${ChoremanderScheduleCard.DENSITIES.join(", ")}. Falling back to "default".`
      );
      this.config = { ...this.config, density: "default" };
    }

    this._syncLayoutAttributes();
  }

  /**
   * Mirror the layout options onto the host, which is what the :host([...])
   * rules select on. The attribute is removed at its default rather than set
   * to "default", so a card that never opted in matches none of those rules.
   */
  _syncLayoutAttributes() {
    if (!this.config) return;

    if (this.config.density && this.config.density !== "default") {
      this.setAttribute("density", this.config.density);
    } else {
      this.removeAttribute("density");
    }
  }

  connectedCallback() {
    super.connectedCallback();
    // setConfig can run before the element is in the document, and Lovelace
    // reuses card elements across view switches, so re-assert on connect.
    this._syncLayoutAttributes();
  }

  getCardSize() {
    // Cozy and compact land on the same number of masonry rows on purpose: what
    // a density trims is the height of a cell, and a row of seven of them only
    // shrinks so far before the header and the day letters are the whole card.
    const sizes = { default: 4, cozy: 3, compact: 3 };
    return sizes[this.config?.density] ?? 4;
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
   * `rules` is in the list because editing is unsafe without it. Every
   * override field replaces its stored dictionary wholesale, so a card that
   * saved against a payload carrying no rules would delete every override it
   * could not see. Refusing to draw is the only honest answer to that.
   *
   * A `name[].field` entry means the attribute must be a list whose entries
   * carry that field.
   */
  static get requiredData() {
    return ["window_start", "days", "chores", "rules"];
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

  /** The schedule attributes as the sensor has them right now. */
  _attributes() {
    return this.hass?.states[this.config.entity]?.attributes || {};
  }

  /**
   * The stored rule for a chore, every field filled in.
   *
   * A chore with no rule at all is absent from `chores` entirely, and reads
   * here as the all-defaults rule - which is exactly what it has: no due days,
   * no rotation, no overrides, on every day for whoever it is assigned to.
   * Copies are returned so a caller editing a dictionary cannot reach into the
   * entity's attributes and change what the next render draws.
   */
  _ruleFor(attributes, choreId) {
    const index = (attributes.chores || []).indexOf(choreId);
    const stored = (index === -1 ? null : (attributes.rules || [])[index]) || {};
    return {
      due_days: [...(stored.due_days || RULE_DEFAULTS.due_days)],
      rotation: stored.rotation || RULE_DEFAULTS.rotation,
      rotation_order: [...(stored.rotation_order || RULE_DEFAULTS.rotation_order)],
      rotation_anchor: stored.rotation_anchor || RULE_DEFAULTS.rotation_anchor,
      day_overrides: { ...(stored.day_overrides || RULE_DEFAULTS.day_overrides) },
      date_overrides: { ...(stored.date_overrides || RULE_DEFAULTS.date_overrides) },
    };
  }

  /**
   * The days a rule stores as due, lowercased and Monday first.
   *
   * `schedule.py` compares due days case-insensitively but stores and publishes
   * them verbatim, so a chore written by hand as `["Monday","Wednesday"]` is a
   * Monday-and-Wednesday chore and the grid draws it as one. Matching that
   * case-sensitively would light no chips in the rule dialog, tell the parent
   * the chore was on no schedule at all, and offer to save that back - deleting
   * the pattern the grid behind the dialog is still showing them.
   */
  _dueDaysOf(rule) {
    const stored = new Set(
      (rule.due_days || []).map((name) => String(name).toLowerCase())
    );
    return DAY_NAMES.filter((name) => stored.has(name));
  }

  /**
   * Whether an override map holds an entry the integration would find for `key`.
   *
   * `_override_for` matches keys case-insensitively, so `{"Friday": [...]}` from
   * a hand-written automation *is* a Friday override and has to be drawn as the
   * exception it is rather than as the pattern.
   */
  _hasOverride(overrides, key) {
    const wanted = String(key).toLowerCase();
    return Object.keys(overrides).some(
      (stored) => String(stored).toLowerCase() === wanted
    );
  }

  /**
   * `overrides` with `key` set, replacing any key that already means the same.
   *
   * Because the integration matches case-insensitively, a rule already carrying
   * `{"Friday": ["child_m"]}` must not come back with a second `"friday"` beside
   * it: the lookup returns whichever was stored first, so the parent's edit
   * would sit in the rule permanently without ever being read, and the save
   * would look like it had simply done nothing. One key per day, spelled the way
   * this card spells it.
   */
  _withOverride(overrides, key, childIds) {
    const wanted = String(key).toLowerCase();
    const merged = {};
    for (const [stored, value] of Object.entries(overrides)) {
      if (String(stored).toLowerCase() !== wanted) {
        merged[stored] = value;
      }
    }
    merged[key] = childIds;
    return merged;
  }

  /**
   * The cells the rule names outright, rather than resolves to.
   *
   * A marked cell means the parent said something about this particular day:
   * the date is in `date_overrides`, or its weekday is in `day_overrides`.
   * Nothing else is marked, and in particular a rotation is not - a chore due
   * Mondays that changes hands each week differs between the two published
   * Mondays by design, and marking both would tell the parent they had
   * overridden the very thing the rotation is doing for them.
   */
  _exceptions(attributes) {
    const marked = new Set();
    const total = attributes.days?.length || 0;

    (attributes.chores || []).forEach((choreId, index) => {
      const rule = this._ruleFor(attributes, choreId);
      for (let offset = 0; offset < total; offset++) {
        const day = this._dayAt(attributes.window_start, offset);
        const weekday = DAY_NAMES[(day.getDay() + 6) % 7];
        if (
          this._hasOverride(rule.date_overrides, this._dateKey(day)) ||
          this._hasOverride(rule.day_overrides, weekday)
        ) {
          marked.add(`${index}:${offset}`);
        }
      }
    });

    return marked;
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
          <div class="empty-state">
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
      <div class="empty-state">
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
          class="cell ${roster.length ? "" : "nobody"} ${isException ? "exception" : ""} ${dateKey === todayKey ? "today" : ""}"
          data-chore-id="${choreId}"
          data-date="${dateKey}"
          title="${this._choreName(choreId)} - ${this._longDate(day)}"
          @click="${() => this._openCell(attributes, choreIndex, offset)}"
        >
          ${isException
            ? html`<span class="marker" title="Set by hand rather than by the pattern">•</span>`
            : ""}
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
    const stored = this._ruleFor(this._attributes(), cell.choreId);
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
          ${cell.scope === "day" && cell.dateKey in stored.date_overrides
            ? html`<div class="note">
                ${this._shortDate(cell.day)} already has a one-off on it, and a
                one-off wins over the weekday - so this ${weekdayLabel} keeps
                what it has and the rest change.
              </div>`
            : ""}

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
    const key = cell.scope === "date" ? cell.dateKey : cell.weekday;
    const field = cell.scope === "date" ? "date_overrides" : "day_overrides";

    // The service replaces the whole dictionary, so the one day being edited is
    // merged onto what is stored *now* rather than onto anything this card
    // remembers writing. Read at save time, not at open time: the sensor is the
    // shared state, and a second tab or a service call from an automation may
    // have added an override since this dialog opened.
    const stored = this._ruleFor(this._attributes(), cell.choreId)[field];
    const overrides = this._withOverride(stored, key, cell.selected);

    if (await this._write({ chore_id: cell.choreId, [field]: overrides })) {
      this._cell = null;
    }
  }

  // --------------------------------------------------------- rule editing

  _openRule(attributes, choreId) {
    const stored = this._ruleFor(attributes, choreId);
    const roster = (attributes.children || []).length
      ? attributes.children
      : this._childRoster().map((child) => child.id);

    this._rule = {
      choreId,
      // Whether the sensor carries a rule for this chore at all. A chore it has
      // never heard of reads as all-defaults above, which is what it behaves
      // like but not necessarily what is on disk - see `_saveRule`.
      scheduled: (attributes.chores || []).includes(choreId),
      // Opened on the rule as stored, so the dialog says what this chore does
      // rather than asking a parent to type it all out again to change one bit
      // of it. Monday first, whatever order the days were stored in.
      dueDays: this._dueDaysOf(stored),
      rotation: stored.rotation,
      order: stored.rotation_order,
      anchor: stored.rotation_anchor,
      // A rotation can still name a child who has left the household. Showing
      // them is what lets a parent take them out; hiding them would leave the
      // name in the rule with nothing in the dialog to remove it by.
      children: [
        ...roster,
        ...stored.rotation_order.filter((childId) => !roster.includes(childId)),
      ],
      // What the rule said when this opened. Save diffs against it so a field
      // the parent only looked at is not sent back - see `_saveRule`.
      stored,
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
            : rule.rotation === "none"
              ? html`<div class="note">
                  No days picked means every day, which takes this chore off the
                  schedule entirely.
                </div>`
              : // Empty due days plus a rotation is still a schedule: the chore
                // falls every day and the turns below decide whose it is.
                html`<div class="note">
                  No days picked means every day, with the turns below still
                  deciding whose day it is.
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
            This is the chore's rule as it stands. Only what you change here is
            sent; anything you leave alone stays exactly as it is, one-off swaps
            included.
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

  /** Whether two lists hold the same ids in the same order. */
  _sameOrder(left, right) {
    return (
      left.length === right.length && left.every((item, index) => item === right[index])
    );
  }

  async _saveRule() {
    const rule = this._rule;
    const stored = rule.stored;
    const anchor = rule.anchor.trim();
    const storedDueDays = this._dueDaysOf(stored);
    const data = { chore_id: rule.choreId };

    // Only the fields the parent actually moved. Sending a field back because
    // the form displayed it is not the no-op it looks like: the ids in it were
    // read a dialog ago, and a household that has changed since would be
    // written back as it was.
    if (!this._sameOrder(rule.dueDays, storedDueDays)) {
      data.due_days = rule.dueDays;
    }
    if (rule.rotation !== stored.rotation) data.rotation = rule.rotation;
    if (!this._sameOrder(rule.order, stored.rotation_order)) {
      data.rotation_order = rule.order;
    }
    if (anchor !== stored.rotation_anchor) data.rotation_anchor = anchor;

    // Nothing moved, so there is nothing to say. Calling anyway would write a
    // storage revision and a coordinator refresh for a dialog the parent
    // opened to read.
    if (Object.keys(data).length === 1) {
      this._rule = null;
      return;
    }

    // Putting a chore the sensor does not carry back on a schedule. `stored` is
    // the all-defaults rule for such a chore, but only the fields `is_scheduled`
    // looks at are actually known to be at their default: a rotation order left
    // behind when this chore's due days were last cleared is still on disk, and
    // the diff above would decline to overwrite an order it believes is already
    // empty. So the turns are sent as the dialog showed them - `[]` included,
    // which is a value the service writes - rather than left to a rule the
    // parent cannot see revive itself the moment the chore is scheduled again.
    if (!rule.scheduled) {
      data.rotation_order = rule.order;
      data.rotation_anchor = anchor;
    }

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

      <div class="form-group">
        <label>Density</label>
        <select @change="${(event) => this._updateConfig("density", event.target.value)}">
          <option value="default" ?selected="${(this.config.density || "default") === "default"}">
            Default
          </option>
          <option value="cozy" ?selected="${this.config.density === "cozy"}">
            Cozy — a bit tighter
          </option>
          <option value="compact" ?selected="${this.config.density === "compact"}">
            Compact — fits a fortnight in less height
          </option>
        </select>
        <small>How much room the grid takes up</small>
      </div>
    `;
  }

  /** Values the card already assumes; writing them down would just add noise. */
  static LAYOUT_DEFAULTS = {
    density: "default",
  };

  _updateConfig(key, value) {
    const newConfig = { ...this.config, [key]: value };
    const isDefault = ChoremanderScheduleCardEditor.LAYOUT_DEFAULTS[key] === value;
    if (value === undefined || value === "" || isDefault) {
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
