# Onlinebibliotheek.nl Audiobooks - Complete Documentatie

## Overzicht

Deze documentatie beschrijft hoe je audioboeken van `onlinebibliotheek.nl` kunt ophalen en afspelen. Het proces combineert API calls (waar mogelijk) met HTML parsing (waar nodig).

## Belangrijke Bevindingen

### ✅ Echte API Endpoints

1. **Login API** - `POST https://login.kb.nl/si/login/api/authenticate`
   - Echte REST API
   - OAuth2 flow voor authorization
   - Returns: Authentication tokens

2. **OAuth2 API** - `GET https://login.kb.nl/si/auth/oauth2.0/v1/authorize`
   - Echte OAuth2 API
   - Returns: Authorization code

3. **Odilo Audio API** - `GET https://nubeplayer.eu.odilo.io/api/v1/media/{mediaId}/play?keyId={keyId}`
   - Echte REST API
   - Returns: JSON met audio URLs en metadata

### ⚠️ HTML Parsing (Geen API Beschikbaar)

1. **Catalogus** - Geen API beschikbaar
   - Website gebruikt Server-Side Rendering (SSR)
   - Data staat in HTML
   - Oplossing: HTML parsing met BeautifulSoup

2. **State Parameter** - Geen API beschikbaar
   - State parameter staat in HTML links
   - Oplossing: HTML parsing om state te extraheren

## Complete Flow

```
1. Login (API) ✅
   ↓
2. Catalogus Ophalen (HTML Parsing) ⚠️
   ↓
3. Voor elk luisterboek:
   a. State Parameter (HTML Parsing) ⚠️
   b. keyId Ophalen (API) ✅
   c. Audio URLs Ophalen (API) ✅
   ↓
4. Audio Afspelen (Direct MP3 URLs) ✅
```

## Stap-voor-Stap Uitleg

### Stap 1: Login

**Endpoint:** `POST https://login.kb.nl/si/login/api/authenticate`

**Payload:**
```json
{
  "module": "UsernameAndPassword",
  "definition": {
    "rememberMe": false,
    "username": "your_username",
    "password": "your_password"
  }
}
```

**Response:**
- JSON met authentication token
- Vervolgens OAuth2 flow voor authorization code
- Code wordt gewisseld voor `onlinebibliotheek.nl` cookies

**Belangrijke Cookies:**
- `TDP_SESSIONID` - Belangrijkste cookie voor authenticatie
- `loginstate` - Login status indicator

### Stap 2: Catalogus Ophalen

**URL:** `https://www.onlinebibliotheek.nl/account/boekenplank.html`

**Methode:** HTML Parsing (geen API beschikbaar)

**HTML Structuur:**
- Links naar boeken: `<a href="/catalogus/{ID}/title">`
- Cover images: `<img src="...">`
- Type detectie: Zoek naar "luister" of "e-book" in tekst

**Output:**
```json
{
  "id": "45557488X",
  "title": "De Kleine Zeemeermin",
  "url": "https://www.onlinebibliotheek.nl/catalogus/...",
  "type": "luisterboek",
  "image": "https://..."
}
```

### Stap 3: State Parameter Ophalen

**Methode:** HTML Parsing van boek pagina

**Zoek naar:**
- Links met `state=` parameter
- Alleen links met "luister" of "audio" in tekst
- Format: `/catalogus/download/redirect?state={STATE}`

**Waarom belangrijk:**
- State parameter is nodig om keyId op te halen
- State is uniek per boek en sessie

### Stap 4: keyId Ophalen

**URL:** `https://www.onlinebibliotheek.nl/catalogus/download/redirect?state={STATE}`

**Methode:** API call (302 redirect)

**Response:**
- 302 redirect naar: `https://nubeplayer.eu.odilo.io/get/{mediaId}/key/{keyId}`
- Parse redirect location om `mediaId` en `keyId` te extraheren

**Belangrijk:**
- keyId is tijdelijk (verloopt binnen minuten)
- Moet direct gebruikt worden na ophalen
- Cookies zijn nodig voor deze redirect

### Stap 5: Audio URLs Ophalen

**Endpoint:** `GET https://nubeplayer.eu.odilo.io/api/v1/media/{mediaId}/play?keyId={keyId}`

