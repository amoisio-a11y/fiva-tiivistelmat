# fiva-tiivistelmat

Lähettää sähköpostin, kun Finanssivalvonta julkaisee uuden
[tarkastuksen tiivistelmän](https://www.finanssivalvonta.fi/finanssisektorin-toimijalle/finanssivalvonnan-valvontatyo/tarkastustoiminta/tarkastusten-tiivistelmat/).

Fivalla ei ole sivulle RSS-syötettä, ja tiivistelmälista latautuu JavaScriptillä.
Uudet tiivistelmät tunnistetaan siksi sivuston `sitemap.xml`:stä: jokainen
tiivistelmä on oma alasivunsa polussa `.../tarkastusten-tiivistelmat/<slug>`.

- **Ajo:** GitHub Actions kerran päivässä (klo 13.47 UTC) + käsin tai ulkoisella ajastimella
- **Tila:** `seen.json` (jo nähdyt URLit), jonka workflow commitoi takaisin repoon
- **Posti:** Gmail SMTP, yksi viesti per uusi tiivistelmä (yli 8 kerralla → yksi koosteviesti)
- **Riippuvuudet:** ei mitään, pelkkä Pythonin standardikirjasto

Vastaanottajan osoite ja Gmailin sovellussalasana ovat vain GitHub Secretseissä,
eivät koodissa. Lähettäjänä on sama Gmail-tili kuin Lounas-repossa.

## Käyttöönotto

1. **Luo repo** GitHubiin (esim. `fiva-tiivistelmat`) ja vie nämä tiedostot sinne.

2. **Lisää salaisuudet:** *Settings → Secrets and variables → Actions → New repository secret*

   | Nimi | Arvo |
   |---|---|
   | `GMAIL_APP_PASSWORD` | sama Gmail-sovellussalasana kuin Lounas-repossa |
   | `RECIPIENTS` | vastaanottaja; useampi pilkulla erotettuna |

3. **Aja kerran käsin:** *Actions → Tarkista uudet tiivistelmät → Run workflow*.
   Ensimmäinen ajo vain tallentaa nykyiset tiivistelmät `seen.json`-tiedostoon
   eikä lähetä mitään.

4. **Testaa posti (valinnainen):** poista yksi rivi `seen.json`-tiedostosta,
   commitoi ja aja workflow uudelleen. Siitä tiivistelmästä pitäisi tulla viesti.

## Ajastus

Workflowssa on GitHubin oma `schedule`-ajastus. GitHub kuitenkin **poistaa
ajastetut workflowt käytöstä julkisessa repossa 60 päivän toimettomuuden
jälkeen**, ja tähän repoon tulee committeja vain silloin, kun uusia
tiivistelmiä ilmestyy. Varmempi tapa on ulkoinen ajastin kuten Lounas-repossa:

- cron-job.org → `POST https://api.github.com/repos/<omistaja>/fiva-tiivistelmat/dispatches`
- Otsakkeet: `Authorization: Bearer <fine-grained token, Contents: read & write>`,
  `Accept: application/vnd.github+json`
- Body: `{"event_type": "check"}`

`repository_dispatch` ei ole 60 päivän säännön alainen.

## Paikallinen ajo

```bash
python scraper.py --dry-run     # näyttää uudet, ei lähetä eikä tallenna
```

## Vianetsintä

- **Workflow epäonnistuu viestillä "yhtään tiivistelmää ei löytynyt":** sitemapin
  tai URL-polun rakenne on muuttunut. Tilaa ei tyhjennetä, eikä viestejä lähetetä.
  GitHub lähettää epäonnistuneesta ajosta ilmoituksen repon omistajalle.
- **`git push` epäonnistuu (403):** *Settings → Actions → General → Workflow
  permissions → Read and write permissions*.
- **Tiivistelmän ingressi puuttuu viestistä:** otsikko ja linkki tulevat aina;
  ingressi poimitaan sivun kuvauksesta tai ensimmäisistä kappaleista, ja se voi
  jäädä tyhjäksi, jos sivupohja muuttuu.
