import express from 'express';
import cors from 'cors';
import { readFile, writeFile, mkdir, readdir } from 'fs/promises';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import WebSocket, { WebSocketServer } from 'ws';
import crypto from 'crypto';

const __dirname = dirname(fileURLToPath(import.meta.url));

const app = express();
const PORT = 3001;
const WS_PORT = 3002;
const LIBRESPOT_URL = 'http://localhost:3678';
const LIBRESPOT_WS = 'ws://localhost:3678/events';

// ============================================
// WebSocket server for frontend clients
// ============================================

const frontendClients = new Set();

// Get current state for broadcasting to frontends
async function getCurrentState() {
  try {
    const response = await fetch(`${LIBRESPOT_URL}/status`);
    
    if (response.status === 204) {
      return { 
        playing: false, 
        track: null,
        context: null 
      };
    }
    
    const status = await response.json();
    
    const trackInfo = status.track ? {
      uri: status.track.uri,
      name: status.track.name,
      artist: status.track.artist_names?.join(', '),
      artistNames: status.track.artist_names,
      album: status.track.album_name,
      albumCover: status.track.album_cover_url,
      duration: status.track.duration,
      position: status.track.position
    } : null;
    
    // Get collected covers for current context (for playlist composite images)
    let covers = null;
    if (currentState.contextUri && contextCovers.has(currentState.contextUri)) {
      covers = Array.from(contextCovers.get(currentState.contextUri).values());
    }
    
    return {
      playing: !status.stopped && !status.paused,
      paused: status.paused,
      stopped: status.stopped,
      buffering: status.buffering || false,
      track: trackInfo,
      context: {
        // Use intendedContextUri if set (for resume playback), otherwise use the actual context
        uri: currentState.intendedContextUri || currentState.contextUri,
        playOrigin: currentState.playOrigin,
        covers: covers // Array of local image paths for playlist composite
      },
      volume: status.volume,
      volumeSteps: status.volume_steps
    };
  } catch (error) {
    console.error('Error getting current state:', error.message);
    return null;
  }
}

// Broadcast state to all connected frontend clients
async function broadcastState() {
  if (frontendClients.size === 0) return;
  
  const state = await getCurrentState();
  if (!state) return;
  
  const message = JSON.stringify(state);
  
  frontendClients.forEach(client => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(message);
    }
  });
}

// Start WebSocket server for frontends
function startFrontendWebSocket() {
  const wss = new WebSocketServer({ port: WS_PORT });
  
  wss.on('connection', async (client) => {
    console.log('🌐 Frontend client connected');
    frontendClients.add(client);
    
    // Send current state immediately on connect
    const state = await getCurrentState();
    if (state && client.readyState === WebSocket.OPEN) {
      client.send(JSON.stringify(state));
    }
    
    client.on('close', () => {
      console.log('🌐 Frontend client disconnected');
      frontendClients.delete(client);
    });
    
    client.on('error', (err) => {
      console.error('Frontend client error:', err.message);
      frontendClients.delete(client);
    });
  });
  
  console.log(`🌐 Frontend WebSocket server running on ws://localhost:${WS_PORT}`);
  
  // Broadcast state every 1 second for position updates
  setInterval(broadcastState, 1000);
}

// Paths for data storage
const CATALOG_PATH = join(__dirname, 'data', 'catalog.json');
const IMAGES_PATH = join(__dirname, 'data', 'images');

app.use(cors());
app.use(express.json());

// ============================================
// State - bijgehouden via WebSocket events
// ============================================

let currentState = {
  contextUri: null,      // playlist/album URI (from websocket)
  trackUri: null,        // current track URI
  playOrigin: null,      // where playback started from
  lastUpdate: null,
  intendedContextUri: null  // The catalog item URI we intend to play (for resume)
};

// Event listeners for play confirmation
// Used by /api/play to wait for 'playing' event before returning
let playingEventListeners = [];

// Track covers per context (for playlist composite images)
// Map<contextUri, Map<hash, localImagePath>>
const contextCovers = new Map();

