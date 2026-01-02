# Berry Carousel - Complete Specification

## What is Berry?
A child-friendly Spotify player with a touch interface. A carousel displays album/playlist covers. The child swipes to a cover and it starts playing automatically.

## Core Principles
1. **Simple and bulletproof** - minimal state, clear flow
2. **What you see = what plays** - cover and track info are ALWAYS in sync
3. **Resume where you left off** - unless >24h ago or album/playlist finished
4. **Debuggable** - every action is traceable with clear logs

---

## Architecture

### Why Split Components?
The current Carousel.jsx does too much: UI, timers, sync logic, event handling. This makes debugging difficult. We split into layers:

```
┌─────────────────────────────────────────────────────┐
│  App.jsx                                            │
│  - Holds nowPlaying state (from WebSocket)          │
│  - Holds items (catalog)                            │
│  - Handles play requests                            │
│  - Shows toasts                                     │
└─────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  Carousel.jsx (Container)                           │
│  - Connects UI with logic                           │
│  - Passes callbacks through                         │
│  - Minimal own state                                │
└─────────────────────────────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ CarouselUI  │  │ Playback    │  │ Debug       │
│             │  │ Timer       │  │ Logger      │
│ - Embla     │  │             │  │             │
│ - Covers    │  │ - 1 sec     │  │ - Log all   │
│ - Animation │  │   delay     │  │   events    │
│ - Swipe     │  │ - Cancel    │  │ - State     │
│   physics   │  │ - Trigger   │  │   snapshots │
└─────────────┘  └─────────────┘  └─────────────┘
```

### File Structure
```
frontend/src/
  components/
    Carousel.jsx          # Container component with inline timer logic
    CarouselSlide.jsx     # Single cover slide (optional extraction)
  hooks/
    useCarouselDebug.js   # Debug logging (optional)
```

### Embla Carousel Behavior (KEEP)
The current swipe feel is good:
- Hard swipe → scroll multiple items
- Momentum scrolling
- Snap to center
- Items outside viewport reachable in one swipe

**Important Embla events:**
- `onSelect` - When a new slide is selected (during scroll)
- `onSettle` - When the carousel STOPS moving and "settles" on a slide

**Timer trigger**: We start the 1-sec timer on `onSettle`, NOT on `onSelect`. This prevents the timer from starting during scrolling.

---

## Data Flow

### Swipe → Play Flow
```
1. User swipes
   │
   ▼
2. Embla: onSelect(index: 2)
   │ → UI update: show cover 2 in center
   │ → UI update: show saved track info (or loading)
   │
   ▼
3. Embla: onSettle(index: 2) [carousel stops moving]
   │
   ▼
4. startPlayTimer(item)
   │ → If item already playing: skip
   │ → Otherwise: start 1 sec timer
   │
   ▼
5. [Wait 1 second - user can still swipe away]
   │
   ▼
6. Timer fired → onPlay(item)
   │
   ▼
7. App.jsx: POST /api/play
   │
   ▼
8. Backend: play request to go-librespot
   │
   ▼
9. go-librespot: WebSocket → "playing" event
   │
   ▼
10. Backend: broadcast to frontend
    │
    ▼
11. App.jsx: nowPlaying state update
    │
    ▼
12. UI: shows actual track info
```

### Failure Flow (back to previous)
```
7. App.jsx: POST /api/play
   │
   ▼
8. Backend: 403/404 from Spotify
   │
   ▼
9. App.jsx: onPlay returns {success: false}
   │
   ▼
10. App.jsx: showToast("Not available")
    │
    ▼
11. App.jsx: scrollToPlayingItem()
    │
    ▼
12. Carousel: emblaApi.scrollTo(playingIndex)
    │
    ▼
13. UI: back to previous cover + track info
```

---

## The Basic Flow

### Normal Navigation
1. Album A is playing, shows "Track 3 - Artist", cover A is centered
2. Child swipes right → Album B comes to center
3. UI shows IMMEDIATELY: 
   - Cover B
   - IF B has saved progress → "Track 8 - Artist" (from catalog)
   - OTHERWISE → loading indicator (briefly)
