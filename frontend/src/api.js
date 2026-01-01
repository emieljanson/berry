// API configuration
const API_URL = window.location.hostname === 'localhost' 
  ? 'http://localhost:3001' 
  : `http://${window.location.hostname}:3001`

export const WS_URL = window.location.hostname === 'localhost'
  ? 'ws://localhost:3002'
  : `ws://${window.location.hostname}:3002`

// Helper for POST requests
const post = (endpoint, body = null) => 
  fetch(`${API_URL}${endpoint}`, {
    method: 'POST',
    ...(body && {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
  })

// API methods
export const api = {
  // Catalog
  getCatalog: () => 
    fetch(`${API_URL}/api/catalog`).then(r => r.json()),
  
  saveToCatalog: (item) => 
    post('/api/catalog', item).then(r => r.json()),
  
  deleteFromCatalog: (id) =>
    fetch(`${API_URL}/api/catalog/${id}`, { method: 'DELETE' }).then(r => r.json()),
  
  cleanupImages: () =>
    post('/api/cleanup-images').then(r => r.json()),

  // Playback
  play: (uri) => 
    post('/api/play', { uri }).then(r => r.json()),
  
  pause: () => post('/api/pause'),
  resume: () => post('/api/resume'),
  next: () => post('/api/next'),
  prev: () => post('/api/prev'),
  
  seek: (position) => 
    post('/api/seek', { position }),
}

// Image URL helper
export const getImageUrl = (path) => 
  path?.startsWith('/') ? `${API_URL}${path}` : path