// Global set of image hashes we've already saved (prevents duplicates)
const savedImageHashes = new Map(); // hash -> localImagePath

// ============================================
// Progress tracking - saved directly in catalog items
// This allows resuming at the exact track + position
// ============================================

// Save current playback position to catalog item
async function saveCurrentPosition(contextUri, trackUri, position) {
  if (!contextUri || !trackUri || position === undefined) return;
  
  try {
    const catalog = await loadCatalog();
    const item = catalog.items.find(i => i.uri === contextUri);
    
    if (item) {
      item.currentTrack = {
        uri: trackUri,
        position: position,
        updatedAt: new Date().toISOString()
      };
      await saveCatalog(catalog);
      return true;
    }
  } catch (err) {
    console.error('Error saving position to catalog:', err.message);
  }
  return false;
}

// Get saved position from catalog item (only if updated within 24 hours)
async function getSavedPosition(contextUri) {
  try {
    const catalog = await loadCatalog();
    const item = catalog.items.find(i => i.uri === contextUri);
    
    if (!item?.currentTrack) {
      return null;
    }
    
    // Check if the saved position is within 24 hours
    const savedAt = new Date(item.currentTrack.updatedAt);
    const now = new Date();
    const hoursSinceUpdate = (now - savedAt) / (1000 * 60 * 60);
    
    if (hoursSinceUpdate > 24) {
      // Position is older than 24 hours, clear it and return null
      delete item.currentTrack;
      await saveCatalog(catalog);
      return null;
    }
    
    return item.currentTrack;
  } catch (err) {
    return null;
  }
}

// Periodically save progress (every 10 seconds when playing)
let lastSavedPosition = 0;
let lastSavedTrack = null;
setInterval(async () => {
  try {
    const response = await fetch(`${LIBRESPOT_URL}/status`);
    if (response.status === 204) return;
    
    const status = await response.json();
    if (status.stopped || status.paused || !status.track) return;
    
    const position = status.track.position;
    const trackUri = status.track.uri;
    
    // Save if track changed OR position changed significantly (> 5 seconds)
    const trackChanged = trackUri !== lastSavedTrack;
    const positionChanged = Math.abs(position - lastSavedPosition) > 5000;
    
    if (trackChanged || positionChanged) {
      const saved = await saveCurrentPosition(
        currentState.contextUri,
        trackUri,
        position
      );
      if (saved) {
        lastSavedPosition = position;
        lastSavedTrack = trackUri;
        console.log(`💾 Saved progress: track ${trackUri.split(':').pop().slice(0,8)}... @ ${Math.floor(position / 1000)}s`);
      }
    }
  } catch (err) {
    // Ignore errors during progress saving
  }
}, 10000);

// ============================================
// WebSocket connection to go-librespot
// ============================================

let ws = null;
let wsReconnectTimer = null;

function connectWebSocket() {
  console.log('🔌 Connecting to go-librespot WebSocket...');
  
  ws = new WebSocket(LIBRESPOT_WS);
  
  ws.on('open', () => {
    console.log('✅ Connected to go-librespot WebSocket');
    if (wsReconnectTimer) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
  });
  
  ws.on('message', (data) => {
    try {
      const event = JSON.parse(data.toString());
      handleLibrespotEvent(event).catch(err => console.error('Error handling librespot event:', err));
    } catch (err) {
      console.error('Error parsing WebSocket message:', err);
    }
  });
  
  ws.on('error', (err) => {
    console.error('WebSocket error:', err.message);
  });
  
  ws.on('close', () => {
    console.log('WebSocket closed, reconnecting in 5s...');
    ws = null;
    wsReconnectTimer = setTimeout(connectWebSocket, 5000);
  });
}

