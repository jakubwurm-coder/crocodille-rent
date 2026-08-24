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
open CrocodilleFleet.xcodeproj
```

V Xcode zvol vlastní Team v Signing & Capabilities a spusť aplikaci na iPhonu.

## Notifikace
Aktuální první verze používá lokální notifikace. Po načtení dat aplikace naplánuje upozornění podle termínů z backendu. Další krok je serverový APNs push, který umožní upozornění i bez předchozího otevření aplikace po změně dat.
