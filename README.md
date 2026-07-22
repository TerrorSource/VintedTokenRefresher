# Vinted token refresher

Houdt de `access_token_web` van je Vinted-Notifications container automatisch vers,
zodat die 24/7 blijft draaien zonder handmatig cookies plakken.

## Hoe het werkt
Elk uur roept dit containertje `POST https://www.vinted.nl/web/api/auth/refresh` aan
met je `refresh_token_web` cookie. Vinted geeft een verse access-token terug (2u geldig)
plus een geroteerde refresh-token. De access-token wordt in het `default_headers`-veld
van de Vinted-Notifications database gezet (als `Cookie: access_token_web=...`), de
nieuwe refresh-token + datadome worden op schijf bewaard in `state/state.json`.

De refresh-token leeft weken, en roteert elke keer mee — zolang het containertje
minstens eens per paar dagen draait, blijft de keten oneindig doorlopen.

## Eenmalige setup

1. Maak de state-map aan op je NAS:
   `/share/CACHEDEV1_DATA/Docker/vinted-token-refresher/state/`

2. Haal uit je browser (ingelogd op vinted.nl) via DevTools -> Application -> Cookies:
   - `refresh_token_web`  (de LANGE, purpose=refresh — NIET access_token_web)
   - `datadome`
   - `cf_clearance` (optioneel, helpt tegen Cloudflare)

3. Maak `state/state.json` met die waarden:
   ```json
   {
     "refresh_token_web": "PLAK_REFRESH_TOKEN",
     "datadome": "PLAK_DATADOME",
     "cf_clearance": "PLAK_CF_CLEARANCE"
   }
   ```

4. Zorg dat de `volumes` in docker-compose.yml naar DEZELFDE data-map wijzen als je
   vinted-notifications container (waar `vinted_notifications.db` staat).

5. Deploy in Portainer. Check de logs: je wilt zien
   `OK: access-token vernieuwd en in DB geschreven.`

6. Herstart daarna eenmalig je vinted-notifications container zodat die de nieuwe
   headers uit de DB oppikt. (Daarna niet meer nodig; de app leest de DB per scrape.)

## Belangrijk
- Zorg dat de Chrome-versie in USER_AGENT en in Sec-Ch-Ua (in refresh.py) overeenkomt
  met de fingerprint die je in de Vinted-Notifications UI hebt ingesteld.
- Werkt de refresh niet meer (bijv. na wachtwoordwijziging of lange downtime), dan is
  de refresh-token vervallen: herhaal stap 2-3 met verse cookies uit de browser.