async function handleLibrespotEvent(event) {
  console.log(`📡 Event: ${event.type}`);
  
  // #region agent log
  fetch('http://127.0.0.1:7244/ingest/794c737d-9e69-4ad3-b236-3e681c4ae44f',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'server.js:267',message:'Librespot event received',data:{type:event.type,contextUri:event.data?.context_uri,trackUri:event.data?.uri,playOrigin:event.data?.play_origin},timestamp:Date.now(),sessionId:'debug-session',runId:'run1',hypothesisId:'C'})}).catch(()=>{});
  // #endregion
  
  let shouldBroadcast = false;
  
  switch (event.type) {
    case 'will_play':
      // DON'T update context here - will_play is just "trying to play"
      // If play fails (code 2 DRM error), we'd have wrong context
      // Only log what we're TRYING to play
      console.log(`   Trying context: ${event.data?.context_uri}`);
      console.log(`   Trying track: ${event.data?.uri}`);
      currentState.playOrigin = event.data?.play_origin || currentState.playOrigin;
      currentState.lastUpdate = new Date().toISOString();
      break;
      
    case 'playing':
      // Only update context when actually playing (play succeeded)
      if (event.data?.context_uri) {
        currentState.contextUri = event.data.context_uri;
        console.log(`   ✓ Now playing context: ${currentState.contextUri}`);
        
        // Notify any waiting play requests that playback started
        // Match on context URI (the catalog item we wanted to play)
        const contextUri = event.data.context_uri;
        playingEventListeners = playingEventListeners.filter(listener => {
          if (listener.contextUri === contextUri) {
            listener.resolve({ success: true, context: contextUri });
            return false; // Remove this listener
          }
          return true; // Keep listener
        });
        
        // Collect album covers for playlists (for composite images)
        if (currentState.contextUri.includes('playlist')) {
          collectCoverForContext(currentState.contextUri);
        }
      }
      if (event.data?.uri) {
        currentState.trackUri = event.data.uri;
        
        // Also check if any listener is waiting for this track's context
        // This handles the case where we play a track and get its album/playlist as context
        playingEventListeners = playingEventListeners.filter(listener => {
          // If playing event has a context that matches, resolve it
          if (event.data?.context_uri === listener.contextUri) {
            listener.resolve({ success: true, context: listener.contextUri });
            return false;
          }
          return true;
        });
      }
      shouldBroadcast = true; // Important state change - broadcast immediately
      break;
      
    case 'paused':
    case 'stopped':
      // Update track URI if provided, but keep context
      if (event.data?.uri) {
        currentState.trackUri = event.data.uri;
      }
      // Only update context if explicitly provided (shouldn't change on pause/stop)
      if (event.data?.context_uri) {
        currentState.contextUri = event.data.context_uri;
      }
      
      // #region agent log
      fetch('http://127.0.0.1:7244/ingest/794c737d-9e69-4ad3-b236-3e681c4ae44f',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'server.js:378',message:'Stopped/Paused event',data:{eventType:event.type,contextUri:currentState.contextUri,trackUri:currentState.trackUri,eventContextUri:event.data?.context_uri},timestamp:Date.now(),sessionId:'debug-session',runId:'run3',hypothesisId:'E'})}).catch(()=>{});
      // #endregion
      
      // If stopped and we had an intended context, clear it (album/playlist finished)
      if (event.type === 'stopped' && currentState.intendedContextUri) {
        // #region agent log
        fetch('http://127.0.0.1:7244/ingest/794c737d-9e69-4ad3-b236-3e681c4ae44f',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'server.js:390',message:'Album/playlist finished - clearing intended context',data:{intendedContext:currentState.intendedContextUri,contextUri:currentState.contextUri},timestamp:Date.now(),sessionId:'debug-session',runId:'run3',hypothesisId:'E'})}).catch(()=>{});
        // #endregion
        
        currentState.intendedContextUri = null;
      }
      
      shouldBroadcast = true; // Important state change - broadcast immediately
      break;
      
    case 'metadata':
      // Metadata events might also have context
      if (event.data?.context_uri) {
        currentState.contextUri = event.data.context_uri;
      }
      shouldBroadcast = true;
      break;
  }
  
  // Broadcast state immediately on important events
  if (shouldBroadcast) {
    broadcastState();
  }
}

