/**
 * Taxonomy and category filter chips component.
 */
export class CategoryFilter {
  constructor({ container, onFilterChange }) {
    this.container = container;
    this.onFilterChange = onFilterChange;
    this.activeFilter = "all";

    this.filters = [
      { id: "all", label: "All" },
      { id: "doorbell", label: "Doorbell" },
      { id: "person", label: "Person" },
      { id: "resident", label: "Residents" },
      { id: "courier", label: "Couriers" },
      { id: "pet", label: "Pets" },
      { id: "threat", label: "Threats" },
      { id: "audio", label: "Audio" },
    ];

    this.render();
  }

  setFilter(filterId) {
    this.activeFilter = filterId;
    this.render();
    if (this.onFilterChange) {
      this.onFilterChange(filterId);
    }
  }

  render() {
    if (!this.container) return;
    this.container.innerHTML = "";

    this.filters.forEach((f) => {
      const chip = document.createElement("button");
      chip.className = `filter-chip ${f.id === this.activeFilter ? "active" : ""}`;
      chip.dataset.cat = f.id;
      chip.textContent = f.label;

      chip.addEventListener("click", () => {
        this.setFilter(f.id);
      });

      this.container.appendChild(chip);
    });
  }
}
