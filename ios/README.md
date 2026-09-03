# Crocodille Fleet iOS

První verze iPhone aplikace pro flotilu Crocodille.

## Co umí
- seznam vozidel z Render backendu
- hledání podle SPZ, VIN a ID
- detail vozidla
- STK, dálniční známka, pojištění, asistence a servis
- technické údaje z Datové kostky
- seznam upozornění na termíny do 30 dní
- lokální iOS notifikace po synchronizaci

## Backend API
- `https://vansrenting-crocodille.onrender.com/api/v1/health`
- `https://vansrenting-crocodille.onrender.com/api/v1/vehicles`
- `https://vansrenting-crocodille.onrender.com/api/v1/alerts?days=30`

## Xcode
Projekt je definovaný přes XcodeGen v `project.yml`.

Na Macu:

```bash
brew install xcodegen
cd ios
xcodegen generate
open VansRenting.xcodeproj
```

V Xcode zvol vlastní Team v Signing & Capabilities a spusť aplikaci na iPhonu.

## Notifikace – verze 1.1
- aplikace požádá o oprávnění při prvním spuštění
- upozornění plánuje na 30, 14, 7 a 1 den před termínem a v den termínu
- běžná upozornění se zobrazí i po zavření aplikace
- banner a zvuk se zobrazí také při otevřené aplikaci
- na kartě Upozornění je vidět stav oprávnění a počet naplánovaných oznámení
- testovací tlačítko vytvoří oznámení za 10 sekund
- Background App Refresh se pokouší průběžně stáhnout nové termíny z produkčního API

Po instalaci je nutné aplikaci alespoň jednou otevřít, povolit oznámení a ponechat zapnuté:
`Nastavení → Obecné → Aktualizace aplikací na pozadí → Vans Renting`.

iOS určuje přesný čas aktualizace na pozadí. Naplánované lokální notifikace už ale dorazí bez nutnosti mít aplikaci otevřenou.