// Download image and return hash + buffer
async function downloadAndHashImage(imageUrl) {
  const imageResponse = await fetch(imageUrl);
  const imageBuffer = Buffer.from(await imageResponse.arrayBuffer());
  const hash = crypto.createHash('md5').update(imageBuffer).digest('hex');
  return { hash, buffer: imageBuffer };
}

// Collect album cover for a context (playlist)
async function collectCoverForContext(contextUri) {
  try {
    // Get current track info from REST API
    const response = await fetch(`${LIBRESPOT_URL}/status`);
    if (response.status === 204) return;
    
    const status = await response.json();
    const coverUrl = status.track?.album_cover_url;
    
    if (coverUrl) {
      // Initialize map for this context if needed
      if (!contextCovers.has(contextUri)) {
        contextCovers.set(contextUri, new Map());
      }
      
      const covers = contextCovers.get(contextUri);
      // Only keep first 4 unique covers (by hash)
      if (covers.size < 4) {
        // Download and hash the image
        const { hash, buffer } = await downloadAndHashImage(coverUrl);
        
        // Skip if we already have this exact image for this context
        if (covers.has(hash)) {
          return;
        }
        
        // Check if this image is already saved globally
        let localPath;
        if (savedImageHashes.has(hash)) {
          // Reuse existing image
          localPath = savedImageHashes.get(hash);
          console.log(`   📸 Reusing existing cover (hash: ${hash.slice(0,8)}...) ${covers.size + 1}/4 for playlist`);
        } else {
          // Save new image
          const imageFilename = `${Date.now()}-${hash.slice(0,8)}.jpg`;
          const imagePath = join(IMAGES_PATH, imageFilename);
          await writeFile(imagePath, buffer);
          localPath = `/images/${imageFilename}`;
          savedImageHashes.set(hash, localPath);
          console.log(`   📸 Saved new cover (hash: ${hash.slice(0,8)}...) ${covers.size + 1}/4 for playlist`);
        }
        
        covers.set(hash, localPath);
        
        // Check if this playlist is already saved and needs more covers
        await updatePlaylistCoversIfNeeded(contextUri, hash, localPath);
      }
    }
  } catch (err) {
    console.error('Error collecting cover:', err.message);
  }
}

// Update saved playlist with new covers progressively
async function updatePlaylistCoversIfNeeded(contextUri, hash, localPath) {
  try {
    const catalog = await loadCatalog();
    const item = catalog.items.find(i => i.uri === contextUri);
    
    // Only update if it's a saved playlist with < 4 covers
    if (!item || item.type !== 'playlist') return;
    if (item.images && item.images.length >= 4) return;
    
    const currentImages = item.images || [];
    
    // Check if this image path is already in the catalog item
    if (currentImages.includes(localPath)) return;
    
    // Update the catalog item with the already-saved image path
    item.images = [...currentImages, localPath];
    await saveCatalog(catalog);
    
    console.log(`   📸 Updated saved playlist cover ${item.images.length}/4`);
  } catch (err) {
    console.error('Error updating playlist covers:', err.message);
  }
}

// Start WebSocket connection
connectWebSocket();

// ============================================
// Now Playing - combines WebSocket + REST data
// ============================================

app.get('/api/now-playing', async (req, res) => {
  try {
    // Get full status from REST API
    const response = await fetch(`${LIBRESPOT_URL}/status`);
    
    if (response.status === 204) {
      return res.json({ 
        playing: false, 
        track: null,
        context: null 
      });
    }
    
    const status = await response.json();
    
    // Build track info from live data only
    const trackInfo = status.track ? {
      uri: status.track.uri,
      name: status.track.name,
      artist: status.track.artist_names?.join(', '),
      artistNames: status.track.artist_names,
      album: status.track.album_name,
      albumCover: status.track.album_cover_url,
      duration: status.track.duration,
      position: status.track.position
    } : null;
    
    // Combine with WebSocket state (for context_uri)
    res.json({
      playing: !status.stopped && !status.paused,
      paused: status.paused,
      stopped: status.stopped,
      buffering: status.buffering || false,
      track: trackInfo,
      context: {
        uri: currentState.contextUri,
        playOrigin: currentState.playOrigin
      },
      volume: status.volume,
      volumeSteps: status.volume_steps
    });
  } catch (error) {
    console.error('Error fetching now-playing:', error.message);
    res.status(500).json({ error: 'Could not get now playing info' });
  }
});