4. After 1 second STATIONARY → API call to play B
5. WebSocket reports "B is playing" → UI shows actual track info

### Why 1 Second Delay?
Prevents rate limits when swiping quickly:
```
A → B → C (fast swipe, <1 sec per step)
Only C is started = 1 API call
```

---

## All Scenarios with Expected Behavior

### Scenario 1: Simple Navigation (with saved progress)
```
Starting state: Album A playing "Track 3"
Action: Swipe to Album B (has saved progress: Track 8)
Expected:
  1. IMMEDIATELY: Cover B in center, text shows "Track 8 - Artist" (from catalog.currentTrack)
  2. Timer starts (1 second)
  3. After 1 sec: POST /api/play {uri: "spotify:album:B"}
  4. Backend resumes at Track 8
  5. WebSocket confirms → UI keeps showing "Track 8 - Artist"
```

### Scenario 1b: Simple Navigation (without saved progress)
```
Starting state: Album A playing "Track 3"
Action: Swipe to Album C (no saved progress)
Expected:
  1. IMMEDIATELY: Cover C in center, loading indicator (or nothing)
  2. Timer starts (1 second)
  3. After 1 sec: POST /api/play {uri: "spotify:album:C"}
  4. Backend starts from beginning
  5. WebSocket sends track info → UI shows "Track 1 - Artist"
```

### Scenario 2: Fast Swiping (A → B → C)
```
Starting state: Album A playing
Action: Swipe A→B, then within 1 sec B→C
Expected:
  1. At B: Timer starts for B
  2. At C (<1 sec later): Timer for B is CANCELLED
  3. At C: New timer starts for C
  4. After 1 sec stationary on C: Only C is played
  5. B is NEVER played
```

### Scenario 3: Back to Playing Item (A → B → A)
```
Starting state: Album A playing "Track 5"
Action: Swipe to B, then within 1 sec back to A
Expected:
  1. At B: Timer starts for B
  2. Back at A: Timer for B is CANCELLED
  3. A is already playing, so: NO API call
  4. UI shows "Track 5 from A" again (from WebSocket data)
```

### Scenario 4: Page Refresh While Playing
```
Starting state: Album C playing "Track 5"
Action: Page refresh (F5)
Expected:
  1. Page loads, fetches catalog + now-playing
  2. Carousel SCROLLS AUTOMATICALLY to Album C
  3. UI shows "Track 5 from C"
  4. Everything is in sync
```

### Scenario 5: Play Request Fails (album not available)
```
Starting state: Album A playing "Track 3"
Action: Swipe to Album X (not available on account)
Expected:
  1. UI shows saved track from X (or loading if none saved)
  2. After 1 sec: API call
  3. Backend gets 403/404 from Spotify
  4. Frontend gets {success: false, reason: "unavailable"}
  5. Toast: "Not available" (3 sec)
  6. Carousel SCROLLS BACK to Album A (still playing)
  7. UI shows "Track 3 - Artist" again (from A)
```

### Scenario 6: Play Request Fails (network error)
```
Starting state: Album A playing "Track 3"
Action: Swipe to Album B, but network fails
Expected:
  1. UI shows saved track from B (or loading if none saved)
  2. After 1 sec: API call fails
  3. Frontend gets error or {success: false, reason: "network_error"}
  4. Toast: "Cannot play" (3 sec)
  5. Carousel SCROLLS BACK to Album A
  6. UI shows "Track 3 - Artist" again (from A)
```

### Scenario 7: Resume Where You Left Off
```
Starting state: Album B was played 2 hours ago until Track 8, 1:30
Action: Swipe to Album B
Expected:
  1. After 1 sec: API call with saved progress
  2. Backend plays B from Track 8
  3. Backend seeks to 1:30 position
  4. UI shows "Track 8 from B"
```