**Headers:**
```
Referer: https://nubeplayer.eu.odilo.io/get/{mediaId}/key/{keyId}
Origin: https://nubeplayer.eu.odilo.io
```

**Response:**
```json
{
  "metadata": {
    "title": "De Kleine Zeemeermin",
    "author": "Disney",
    "duration": "00u 16m12",
    "cover": "https://...",
    "synopsis": "..."
  },
  "resources": [
    {
      "title": "001",
      "format": "AUDIO",
      "duration": 972,
      "url": "https://d1ukfmmao1s9m3.cloudfront.net/.../001_001.mp3?..."
    }
  ]
}
```

**Belangrijk:**
- `resources` array bevat alle hoofdstukken/MP3 files
- Elke resource heeft een directe CloudFront URL
- URLs zijn signed URLs (tijdelijk geldig)

### Stap 6: Audio Afspelen

**Audio URLs:**
- Directe MP3 bestanden op CloudFront (AWS CDN)
- Format: `https://d1ukfmmao1s9m3.cloudfront.net/.../001_001.mp3?Policy=...&Signature=...`
- Kan direct gedownload of gestreamd worden

**Metadata:**
- Van API: title, author, duration, cover, synopsis
- Van MP3: ID3 tags (als aanwezig)

## Script Gebruik

### Makkelijkste Methode: Shell Script

```bash
cd bibliotheek
./get_audiobooks.sh
```

Het script vraagt om:
- Username
- Password (verborgen input)
- Output bestand (optioneel, default: `audiobooks.json`)

### Of met argumenten:

```bash
cd bibliotheek
./get_audiobooks.sh <username> <password> [output.json]
```

### Direct Python Script

```bash
# Activeer virtual environment
source venv/bin/activate

# Run script
cd bibliotheek
python get_all_audiobooks.py <username> <password> [output.json]
```

### Voorbeeld

```bash
./get_audiobooks.sh myuser mypass audiobooks.json
```

### Output

Het script genereert:
1. **JSON bestand** met:
   - Alle boeken uit je catalogus
   - Voor elk luisterboek: alle hoofdstukken met MP3 URLs
   - Metadata (title, author, duration, cover)
   - Download statistieken

2. **Downloads folder** (`bibliotheek/downloads/`) met:
   - Per audioboek een eigen folder
   - Alle MP3 bestanden georganiseerd per boek
   - Bestandsnamen: `01_Chapter_Title.mp3`, `02_Chapter_Title.mp3`, etc.

**Folder structuur:**
```
bibliotheek/
  downloads/
    Boek Titel 1/
      01_Hoofdstuk_1.mp3
      02_Hoofdstuk_2.mp3
    Boek Titel 2/
      01_Hoofdstuk_1.mp3
      ...
```

## Technische Details

### HTML Parsing vs API

**Waarom HTML Parsing?**
- Geen API beschikbaar voor catalogus
- Website gebruikt Server-Side Rendering
- Data staat al in HTML (geen client-side API calls)

**Is dit acceptabel?**
- ✅ Ja, het is de enige optie
- ✅ We gebruiken echte APIs waar mogelijk (login, audio)
- ✅ HTML parsing is netjes en functioneel
- ✅ BeautifulSoup maakt het robuust

**Alternatieven onderzocht:**
- ❌ Geen catalogus API gevonden
- ❌ Zoek API (`zoek.bnlapi.nl`) werkt niet voor catalogus
- ❌ JavaScript analyse toont geen API calls voor catalogus

### Authentication

**OAuth2 Flow:**
1. Authenticate bij `login.kb.nl`
2. Get authorization code
3. Exchange code voor `onlinebibliotheek.nl` cookies
4. Cookies gebruiken voor alle volgende requests

**Cookie Management:**
- Cookies zijn session-bound
- `TDP_SESSIONID` is httpOnly (kan niet via JavaScript)
- Cookies moeten in requests.Session worden bewaard

### keyId Expiry

**Belangrijk:**
- keyId verloopt snel (binnen minuten)
- Moet direct gebruikt worden na ophalen
- Voor elk hoofdstuk kan een nieuwe keyId nodig zijn

**Oplossing:**
- Haal keyId op vlak voor gebruik
- Gebruik keyId direct voor API call
- Cache niet te lang