// ============================================
// Player Controls
// ============================================

app.post('/api/play', async (req, res) => {
  try {
    const { uri, fromBeginning } = req.body;
    console.log(`▶️ Play request: ${uri}`);
    
    // Save current position before switching context
    if (currentState.contextUri && currentState.contextUri !== uri) {
      try {
        const statusRes = await fetch(`${LIBRESPOT_URL}/status`);
        if (statusRes.ok) {
          const status = await statusRes.json();
          if (status.track && !status.stopped) {
            await saveCurrentPosition(
              currentState.contextUri,
              status.track.uri,
              status.track.position
            );
            console.log(`💾 Saved position before context switch: ${Math.floor(status.track.position / 1000)}s`);
          }
        }
      } catch (err) {
        // Ignore errors when saving before switch
      }
    }
    
    // Check if we have saved progress for this context (stored in catalog item)
    const savedProgress = fromBeginning ? null : await getSavedPosition(uri);
    
    // Determine what to play:
    // - If we have a saved track, use skip_to_uri parameter to start at that track within the context
    // - Otherwise play the context from the beginning
    let isResumingTrack = false;
    const playBody = { uri: uri }; // Always use the context URI
    
    if (savedProgress?.uri) {
      // Use skip_to_uri to start at the saved track within the context
      playBody.skip_to_uri = savedProgress.uri;
      isResumingTrack = true;
      console.log(`📍 Resuming at track: ${savedProgress.uri} in context: ${uri}`);
    }
    
    // Store the intended context URI (the catalog item we want to play)
    currentState.intendedContextUri = uri;
    
    // Create a promise that resolves when we get a 'playing' event
    // We need to listen for the context URI OR the track URI
    const playConfirmation = new Promise((resolve, reject) => {
      const listener = { contextUri: uri, resolve };
      playingEventListeners.push(listener);
      
      // Timeout after 3 seconds
      setTimeout(() => {
        const index = playingEventListeners.indexOf(listener);
        if (index > -1) {
          playingEventListeners.splice(index, 1);
          resolve({ success: false, context: uri, reason: 'timeout' });
        }
      }, 3000);
    });
    
    // Send play request to go-librespot
    // Use skip_to_uri parameter to start at a specific track within the context
    const response = await fetch(`${LIBRESPOT_URL}/player/play`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(playBody)
    });
    
    if (!response.ok) {
      console.log(`❌ Play request failed: ${response.status}`);
      playingEventListeners = playingEventListeners.filter(l => l.contextUri !== uri);
      return res.json({ success: false, context: uri, reason: 'request_failed' });
    }
    
    // Wait for confirmation (playing event or timeout)
    const result = await playConfirmation;
    
    if (result.success) {
      console.log(`✅ Play confirmed for: ${uri}`);
      
      // If resuming at a track, seek to the saved position
      if (isResumingTrack && savedProgress?.position > 0) {
        console.log(`📍 Seeking to position: ${Math.floor(savedProgress.position / 1000)}s`);
        await fetch(`${LIBRESPOT_URL}/player/seek`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ position: savedProgress.position })
        });
      }
      
      // Disable repeat - albums/playlists should stop when finished
      try {
        // #region agent log
        fetch('http://127.0.0.1:7244/ingest/794c737d-9e69-4ad3-b236-3e681c4ae44f',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'server.js:593',message:'Setting repeat mode to off',data:{contextUri:uri},timestamp:Date.now(),sessionId:'debug-session',runId:'run1',hypothesisId:'A'})}).catch(()=>{});
        // #endregion
        
        const repeatResponse = await fetch(`${LIBRESPOT_URL}/player/repeat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ type: 'off' })
        });
        
        // #region agent log
        fetch('http://127.0.0.1:7244/ingest/794c737d-9e69-4ad3-b236-3e681c4ae44f',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'server.js:600',message:'Repeat mode response',data:{status:repeatResponse.status,ok:repeatResponse.ok,contextUri:uri},timestamp:Date.now(),sessionId:'debug-session',runId:'run1',hypothesisId:'A'})}).catch(()=>{});
        // #endregion
      } catch (err) {
        // #region agent log
        fetch('http://127.0.0.1:7244/ingest/794c737d-9e69-4ad3-b236-3e681c4ae44f',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'server.js:602',message:'Repeat mode error',data:{error:err.message,contextUri:uri},timestamp:Date.now(),sessionId:'debug-session',runId:'run1',hypothesisId:'A'})}).catch(()=>{});
        // #endregion
        // Ignore repeat mode errors
      }
    } else {
      console.log(`⚠️ Play not confirmed (timeout) for: ${uri}`);
    }
    
    res.json({ 
      success: result.success, 
      context: uri,
      resumed: isResumingTrack,
      resumedTrack: savedProgress?.uri,
      position: savedProgress?.position
    });
  } catch (error) {
    console.error('Error playing:', error.message);
    res.status(500).json({ error: 'Could not play', success: false });
  }
});

app.post('/api/pause', async (req, res) => {
  try {
    // Save current position before pausing (to catalog item)
    const statusRes = await fetch(`${LIBRESPOT_URL}/status`);
    if (statusRes.ok) {
      const status = await statusRes.json();
      if (status.track && currentState.contextUri) {
        const saved = await saveCurrentPosition(
          currentState.contextUri,
          status.track.uri,
          status.track.position
        );
        if (saved) {
          console.log(`💾 Saved to catalog on pause: ${status.track.uri.split(':').pop().slice(0,8)}... @ ${Math.floor(status.track.position / 1000)}s`);
        }
      }
    }
    
    await fetch(`${LIBRESPOT_URL}/player/pause`, { method: 'POST' });
    res.json({ success: true });
  } catch (error) {
    console.error('Error pausing:', error.message);
    res.status(500).json({ error: 'Could not pause' });
  }
});

app.post('/api/resume', async (req, res) => {
  try {
    await fetch(`${LIBRESPOT_URL}/player/resume`, { method: 'POST' });
    res.json({ success: true });
  } catch (error) {
    console.error('Error resuming:', error.message);
    res.status(500).json({ error: 'Could not resume' });
  }
});

app.post('/api/next', async (req, res) => {
  try {
    const response = await fetch(`${LIBRESPOT_URL}/player/next`, { method: 'POST' });
    res.json({ success: response.ok });
  } catch (error) {
    console.error('Error skipping:', error.message);
    res.status(500).json({ error: 'Could not skip' });
  }
});

app.post('/api/prev', async (req, res) => {
  try {
    await fetch(`${LIBRESPOT_URL}/player/prev`, { method: 'POST' });
    res.json({ success: true });
  } catch (error) {
    console.error('Error going back:', error.message);
    res.status(500).json({ error: 'Could not go back' });
  }
});

app.post('/api/seek', async (req, res) => {
  try {
    const { position } = req.body;
    await fetch(`${LIBRESPOT_URL}/player/seek`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ position })
    });
    res.json({ success: true });
  } catch (error) {
    console.error('Error seeking:', error.message);
    res.status(500).json({ error: 'Could not seek' });
  }
});

app.post('/api/volume', async (req, res) => {
  try {
    const { volume } = req.body;
    await fetch(`${LIBRESPOT_URL}/player/volume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ volume })
    });
    res.json({ success: true });
  } catch (error) {
    console.error('Error setting volume:', error.message);
    res.status(500).json({ error: 'Could not set volume' });
  }
});