### Scenario 8: Saved Progress Too Old (>24h)
```
Starting state: Album B was played 3 days ago
Action: Swipe to Album B
Expected:
  1. Backend sees progress is >24h old
  2. Backend CLEARS the old progress
  3. Album B plays from the BEGINNING (Track 1)
```

### Scenario 9: Album/Playlist Finished
```
Starting state: Album B was fully played
Context: go-librespot automatically went to another album (autoplay)
Detection: Context changed WITHOUT user action
Expected:
  1. Backend detects: context change without user play request
  2. Backend CLEARS the progress for B
  3. Next time B → starts from beginning
```

### Scenario 10: Clicking on Cover
```
Action: Click on cover that is NOT in the center
Expected: Carousel scrolls to that cover (same as swiping)

Action: Click on cover that IS in the center
Expected: Toggle play/pause
```

### Scenario 11: Backend Restart
```
Starting state: Album C playing, backend is restarted
Problem: Backend loses in-memory state (currentState.contextUri = null)
Expected:
  1. Backend recovers context from catalog:
     - Match playing track URI against catalog items
     - Or match album name against catalog items
  2. contextUri is correct again
  3. Frontend refresh → everything works
```

---

## State Model

### Frontend State (App.jsx)
```javascript
{
  items: [                // Catalog items, each with optional saved progress
    {
      id, uri, name, artist, image,
      currentTrack: {     // Saved progress (optional, from backend)
        uri: "spotify:track:abc",
        name: "Track 8",
        artist: "Carry Slee",
        position: 45000,
        updatedAt: "2024-..."
      }
    }
  ],
  nowPlaying: {           // From WebSocket - actual playback status
    contextUri: "spotify:album:xyz",
    isPlaying: true,
    track: { uri, name, artist, albumName, position, duration }
  },
  pendingItem: null,      // Item we're navigating to (for UI)
  toast: null
}
```

### Frontend State (Carousel.jsx)
```javascript
{
  selectedIndex: 2,       // Which cover is centered
  playTimer: null         // The 1-sec timer (useRef)
}
```

### Backend State (server.js)
```javascript
currentState = {
  contextUri: null,       // What's now playing (from WebSocket)
  intendedContextUri: null, // What we WANT to play (from play request)
  trackUri: null,
  isPlaying: false,
  track: {}
}
```

### Catalog item.currentTrack (saved in catalog.json)
```javascript
// MUST contain track name and artist for direct UI display!
{
  uri: "spotify:track:abc",
  name: "Track 8",           // ← Track name
  artist: "Carry Slee",      // ← Artist
  position: 45000,
  updatedAt: "2024-01-02T..."
}
```

---

## UI Display Logic

**Important principles:**
- Album/playlist names are NOT relevant for children
- Always show track name + artist
- Use saved progress to immediately show the correct track

```
What do I show as track info?

IF selected item has savedTrack (from catalog.currentTrack):
  → Show savedTrack.name + artist (we'll continue from there)
  
ELSE IF nowPlaying.track exists AND contextUri matched:
  → Show nowPlaying.track.name + artist
  
ELSE:
  → Show "Track 1" or first track info if known
  → Or brief loading state until WebSocket update
```

### Example Flow:
```
1. Album B has saved progress: "Track 8 - Mrs Wormwood"
2. Child swipes to B
3. UI shows IMMEDIATELY: "Track 8 - Mrs Wormwood" (from catalog)
4. After play: WebSocket confirms → stays "Track 8"

1. Album C has NO saved progress
2. Child swipes to C  
3. UI shows: loading spinner or empty text (very brief, ~1-2 sec)
4. After play: WebSocket sends → "Track 1 - ..."
```

### Loading State (when no saved progress):
- Option A: Empty text / no track info shown
- Option B: Subtle loading indicator ("Loading...")
- Option C: Skeleton loader
- **NEVER**: Album name or "Playlist" shown

---

## API Endpoints

### POST /api/play
Request:
```json
{ "uri": "spotify:album:xyz" }
```

