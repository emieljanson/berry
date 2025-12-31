import { useState, useEffect, useCallback, useRef } from 'react'
import useEmblaCarousel from 'embla-carousel-react'
import './App.css'

// API URL for REST endpoints
const API_URL = window.location.hostname === 'localhost' 
  ? 'http://localhost:3001' 
  : `http://${window.location.hostname}:3001`

// WebSocket URL for real-time state updates
const WS_URL = window.location.hostname === 'localhost'
  ? 'ws://localhost:3002'
  : `ws://${window.location.hostname}:3002`

function App() {
  const [catalog, setCatalog] = useState({ items: [] })
  const [nowPlaying, setNowPlaying] = useState(null)
  const [saving, setSaving] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [tempItem, setTempItem] = useState(null)

  // Embla carousel
  const [emblaRef, emblaApi] = useEmblaCarousel({
    loop: false,
    align: 'center',
    containScroll: false,
    skipSnaps: true,
    dragFree: false,
    watchDrag: true,
    watchResize: true,
  })

  // Fetch catalog periodically
  useEffect(() => {
    const fetchCatalog = async () => {
      try {
        const res = await fetch(`${API_URL}/api/catalog`)
        const data = await res.json()
        const filteredItems = (data.items || []).filter(item => item.type !== 'track')
        setCatalog({ ...data, items: filteredItems })
      } catch (err) {
        console.error('Error fetching catalog:', err)
      }
    }
    
    fetchCatalog()
    const interval = setInterval(fetchCatalog, 10000)
    return () => clearInterval(interval)
  }, [])

  // WebSocket connection for real-time state updates
  useEffect(() => {
    let ws = null
    let reconnectTimeout = null
    
    function connect() {
      console.log('🔌 Connecting to WebSocket...')
      ws = new WebSocket(WS_URL)
      
      ws.onopen = () => {
        console.log('✅ WebSocket connected')
      }
      
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          setNowPlaying(data)
        } catch (err) {
          console.error('Error parsing WebSocket message:', err)
        }
      }
      
      ws.onclose = () => {
        console.log('WebSocket closed, reconnecting in 1s...')
        reconnectTimeout = setTimeout(connect, 1000)
      }
      
      ws.onerror = (err) => {
        console.error('WebSocket error:', err)
        ws.close()
      }
    }
    
    connect()
    
    return () => {
      if (reconnectTimeout) clearTimeout(reconnectTimeout)
      if (ws) ws.close()
    }
  }, [])

  // Create displayItems: catalog items + optional temp item
  const displayItems = tempItem 
    ? [...catalog.items, tempItem]
    : catalog.items

  // Helper: find a catalog item that matches the current playback
  // Matches by context URI or by currentTrack.uri (for resumed playback)
  const findMatchingCatalogItem = useCallback((contextUri, trackUri) => {
    // First try to match by context URI (album/playlist)
    let match = catalog.items.find(item => item.uri === contextUri)
    
    // If not found, try to match by currentTrack.uri
    // This handles the case where we resumed a track from a saved position
    if (!match && trackUri) {
      match = catalog.items.find(item => item.currentTrack?.uri === trackUri)
    }
    
    return match
  }, [catalog.items])

  // Manage tempItem: create when playing something not in catalog
  useEffect(() => {
    const contextUri = nowPlaying?.context?.uri
    const trackUri = nowPlaying?.track?.uri
    
    if (!contextUri && !trackUri) {
      // No context or track - clear temp item
      setTempItem(null)
      return
    }
    
    // Check if this playback matches a catalog item
    const matchedItem = findMatchingCatalogItem(contextUri, trackUri)
    
    if (matchedItem) {
      // Found in catalog - clear temp item
      setTempItem(null)
      return
    }
    
    // Not in catalog - create temp item
    if (contextUri) {
      const isPlaylist = contextUri.includes('playlist')
      const track = nowPlaying.track
      const contextCovers = nowPlaying.context?.covers || []
      
      setTempItem(prevTempItem => {
        const newTempItem = {
          id: 'temp',
          type: isPlaylist ? 'playlist' : 'album',
          uri: contextUri,
          name: track ? (isPlaylist ? 'Playlist' : track.album) : 'Laden...',
          artist: track?.artist || '',
          image: track?.albumCover || null,
          images: isPlaylist ? contextCovers : null,
          isTemp: true
        }
        
        // Only update if something actually changed
        if (!prevTempItem || 
            prevTempItem.uri !== newTempItem.uri ||
            prevTempItem.name !== newTempItem.name ||
            prevTempItem.artist !== newTempItem.artist ||
            prevTempItem.image !== newTempItem.image ||
            (prevTempItem.images?.length || 0) !== (newTempItem.images?.length || 0)) {
          return newTempItem
        }
        
        return prevTempItem
      })
    }
  }, [nowPlaying?.context?.uri, nowPlaying?.track?.uri, nowPlaying?.context?.covers, nowPlaying?.track?.album, nowPlaying?.track?.artist, nowPlaying?.track?.albumCover, findMatchingCatalogItem])

  // Refs for carousel state
  const isSyncingRef = useRef(false)
  const lastSyncedContextRef = useRef(null)
  const displayItemsRef = useRef(displayItems)
  
  useEffect(() => {
    displayItemsRef.current = displayItems
  }, [displayItems])

  // Helper: find index of item matching the current playback
  const findPlayingIndex = useCallback((items, contextUri, trackUri) => {
    if (!contextUri && !trackUri) return -1
    
    // Try matching by context URI
    let index = items.findIndex(item => item.uri === contextUri)
    
    // Try matching by currentTrack.uri
    if (index === -1 && trackUri) {
      index = items.findIndex(item => item.currentTrack?.uri === trackUri)
    }
    
    // Check if tempItem matches (always last)
    if (index === -1 && tempItem && (tempItem.uri === contextUri || tempItem.uri === trackUri)) {
      index = items.length - 1
    }
    
    return index
  }, [tempItem])

  // Handle carousel selection change
  const onSelect = useCallback(() => {
    if (!emblaApi) return
    setSelectedIndex(emblaApi.selectedScrollSnap())
  }, [emblaApi])

  // Sync carousel to playing context (only when context CHANGES)
  useEffect(() => {
    if (!emblaApi || displayItems.length === 0 || isSyncingRef.current) return
    
    const contextUri = nowPlaying?.context?.uri
    const trackUri = nowPlaying?.track?.uri
    const playingKey = contextUri || trackUri
    
    // Clear ref if nothing playing, or skip if already synced
    if (!playingKey) {
      lastSyncedContextRef.current = null
      return
    }
    if (playingKey === lastSyncedContextRef.current) return
    
    const playingIndex = findPlayingIndex(displayItems, contextUri, trackUri)
    if (playingIndex === -1) return
    
    // Mark as synced
    lastSyncedContextRef.current = playingKey
    
    // Skip scroll if already at correct position
    if (emblaApi.selectedScrollSnap() === playingIndex) return
    
    // Scroll to playing item
    isSyncingRef.current = true
    emblaApi.scrollTo(playingIndex)
    setTimeout(() => { isSyncingRef.current = false }, 500)
  }, [emblaApi, nowPlaying?.context?.uri, nowPlaying?.track?.uri, displayItems, findPlayingIndex])

  // Check if an item is currently playing
  const isItemPlaying = useCallback((item) => {
    return item?.uri === nowPlaying?.context?.uri
  }, [nowPlaying?.context?.uri])

  // Play an item if not already playing
  const selectAndPlay = useCallback(async (item) => {
    if (!item || isItemPlaying(item)) return
    
    // Clear tempItem when selecting a catalog item
    if (!item.isTemp) setTempItem(null)
    
    try {
      const res = await fetch(`${API_URL}/api/play`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uri: item.uri })
      })
      const result = await res.json()
      if (!result.success) console.warn('Play failed:', result.reason)
    } catch (err) {
      console.error('Error playing:', err)
    }
  }, [isItemPlaying])

  // Handle settle (carousel stops) - play selected item
  const onSettle = useCallback(() => {
    if (!emblaApi || isSyncingRef.current) return
    const item = displayItemsRef.current[emblaApi.selectedScrollSnap()]
    selectAndPlay(item)
  }, [emblaApi, selectAndPlay])

  // Click on slide - scroll to it and play
  const onSlideClick = useCallback((index) => {
    if (!emblaApi) return
    emblaApi.scrollTo(index)
    selectAndPlay(displayItemsRef.current[index])
  }, [emblaApi, selectAndPlay])

  // Subscribe to carousel events
  useEffect(() => {
    if (!emblaApi) return
    
    emblaApi.on('select', onSelect)
    emblaApi.on('settle', onSettle)
    onSelect()
    
    return () => {
      emblaApi.off('select', onSelect)
      emblaApi.off('settle', onSettle)
    }
  }, [emblaApi, onSelect, onSettle])

  // Reinitialize carousel when displayItems count changes
  useEffect(() => {
    if (!emblaApi || displayItems.length === 0) return
    
    const contextUri = nowPlaying?.context?.uri
    const trackUri = nowPlaying?.track?.uri
    const targetIndex = findPlayingIndex(displayItems, contextUri, trackUri)
    
    // Prevent events during reInit
    isSyncingRef.current = true
    emblaApi.reInit()
    
    // Scroll to correct position instantly
    if (targetIndex !== -1) {
      emblaApi.scrollTo(targetIndex, true)
      // Mark as synced so the sync effect doesn't re-scroll
      lastSyncedContextRef.current = contextUri || trackUri
    }
    
    setTimeout(() => { isSyncingRef.current = false }, 100)
  }, [emblaApi, displayItems.length, nowPlaying?.context?.uri, nowPlaying?.track?.uri, findPlayingIndex])

  // Player controls - simple fire-and-forget
  const pause = () => fetch(`${API_URL}/api/pause`, { method: 'POST' })
  const resume = () => fetch(`${API_URL}/api/resume`, { method: 'POST' })
  const next = () => fetch(`${API_URL}/api/next`, { method: 'POST' })
  
  const prev = () => {
    const position = nowPlaying?.track?.position || 0
    if (position < 10000) {
      return fetch(`${API_URL}/api/prev`, { method: 'POST' })
    } else {
      return fetch(`${API_URL}/api/seek`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ position: 0 })
      })
    }
  }
  
  const togglePlayPause = () => {
    if (nowPlaying?.playing) {
      return pause()
    } else if (nowPlaying?.paused) {
      return resume()
    } else {
      const selectedItem = displayItems[selectedIndex]
      if (selectedItem) {
        return playItem(selectedItem.uri)
      }
    }
  }

  // Save context to catalog
  const saveContext = async () => {
    const contextUri = nowPlaying?.context?.uri
    if (!contextUri || !nowPlaying?.track) return
    
    setSaving(true)
    
    try {
      const isPlaylist = contextUri.includes('playlist')
      const isAlbum = contextUri.includes('album')
      
      const res = await fetch(`${API_URL}/api/catalog`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: isPlaylist ? 'playlist' : 'album',
          uri: contextUri,
          name: isAlbum ? nowPlaying.track.album : 'Playlist',
          artist: nowPlaying.track.artist,
          album: nowPlaying.track.album,
          image: nowPlaying.track.albumCover
        })
      })
      
      const data = await res.json()
      
      if (data.success) {
        isSyncingRef.current = true
        
        const catalogRes = await fetch(`${API_URL}/api/catalog`)
        const catalogData = await catalogRes.json()
        const filteredItems = (catalogData.items || []).filter(item => item.type !== 'track')
        const newItemIndex = filteredItems.findIndex(item => item.uri === contextUri)
        
        setCatalog({ ...catalogData, items: filteredItems })
        setTempItem(null)
        
        if (emblaApi && newItemIndex !== -1) {
          setTimeout(() => {
            emblaApi.scrollTo(newItemIndex)
            isSyncingRef.current = false
          }, 50)
        } else {
          isSyncingRef.current = false
        }
      }
    } catch (err) {
      console.error('Error saving:', err)
    }
    
    setSaving(false)
  }

  // Derived state
  const track = nowPlaying?.track
  const isPlaying = nowPlaying?.playing && !!track
  const isPaused = nowPlaying?.paused
  const isBuffering = nowPlaying?.buffering
  
  const formatTime = (ms) => {
    if (!ms) return '0:00'
    const seconds = Math.floor(ms / 1000)
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }
  
  const progress = track?.duration ? (track.position / track.duration) * 100 : 0
  const initialProgressRef = useRef(0)
  const lastTrackUriRef = useRef(null)
  const previousProgressRef = useRef(0)
  
  // Track initial progress when a track starts/resumes
  useEffect(() => {
    const currentTrackUri = track?.uri
    const previousTrackUri = lastTrackUriRef.current
    
    // If track changed, capture initial progress
    if (currentTrackUri && currentTrackUri !== previousTrackUri) {
      initialProgressRef.current = progress
      lastTrackUriRef.current = currentTrackUri
      previousProgressRef.current = progress
    } else if (!currentTrackUri) {
      // Track stopped - reset
      initialProgressRef.current = 0
      lastTrackUriRef.current = null
      previousProgressRef.current = 0
    }
  }, [track?.uri, progress])
  
  // Only animate if playing and progress is increasing beyond initial position
  const isProgressIncreasing = progress > previousProgressRef.current
  const isBeyondInitial = progress > initialProgressRef.current
  const shouldAnimateProgress = isPlaying && isProgressIncreasing && isBeyondInitial
  
  // Update previous progress after render
  useEffect(() => {
    previousProgressRef.current = progress
  }, [progress])
  
  const contextUri = nowPlaying?.context?.uri || ''
  const trackUri = nowPlaying?.track?.uri || ''
  const hasContext = contextUri.includes('playlist') || contextUri.includes('album')
  // Check if context is saved (by context URI or by currentTrack match)
  const isContextSaved = catalog.items.some(item => 
    item.uri === contextUri || item.currentTrack?.uri === trackUri
  )

  // Render album cover
  const renderCover = (item) => {
    const isItemPlaylist = item.uri?.includes('playlist') || item.type === 'playlist'
    
    if (isItemPlaylist) {
      const covers = item.images || []
      return (
        <div className="composite-cover">
          {[0, 1, 2, 3].map((i) => {
            const img = covers[i]
            if (img) {
              const imgSrc = img.startsWith('/') ? `${API_URL}${img}` : img
              return <img key={i} src={imgSrc} alt="" />
            }
            return <div key={i} className="composite-empty" />
          })}
        </div>
      )
    }
    
    if (!item.image) {
      return <div className="slide-img slide-placeholder" />
    }
    const imgSrc = item.image?.startsWith('/') ? `${API_URL}${item.image}` : item.image
    return <img src={imgSrc} alt={item.name} className="slide-img" />
  }

  return (
    <div className="app">
      {displayItems.length > 0 ? (
        <>
          {/* Carousel */}
          <div className="carousel-container">
            <div className="embla" ref={emblaRef}>
              <div className="embla__container">
                {displayItems.map((item, index) => {
                  const isPlayingPaused = isItemPlaying(item) && isPaused
                  return (
                  <div 
                    key={item.id} 
                    className={`embla__slide ${index === selectedIndex ? 'is-selected' : ''} ${item.isTemp ? 'is-temp' : ''} ${isPlayingPaused ? 'is-playing-paused' : ''}`}
                    onClick={() => onSlideClick(index)}
                  >
                    <div className="slide-content">
                      {renderCover(item)}
                      {isItemPlaying(item) && isBuffering && !track && (
                        <div className="buffering-overlay">
                          <div className="buffering-spinner" />
                        </div>
                      )}
                      {item.isTemp && (
                        <button 
                          className="cover-save-btn"
                          onClick={(e) => {
                            e.stopPropagation()
                            saveContext()
                          }}
                          disabled={saving}
                          aria-label="Opslaan in catalogus"
                        >
                          {saving ? (
                            <div className="save-spinner" />
                          ) : (
                            <svg viewBox="0 0 24 24" fill="currentColor">
                              <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
                            </svg>
                          )}
                        </button>
                      )}
                    </div>
                  </div>
                  )
                })}
              </div>
            </div>
          </div>

          {/* Track Info */}
          <div className="track-info">
            {track ? (
              <>
                <h2 className="track-name">{track.name}</h2>
                <p className="artist-name">{track.artist}</p>
              </>
            ) : (
              <>
                <h2 className="track-name">{displayItems[selectedIndex]?.name || 'Kies een album'}</h2>
                <p className="artist-name">{displayItems[selectedIndex]?.artist || ''}</p>
              </>
            )}
          </div>

          {/* Progress Bar */}
          <div className="progress-container">
            <span className="time-current">{formatTime(track?.position)}</span>
            <div className="progress-bar">
              <div 
                className="progress-fill" 
                style={{ 
                  width: `${progress}%`,
                  transition: shouldAnimateProgress ? 'width 1s linear' : 'none'
                }} 
              />
            </div>
            <span className="time-total">{formatTime(track?.duration)}</span>
          </div>

          {/* Playback Controls */}
          <div className="controls">
            <button className="control-btn" onClick={prev} aria-label="Vorige">
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/>
              </svg>
            </button>
            
            <button 
              className="control-btn play-btn" 
              onClick={togglePlayPause}
              aria-label={isPlaying ? 'Pauze' : 'Afspelen'}
            >
              {isPlaying ? (
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z"/>
                </svg>
              )}
            </button>
            
            <button className="control-btn" onClick={next} aria-label="Volgende">
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/>
              </svg>
            </button>
          </div>
        </>
      ) : (
        /* Empty State */
        <div className="empty-state">
          <div className="empty-icon">🎧</div>
          <h2>Nog geen muziek</h2>
          <p>Speel muziek af via Spotify en druk op + om toe te voegen</p>
          
          {track && (
            <div className="current-track-preview">
              <img src={track.albumCover} alt="" className="preview-cover" />
              <div className="preview-info">
                <p className="preview-name">{track.name}</p>
                <p className="preview-artist">{track.artist}</p>
              </div>
              
              <div className="controls compact">
                <button className="control-btn small" onClick={prev}>
                  <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/>
                  </svg>
                </button>
                <button className="control-btn play-btn small" onClick={togglePlayPause}>
                  {isPlaying ? (
                    <svg viewBox="0 0 24 24" fill="currentColor">
                      <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
                    </svg>
                  ) : (
                    <svg viewBox="0 0 24 24" fill="currentColor">
                      <path d="M8 5v14l11-7z"/>
                    </svg>
                  )}
                </button>
                <button className="control-btn small" onClick={next}>
                  <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/>
                  </svg>
                </button>
              </div>
              
              {hasContext && !isContextSaved && (
                <button 
                  className="save-btn-inline"
                  onClick={saveContext}
                  disabled={saving}
                >
                  {saving ? 'Opslaan...' : '+ Toevoegen aan Berry'}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default App