// Get saved progress for a context (from catalog)
app.get('/api/progress/:uri', async (req, res) => {
  try {
    const uri = decodeURIComponent(req.params.uri);
    const progress = await getSavedPosition(uri);
    res.json(progress || { position: 0 });
  } catch (error) {
    console.error('Error getting progress:', error.message);
    res.status(500).json({ error: 'Could not get progress' });
  }
});

// Get all saved progress (from catalog items, only within 24 hours)
app.get('/api/progress', async (req, res) => {
  try {
    const catalog = await loadCatalog();
    const progress = {};
    const now = new Date();
    let catalogChanged = false;
    
    for (const item of catalog.items) {
      if (item.currentTrack) {
        // Check if the saved position is within 24 hours
        const savedAt = new Date(item.currentTrack.updatedAt);
        const hoursSinceUpdate = (now - savedAt) / (1000 * 60 * 60);
        
        if (hoursSinceUpdate <= 24) {
          progress[item.uri] = item.currentTrack;
        } else {
          // Remove old progress
          delete item.currentTrack;
          catalogChanged = true;
        }
      }
    }
    
    // Save catalog if we removed old progress
    if (catalogChanged) {
      await saveCatalog(catalog);
    }
    
    res.json(progress);
  } catch (error) {
    console.error('Error getting progress:', error.message);
    res.status(500).json({ error: 'Could not get progress' });
  }
});