Response (success):
```json
{ "success": true, "context": "spotify:album:xyz" }
```

Response (failure):
```json
{ 
  "success": false, 
  "context": "spotify:album:xyz",
  "reason": "unavailable" | "network_error" | "timeout"
}
```

### GET /api/now-playing
Response:
```json
{
  "contextUri": "spotify:album:xyz",
  "isPlaying": true,
  "track": {
    "uri": "spotify:track:abc",
    "name": "Track Name",
    "artist": "Artist",
    "albumName": "Album",
    "position": 45000,
    "duration": 180000
  }
}
```

---

## Important Implementation Details

### 1. Playback Timer (inline in Carousel.jsx)
```javascript
// Timer logic is inline in Carousel.jsx using useRef
const playTimerRef = useRef(null)

const startPlayTimer = useCallback((item) => {
  // Cancel existing timer
  if (playTimerRef.current) {
    log('Timer cancelled (new item selected)')
    clearTimeout(playTimerRef.current)
  }
  
  // Don't start timer if this item is already playing
  if (isItemPlaying(item)) {
    log('Timer skipped (item already playing)')
    return
  }
  
  log('Timer starting for:', item.name)
  
  playTimerRef.current = setTimeout(async () => {
    playTimerRef.current = null
    
    // Double-check it's still not playing
    if (isItemPlaying(item)) {
      log('Timer fired but item now playing, skipping')
      return
    }
    
    log('Timer fired! Playing:', item.name)
    await onPlay(item)
  }, 1000)
}, [isItemPlaying, onPlay])
```

### 2. Scroll Back on Failure
```javascript
// In App.jsx
async function handlePlay(item) {
  const result = await api.play(item.uri)
  
  if (!result.success) {
    showToast(getErrorMessage(result.reason))
    
    // Scroll back to playing item
    const playingIndex = findPlayingIndex()
    if (playingIndex >= 0) {
      setScrollToIndex(playingIndex)
    }
  }
  
  return result
}
```

### 3. Context Recovery (Backend)
```javascript
// When go-librespot sends playing event but we lost context
function findContextFromCatalog(trackUri, albumName) {
  const catalog = loadCatalog()
  
  // First try to match by stored progress trackUri
  for (const item of catalog.items) {
    if (item.currentTrack?.uri === trackUri) {
      return item.uri
    }
  }
  
  // Then try to match by album name
  for (const item of catalog.items) {
    if (item.name === albumName || item.album === albumName) {
      return item.uri
    }
  }
  
  return null
}
```

### 4. Auto-finish Detection
```javascript
// In backend WebSocket handler
// Detect when context changes without user action
if (newContextUri !== currentState.intendedContextUri && 
    newContextUri !== currentState.contextUri) {
  // Context changed automatically (autoplay at end of album)
  // Clear saved progress for the old context
  clearSavedPosition(currentState.contextUri)
}
```

---

## Testing Checklist

- [ ] Swipe A→B: B plays after 1 sec
- [ ] Swipe A→B→C fast: only C plays
- [ ] Swipe A→B→A fast: nothing changes, A keeps playing
- [ ] Refresh: carousel shows playing item
- [ ] Resume: track position is restored
- [ ] Resume >24h: starts from beginning
- [ ] Album finished: next time starts from beginning
- [ ] Play failure: scroll back, toast shown
- [ ] Click on other cover: scroll + play
- [ ] Click on active cover: toggle pause
- [ ] Hard swipe: scroll multiple items, settles correctly

---

## Troubleshooting

### Problem: "Cannot play" toast but music plays anyway
**Symptoms:** Toast appears, but track plays anyway
**Cause:** Backend sends `success: false` before go-librespot is ready
**Debug steps:**
1. Check backend log: `[play] go-librespot: status=???`
2. Check WebSocket: does a playing event come after?
**Fix:** Backend must wait for WebSocket confirmation, not just HTTP response

