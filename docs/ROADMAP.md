# NanoClaw Roadmap

Plánované funkce a architekturní změny.

---

## Mode MCP Server

**Status:** Planned
**Trigger:** Až bude druhý systém potřebovat znát aktuální režim (např. time blocking z kalendáře).

Aktuálně voice daemon drží `current_mode` (schránka/audio) jako lokální proměnnou. Až bude potřeba sdílet režim mezi více systémy, vyextrahovat do MCP serveru.

**Motivace:**
- Voice daemon, NanoClaw orchestrátor, kalendář/time blocking — všichni potřebují znát a měnit aktuální režim
- Automatické přepínání režimu podle time blocků v kalendáři
- Centrální stav místo synchronizace mezi procesy

**API návrh:**
- `get_mode` — vrátí aktuální režim
- `set_mode` — nastaví režim (s volitelným timeoutem pro auto-reset)
- `subscribe_mode` — notifikace při změně režimu

**Migrace:**
- Voice daemon: nahradit `current_mode` proměnnou za volání MCP serveru
- NanoClaw: může reagovat na změnu režimu (např. jiný system prompt)
- Kalendář: při začátku time blocku automaticky přepne režim