// Clear progress for a context (start from beginning)
app.delete('/api/progress/:uri', async (req, res) => {
  try {
    const uri = decodeURIComponent(req.params.uri);
    const catalog = await loadCatalog();
    const item = catalog.items.find(i => i.uri === uri);
    
    if (item && item.currentTrack) {
      delete item.currentTrack;
      await saveCatalog(catalog);
      console.log(`🗑️ Cleared progress for: ${item.name}`);
    }
    
    res.json({ success: true });
  } catch (error) {
    console.error('Error clearing progress:', error.message);
    res.status(500).json({ error: 'Could not clear progress' });
  }
});

// ============================================
// Mirror Catalog
// ============================================

// Ensure data directories exist
async function ensureDataDirs() {
  try {
    await mkdir(join(__dirname, 'data'), { recursive: true });
    await mkdir(IMAGES_PATH, { recursive: true });
  } catch (err) {
    // Directories might already exist
  }
}

// Index existing images on startup for deduplication
// Extracts hash from filename (format: timestamp-hash.jpg) instead of re-reading files
async function indexExistingImages() {
  try {
    await ensureDataDirs();
    const files = await readdir(IMAGES_PATH);
    let indexed = 0;
    
    for (const file of files) {
      if (!file.endsWith('.jpg')) continue;
      
      // Extract hash from filename: "1767089701460-6aa1f146.jpg" -> "6aa1f146"
      const match = file.match(/-([a-f0-9]{8})\.jpg$/);
      if (match) {
        const hash = match[1];
        const localPath = `/images/${file}`;
        if (!savedImageHashes.has(hash)) {
          savedImageHashes.set(hash, localPath);
          indexed++;
        }
      }
    }
    
    console.log(`📁 Indexed ${indexed} images from filenames`);
  } catch (err) {
    console.error('Error indexing images:', err.message);
  }
}

// Run on startup
indexExistingImages();

// Load catalog
async function loadCatalog() {
  try {
    const data = await readFile(CATALOG_PATH, 'utf-8');
    return JSON.parse(data);
  } catch (err) {
    // Return empty catalog if file doesn't exist
    return { items: [] };
  }
}

// Save catalog
async function saveCatalog(catalog) {
  await writeFile(CATALOG_PATH, JSON.stringify(catalog, null, 2));
}

// GET catalog
app.get('/api/catalog', async (req, res) => {
  try {
    const catalog = await loadCatalog();
    res.json(catalog);
  } catch (error) {
    console.error('Error loading catalog:', error.message);
    res.status(500).json({ error: 'Could not load catalog' });
  }
});