### Problem: Wrong track info shown
**Symptoms:** Cover X but track info from Y
**Cause:** Race condition between UI update and WebSocket
**Debug steps:**
1. Check `[state]` logs - what is the state at each moment?
2. Check timing of `onSelect` vs WebSocket message
**Fix:** pendingItem should determine track info until WebSocket confirms

### Problem: Timer didn't fire / fired too early
**Symptoms:** Play request doesn't come or comes during scrolling
**Debug steps:**
1. Check `[timer]` logs - is timer started? cancelled?
2. Check if `onSettle` is fired
**Fix:** Timer should only start on `onSettle`, not on `onSelect`

### Problem: Carousel doesn't scroll to playing item on refresh
**Symptoms:** After refresh, wrong cover is in center
**Debug steps:**
1. Check `[state] nowPlaying` - is contextUri correct?
2. Check if item is in catalog
3. Check `emblaApi.scrollTo()` call
**Fix:** Backend must recover context from catalog

### Problem: Resume doesn't work
**Symptoms:** Always starts from beginning instead of saved position
**Debug steps:**
1. Check catalog.json - is currentTrack saved?
2. Check `[resume]` log - is skip_to_uri being sent?
3. Check go-librespot log - does it accept skip_to_uri?
**Fix:** Possibly currentTrack is >24h old or track URI is invalid

---

## Debugging Strategy

### Debug Mode
```javascript
// At the top of each file:
const DEBUG = import.meta.env.DEV // Enabled in development only

function log(...args) {
  if (DEBUG) console.log(...args)
}
```

### State Snapshots
At each important moment, log the complete state:
```javascript
function logState(event) {
  log(`[${event}]`, {
    selectedIndex,
    nowPlaying: { uri: nowPlaying?.contextUri, track: nowPlaying?.track?.name },
    pendingTimer: playTimerRef.current ? 'active' : 'none',
    items: items.map(i => i.name.slice(0, 20))
  })
}
```

### Event Tracing
Each user action gets a unique ID so you can follow the flow:
```javascript
const actionId = Date.now()
log(`[${actionId}] User swiped to index 2`)
// ... later ...
log(`[${actionId}] Timer fired, starting play`)
log(`[${actionId}] Play response: success`)
```

---

## Logging Requirements

### Backend Logs
```
[play] Request: {uri: "spotify:album:xyz", skip_to_uri: "spotify:track:abc"}
[play] go-librespot: status=200
[play] Started: spotify:album:xyz
[resume] Track: "Chapter 8" @ 45s
[context] Changed: album:abc → album:xyz (reason: user_request)
[context] Changed: album:xyz → album:def (reason: autoplay - clearing progress)
[progress] Saved: "Chapter 8" @ 1:23 in album:xyz
[progress] Cleared: album:xyz (finished)
[ws] Client connected
[ws] Broadcasting: {type: "playing", track: "..."}
```

### Frontend Logs - Carousel
```
🎠 [carousel] Embla initialized, items: 3
🎠 [carousel] onSelect: index 0 → 2
🎠 [carousel] onSettle: index 2 (Album: "Mrs Wormwood")
⏱️ [timer] Starting 1s timer for index 2
⏱️ [timer] Cancelled (user navigated away)
⏱️ [timer] Fired! Triggering play for index 2
```

### Frontend Logs - App
```
▶️ [play] Request: spotify:album:xyz
✅ [play] Success
❌ [play] Failed: unavailable
↩️ [play] Scrolling back to playing item (index 0)
🔌 [ws] Connected
🔌 [ws] Message: {isPlaying: true, track: "Chapter 8"}
📊 [state] nowPlaying updated: album:xyz, "Chapter 8"
```

### Log Levels
For production we can use levels:
```javascript
const LOG_LEVEL = 'debug' // 'none' | 'error' | 'info' | 'debug'

function log(level, ...args) {
  const levels = { none: 0, error: 1, info: 2, debug: 3 }
  if (levels[level] <= levels[LOG_LEVEL]) {
    console.log(`[${level.toUpperCase()}]`, ...args)
  }
}
```