### Audio Format

**MP3 Bestanden:**
- Standaard MP3 format
- Directe CloudFront URLs
- Signed URLs (tijdelijk geldig)
- Kan gestreamd worden (206 Partial Content)

**Metadata:**
- Van API: uitgebreide metadata
- Van MP3: ID3 tags (als aanwezig)
- Duration in seconden (van API)

## Veelgestelde Vragen

### Q: Waarom HTML parsing en niet alleen API?

A: Er is geen API beschikbaar voor catalogus. De website gebruikt Server-Side Rendering, dus alle data staat in de HTML. We gebruiken HTML parsing alleen waar nodig, en echte APIs voor login en audio.

### Q: Is HTML parsing betrouwbaar?

A: Ja, zolang de HTML structuur niet drastisch verandert. We gebruiken BeautifulSoup voor robuuste parsing, en de structuur is relatief stabiel.

### Q: Hoe lang blijven keyIds geldig?

A: keyIds verloopt binnen minuten. Haal ze op vlak voor gebruik en gebruik ze direct.

### Q: Kan ik audio downloaden?

A: Ja, de MP3 URLs zijn directe download links. Je kunt ze downloaden of streamen.

### Q: Werkt dit voor e-books?

A: Dit script is specifiek voor luisterboeken. E-books hebben een andere flow (download links, geen audio).

## Troubleshooting

### Login Fails

- Check username/password
- Check internet verbinding
- Check of login.kb.nl bereikbaar is

### No Books Found

- Check of je ingelogd bent
- Check of je geleende boeken hebt
- Check cookies (mogelijk verlopen)

### No State Parameter

- Check of het boek een luisterboek is
- Check of je het boek hebt geleend
- Check of de link "luister" of "audio" bevat

### keyId Expired

- Haal keyId opnieuw op
- Gebruik keyId direct na ophalen
- Check of cookies nog geldig zijn

### Audio URL Invalid

- Check of keyId nog geldig is
- Check of mediaId correct is
- Probeer opnieuw met nieuwe keyId

## Code Voorbeelden

### Login

```python
def login(username, password):
    session = requests.Session()
    
    # Get login page
    session.get("https://www.onlinebibliotheek.nl/account/inloggen.html")
    
    # Authenticate
    auth_data = {
        'module': 'UsernameAndPassword',
        'definition': {
            'rememberMe': False,
            'username': username,
            'password': password,
        }
    }
    session.post("https://login.kb.nl/si/login/api/authenticate", json=auth_data)
    
    # OAuth2 flow...
    
    return session
```

### Catalogus Ophalen

```python
def get_catalogus(session):
    response = session.get("https://www.onlinebibliotheek.nl/account/boekenplank.html")
    soup = BeautifulSoup(response.text, 'html.parser')
    
    books = []
    for link in soup.find_all('a', href=re.compile(r'/catalogus/\d+')):
        # Extract book info...
        books.append({...})
    
    return books
```

### Audio Ophalen

```python
def get_audio(session, media_id, key_id):
    api_url = f"https://nubeplayer.eu.odilo.io/api/v1/media/{media_id}/play?keyId={key_id}"
    response = session.get(api_url)
    return response.json()
```

## Best Practices

1. **Gebruik Session:** Bewaar cookies in `requests.Session`
2. **Fresh keyIds:** Haal keyId op vlak voor gebruik
3. **Error Handling:** Check status codes en responses
4. **Rate Limiting:** Voeg delays toe tussen requests
5. **Caching:** Cache catalogus, maar niet keyIds

## Toekomstige Verbeteringen

- [ ] Caching van catalogus
- [ ] Retry logic voor expired keyIds
- [ ] Progress tracking voor downloads
- [ ] ID3 tag extraction van MP3 files
- [ ] Streaming support (niet volledig downloaden)

## Referenties

- **Login API:** `https://login.kb.nl/si/login/api/authenticate`
- **Odilo API:** `https://nubeplayer.eu.odilo.io/api/v1/media/{id}/play`
- **Catalogus:** `https://www.onlinebibliotheek.nl/account/boekenplank.html`

## Licentie

Deze code is voor persoonlijk gebruik en proof-of-concept doeleinden.