// POST - add to catalog
app.post('/api/catalog', async (req, res) => {
  try {
    const { type, uri, name, artist, album, image } = req.body;
    
    if (!type || !uri || !name) {
      return res.status(400).json({ error: 'Missing required fields: type, uri, name' });
    }
    
    const catalog = await loadCatalog();
    
    // Check for duplicates
    const exists = catalog.items.some(item => item.uri === uri);
    if (exists) {
      return res.status(409).json({ error: 'Item already in catalog', duplicate: true });
    }
    
    let localImage = null;
    let localImages = null;
    
    // For playlists: use collected covers if available (already saved locally with dedup)
    if (type === 'playlist' && contextCovers.has(uri)) {
      // contextCovers Map values are already local paths
      const covers = Array.from(contextCovers.get(uri).values());
      if (covers.length > 0) {
        localImages = covers;
        localImage = covers[0];
        console.log(`💾 Using ${covers.length} pre-collected covers for playlist`);
      }
    }
    
    // Download single image if no composite images (albums)
    if (!localImage && image) {
      try {
        const { hash, buffer } = await downloadAndHashImage(image);
        
        // Check if we already have this image
        if (savedImageHashes.has(hash)) {
          localImage = savedImageHashes.get(hash);
          console.log(`💾 Reusing existing image (hash: ${hash.slice(0,8)}...): ${localImage}`);
        } else {
          const imageFilename = `${Date.now()}-${hash.slice(0,8)}.jpg`;
          const imagePath = join(IMAGES_PATH, imageFilename);
          await writeFile(imagePath, buffer);
          localImage = `/images/${imageFilename}`;
          savedImageHashes.set(hash, localImage);
          console.log(`💾 Saved new image: ${localImage}`);
        }
      } catch (err) {
        console.error('Error saving image:', err.message);
        localImage = image; // Fall back to original URL
      }
    }
    
    // Add new item
    const newItem = {
      id: Date.now().toString(),
      type,        // 'track', 'album', or 'playlist'
      uri,
      name,
      artist: artist || null,
      album: album || null,
      image: localImage || image,
      images: localImages || null,  // Array of images for playlist composite
      originalImage: image,
      addedAt: new Date().toISOString()
    };
    
    catalog.items.push(newItem);
    await saveCatalog(catalog);
    
    // Keep collecting covers for playlists even after saving
    // They will be progressively added via updatePlaylistCoversIfNeeded
    
    console.log(`📥 Added to catalog: ${name} (${type})`);
    res.json({ success: true, item: newItem });
  } catch (error) {
    console.error('Error adding to catalog:', error.message);
    res.status(500).json({ error: 'Could not add to catalog' });
  }
});

// DELETE - remove from catalog
app.delete('/api/catalog/:id', async (req, res) => {
  try {
    const { id } = req.params;
    const catalog = await loadCatalog();
    
    const index = catalog.items.findIndex(item => item.id === id);
    if (index === -1) {
      return res.status(404).json({ error: 'Item not found' });
    }
    
    const removed = catalog.items.splice(index, 1)[0];
    await saveCatalog(catalog);
    
    // Progress is stored in the catalog item itself, so it's automatically removed
    console.log(`🗑️ Removed from catalog: ${removed.name}`);
    res.json({ success: true, removed });
  } catch (error) {
    console.error('Error removing from catalog:', error.message);
    res.status(500).json({ error: 'Could not remove from catalog' });
  }
});

// Serve local images
app.use('/images', express.static(IMAGES_PATH));

// ============================================
// Start server
// ============================================

app.listen(PORT, () => {
  console.log(`🍓 Berry backend running on http://localhost:${PORT}`);
  console.log(`   Connecting to go-librespot at ${LIBRESPOT_URL}`);
  
  // Start WebSocket server for frontend clients
  startFrontendWebSocket();
});
