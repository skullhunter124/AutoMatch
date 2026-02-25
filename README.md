# AutoMatch SI 🚗

AutoMatch SI is a modern, mobile-friendly car discovery tool specifically tailored for the Slovenian automotive market. It streamlines the car-buying journey by matching users with vehicles based on lifestyle, budget, and ownership costs through an intuitive, interactive interface.

The application is a pure frontend solution—it runs entirely in the browser and requires no backend infrastructure.

## 🛠 How It Works

AutoMatch SI guides users through a progressive, four-step flow to find their perfect match:

Vibe: Select a car category that fits your current lifestyle (e.g., City, Family, Adventure).

Filters: Apply realistic constraints including manufacturing year and previous ownership.

Budget: Define clear affordability limits.

Results: Swipe through a curated stack of matched cars, view details, and save your favorites.

All matching logic, scoring algorithms, and UI state management are handled client-side.

## Run with a local server (Python)

```bash
python -m http.server
```

Or use VS Code's **Live Server** extension.

---

## Features

| Feature | Description |
|---|---|
| 🎯 Progressive quiz | Vibe → Filters → Budget → Results |
| 🃏 Swipe cards | Tinder-style draggable cards (mouse + touch) |
| 💶 Maintenance Math | Estimated annual insurance + registration + service |
| 🇸🇮 Slovensko poreklo | Badge for Slovenian-origin cars |
| ❤️ Liked cars library | Save cars, compare yearly costs |
| 🔗 Share link | Encode your preferences in a URL |
| 🍋 Anti-Lemon filter | Filter out 3+ owner cars |

---

## File Structure

```
automatch-si/
├── index.html          # Main application (Logic, Styles, and HTML)
├── package.json        # Project metadata
├── README.md           # Project documentation
└── requirements.txt    # Requirements for the app to work
```

---

## Extending

- Add more car categories by editing `CATEGORIES` in `scraper.js`
- Adjust scoring weights in the `scoreCar()` function in `index.html`
- Add more model specs to `MODEL_SPECS` in `scraper.js`

## License
This project is licensed under the MIT License - see the LICENSE file for details