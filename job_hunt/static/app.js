/* Anmol Job Hunt 2026 — client-side glue.
 * - Alpine root state on <html> (dark mode, help overlay)
 * - Keyboard shortcuts via window keydown
 * - Toast helpers
 * - Re-render Lucide icons after every HTMX swap
 */

function dashboardShell() {
  return {
    dark: false,
    helpOpen: false,
    selectedRow: -1,
    pendingG: false,        // 'G' chord: waiting for second key (R/C)

    init() {
      const saved = localStorage.getItem("dark");
      this.dark =
        saved === "1" ||
        (saved === null && window.matchMedia("(prefers-color-scheme: dark)").matches);
      this.applyDark();

      // Lucide icons after Alpine boots
      this.$nextTick(() => window.lucide && lucide.createIcons());

      // Refresh icons after HTMX swaps
      document.body.addEventListener("htmx:afterSwap", () => {
        if (window.lucide) lucide.createIcons();
        // Reset selection if rows changed
        this.selectedRow = -1;
        this.applyRowSelection();
      });

      // Toast on artifact-generation responses
      document.body.addEventListener("htmx:afterRequest", (e) => {
        const path = e.detail?.requestConfig?.path || "";
        if (e.detail?.successful && path.match(/\/jobs\/\d+\/(resume|cover-letter)$/)) {
          const kind = path.endsWith("/resume") ? "Resume" : "Cover letter";
          window.toast(`${kind} generated`, "success");
        }
        if (!e.detail?.successful && path.includes("/jobs/")) {
          window.toast("Action failed — check the server log", "error");
        }
      });

      this.bindKeys();
    },

    toggleDark() {
      this.dark = !this.dark;
      localStorage.setItem("dark", this.dark ? "1" : "0");
      this.applyDark();
    },
    applyDark() {
      document.documentElement.classList.toggle("dark", this.dark);
    },

    /* -------- keyboard shortcuts -------- */
    bindKeys() {
      window.addEventListener("keydown", (e) => {
        // Don't intercept while typing in an input/textarea/select
        const tag = (e.target.tagName || "").toLowerCase();
        const inField = ["input", "textarea", "select"].includes(tag) || e.target.isContentEditable;
        if (e.metaKey || e.ctrlKey || e.altKey) return;

        // '/' focuses the search input even from anywhere
        if (e.key === "/" && !inField) {
          e.preventDefault();
          const search = document.querySelector('input[name="q"]');
          if (search) { search.focus(); search.select(); }
          return;
        }

        if (inField) return;

        // 'G' chord
        if (this.pendingG) {
          this.pendingG = false;
          if (e.key.toLowerCase() === "r") return this.triggerOnSelected("resume");
          if (e.key.toLowerCase() === "c") return this.triggerOnSelected("cover-letter");
          return;
        }

        switch (e.key.toLowerCase()) {
          case "?": this.helpOpen = true; e.preventDefault(); return;
          case "escape": this.helpOpen = false; return;
          case "d": this.toggleDark(); return;
          case "r": this.click('button[hx-post="/refresh"]'); return;
          case "s": this.click('button[hx-post="/score-all"]'); return;
          case "j": this.moveSelection(1); e.preventDefault(); return;
          case "k": this.moveSelection(-1); e.preventDefault(); return;
          case "enter": this.openSelectedDrawer(); return;
          case "g": this.pendingG = true; setTimeout(() => (this.pendingG = false), 800); return;
          case "a": this.archiveSelected(); return;
          case "1": this.setStatusOnSelected("not_applied"); return;
          case "2": this.setStatusOnSelected("applied"); return;
          case "3": this.setStatusOnSelected("interview"); return;
          case "4": this.setStatusOnSelected("rejected"); return;
          case "5": this.setStatusOnSelected("offer"); return;
        }
      });
    },

    rows() { return [...document.querySelectorAll("[data-job-row]")]; },

    moveSelection(delta) {
      const rows = this.rows();
      if (!rows.length) return;
      this.selectedRow = Math.max(0, Math.min(rows.length - 1, this.selectedRow + delta));
      if (this.selectedRow < 0) this.selectedRow = 0;
      this.applyRowSelection();
      rows[this.selectedRow].scrollIntoView({ block: "nearest", behavior: "smooth" });
    },
    applyRowSelection() {
      this.rows().forEach((r, i) => r.classList.toggle("is-selected", i === this.selectedRow));
    },

    triggerOnSelected(kind) {
      const row = this.rows()[this.selectedRow];
      if (!row) return;
      const id = row.dataset.jobId;
      const btn = row.querySelector(`button[hx-post="/jobs/${id}/${kind}"]`);
      if (btn) btn.click();
    },
    setStatusOnSelected(status) {
      const row = this.rows()[this.selectedRow];
      if (!row) return;
      const sel = row.querySelector('select[name="status"]');
      if (sel) {
        sel.value = status;
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      }
    },
    archiveSelected() {
      const row = this.rows()[this.selectedRow];
      if (!row) return;
      const btn = row.querySelector('button[hx-post*="/archive"]');
      if (btn) btn.click();
    },
    openSelectedDrawer() {
      const row = this.rows()[this.selectedRow];
      if (!row) return;
      const link = row.querySelector('a[hx-get*="/jd"]');
      if (link) link.click();
    },

    click(selector) {
      const el = document.querySelector(selector);
      if (el) el.click();
    },
  };
}

/* -------- Toasts -------- */
window.toast = function (message, kind = "success", ttl = 3500) {
  const host = document.getElementById("toast-host");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<span>${kind === "error" ? "⚠" : "✓"}</span><span>${message}</span>`;
  host.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity 200ms";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 220);
  }, ttl);
};
