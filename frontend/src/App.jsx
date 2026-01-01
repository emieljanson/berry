import { useState, useEffect, useCallback, useRef } from 'react'
import useEmblaCarousel from 'embla-carousel-react'
import { api, WS_URL, getImageUrl } from './api'
import './App.css'

// Toast messages
const TOAST = {
  PLAY_FAILED: 'Kan niet afspelen',
  UNAVAILABLE: 'Niet beschikbaar op dit account',
  OFFLINE: 'Geen verbinding',
  SAVE_FAILED: 'Opslaan mislukt',
  DELETE_FAILED: 'Verwijderen mislukt'
}

// Format milliseconds to mm:ss
const formatTime = (ms) => {
  if (!ms) return '0:00'
  const seconds = Math.floor(ms / 1000)
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

// Custom hook for progress bar animation logic
const useProgressAnimation = (track, isPlaying) => {
  const progress = track?.duration ? (track.position / track.duration) * 100 : 0
  const lastPositionRef = useRef(track?.position || 0)
  
  const position = track?.position || 0
  const diff = Math.abs(position - lastPositionRef.current)
  
  // Animate only if playing AND small increment (< 1.5s = normal playback)
  const shouldAnimate = isPlaying && diff > 0 && diff < 1500
  
  // Update ref after render
  useEffect(() => {
    lastPositionRef.current = position
  })
  
  return {
    progress,
    shouldAnimate,
    currentTime: formatTime(track?.position),
    totalTime: formatTime(track?.duration)
  }
}

// Playback controls component - reusable for normal and compact views
const PlaybackControls = ({ isPlaying, onPrev, onToggle, onNext, compact }) => (
  <div className={`controls ${compact ? 'compact' : ''}`}>
    <button className={`control-btn ${compact ? 'small' : ''}`} onClick={onPrev} aria-label="Vorige">
      <svg viewBox="0 0 24 24" fill="currentColor">
        <rect x="6" y="5" width="2.5" height="14" rx="1.25"/>
        <path d="M10.5 11.3c-.3.2-.3.6 0 .9l7 4.8c.4.3 1 0 1-.5V7.5c0-.5-.6-.8-1-.5l-7 4.3z"/>
      </svg>
    </button>
    
    <button 
      className={`control-btn play-btn ${compact ? 'small' : ''}`}
      onClick={onToggle}
      aria-label={isPlaying ? 'Pauze' : 'Afspelen'}
    >
      {isPlaying ? (
        <svg viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="5" width="4" height="14" rx="1.5"/>
          <rect x="14" y="5" width="4" height="14" rx="1.5"/>
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M8 6.82c0-.87.96-1.4 1.7-.94l8.56 5.18c.66.4.66 1.38 0 1.78l-8.56 5.18c-.74.46-1.7-.07-1.7-.94V6.82z"/>
        </svg>
      )}
    </button>
    
    <button className={`control-btn ${compact ? 'small' : ''}`} onClick={onNext} aria-label="Volgende">
      <svg viewBox="0 0 24 24" fill="currentColor">
        <path d="M6.5 7.5c0-.5.6-.8 1-.5l7 4.3c.3.2.3.6 0 .9l-7 4.8c-.4.3-1 0-1-.5V7.5z"/>
        <rect x="15.5" y="5" width="2.5" height="14" rx="1.25"/>
      </svg>
    </button>
  </div>
)

function App() {
  const [catalog, setCatalog] = useState({ items: [] })
  const [nowPlaying, setNowPlaying] = useState(null)
  const [saving, setSaving] = useState(false)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [tempItem, setTempItem] = useState(null)
  const [deleteMode, setDeleteMode] = useState(null) // item id in delete mode
  const [deleting, setDeleting] = useState(false)
  const [toast, setToast] = useState(null) // { message, visible }
  const longPressTimerRef = useRef(null)
  const suppressUntilPlayRef = useRef(false) // Suppress WebSocket updates until user plays something
  const toastTimeoutRef = useRef(null)

  // Show toast notification
  const showToast = useCallback((message) => {
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current)
    setToast({ message, visible: true })
    toastTimeoutRef.current = setTimeout(() => {
      setToast(prev => prev ? { ...prev, visible: false } : null)
    }, 4000)
  }, [])

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
        const data = await api.getCatalog()
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
    let offlineTimeout = null
    
    function connect() {
      console.log('🔌 Connecting to WebSocket...')
      ws = new WebSocket(WS_URL)
      
      ws.onopen = () => {
        console.log('✅ WebSocket connected')
        if (offlineTimeout) {
          clearTimeout(offlineTimeout)
          offlineTimeout = null
        }
      }
      
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          
          // If we receive a "cleared" state from backend (after delete)
          if (data.cleared) {
            suppressUntilPlayRef.current = true
            setNowPlaying(null)
            setTempItem(null)
            return
          }
          
          // If suppressed, ignore updates until user plays something
          if (suppressUntilPlayRef.current) {
            return
          }
          
          setNowPlaying(data)
        } catch (err) {
          console.error('Error parsing WebSocket message:', err)
        }
      }
      
      ws.onclose = () => {
        console.log('WebSocket closed, reconnecting in 1s...')
        reconnectTimeout = setTimeout(connect, 1000)
        // Show offline toast after 5 seconds of disconnect
        offlineTimeout = setTimeout(() => showToast(TOAST.OFFLINE), 5000)
      }
      
      ws.onerror = (err) => {
        console.error('WebSocket error:', err)
        ws.close()
      }
    }
    
    connect()
    
    return () => {
      if (reconnectTimeout) clearTimeout(reconnectTimeout)
      if (offlineTimeout) clearTimeout(offlineTimeout)
      if (ws) ws.close()
    }
  }, [showToast])

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
    
    // Re-enable WebSocket updates (was suppressed after delete)
    suppressUntilPlayRef.current = false
    
    try {
      const result = await api.play(item.uri)
      if (!result.success) {
        // Check if it's an availability issue (audiobooks, geo-restricted)
        if (result.reason === 'unavailable') {
          showToast(TOAST.UNAVAILABLE)
        } else {
          showToast(TOAST.PLAY_FAILED)
        }
      }
    } catch (err) {
      console.error('Error playing:', err)
      showToast(TOAST.PLAY_FAILED)
    }
  }, [isItemPlaying, showToast])

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
    
    const item = displayItemsRef.current[index]
    // If clicking the currently active item, toggle play/pause
    if (item && isItemPlaying(item)) {
      if (nowPlaying?.playing) {
        api.pause()
      } else {
        suppressUntilPlayRef.current = false
        api.resume()
      }
    } else {
      selectAndPlay(item)
    }
  }, [emblaApi, selectAndPlay, isItemPlaying, nowPlaying?.playing])

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
  const pause = () => api.pause()
  const resume = () => api.resume()
  const next = () => api.next()
  
  const prev = () => {
    const position = nowPlaying?.track?.position || 0
    return position < 10000 ? api.prev() : api.seek(0)
  }
  
  const togglePlayPause = () => {
    if (nowPlaying?.playing) {
      return pause()
    } else if (nowPlaying?.paused) {
      // Re-enable WebSocket updates when resuming
      suppressUntilPlayRef.current = false
      return resume()
    } else {
      const selectedItem = displayItems[selectedIndex]
      if (selectedItem) {
        return selectAndPlay(selectedItem)
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
      
      const data = await api.saveToCatalog({
        type: isPlaylist ? 'playlist' : 'album',
        uri: contextUri,
        name: isAlbum ? nowPlaying.track.album : 'Playlist',
        artist: nowPlaying.track.artist,
        album: nowPlaying.track.album,
        image: nowPlaying.track.albumCover
      })
      
      if (data.success) {
        isSyncingRef.current = true
        
        const catalogData = await api.getCatalog()
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
      showToast(TOAST.SAVE_FAILED)
    }
    
    setSaving(false)
  }

  // Delete item from catalog
  const deleteFromCatalog = async (itemId) => {
    setDeleting(true)
    try {
      // Find the index of the item being deleted
      const deleteIndex = displayItems.findIndex(item => item.id === itemId)
      
      // Delete the item - backend handles pause + clear state + broadcast
      const result = await api.deleteFromCatalog(itemId)
      if (result.success) {
        // Refresh catalog
        const data = await api.getCatalog()
        const filteredItems = (data.items || []).filter(item => item.type !== 'track')
        setCatalog({ ...data, items: filteredItems })
        setDeleteMode(null)
        
        // Navigate to next item (or previous if deleting last)
        if (filteredItems.length > 0 && emblaApi) {
          const newIndex = deleteIndex >= filteredItems.length 
            ? filteredItems.length - 1 
            : deleteIndex
          setTimeout(() => {
            emblaApi.scrollTo(newIndex, true)
            setSelectedIndex(newIndex)
          }, 50)
        }
      }
    } catch (err) {
      console.error('Error deleting:', err)
      showToast(TOAST.DELETE_FAILED)
    }
    setDeleting(false)
  }

  // Long press handlers for delete mode
  const handlePressStart = (item) => {
    if (item.isTemp) return // Can't delete temp items
    longPressTimerRef.current = setTimeout(() => {
      setDeleteMode(item.id)
    }, 1000) // 1 second long press
  }

  const handlePressEnd = () => {
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current)
      longPressTimerRef.current = null
    }
  }

  // Cancel delete mode when clicking elsewhere (not on the slide itself or delete button)
  const handleAppClick = (e) => {
    if (deleteMode && !e.target.closest('.cover-delete-btn') && !e.target.closest('.embla__slide')) {
      setDeleteMode(null)
    }
  }

  // Derived state
  const track = nowPlaying?.track
  const isPlaying = nowPlaying?.playing && !!track
  const isPaused = nowPlaying?.paused
  const isBuffering = nowPlaying?.buffering
  
  // Progress bar animation
  const { progress, shouldAnimate, currentTime, totalTime } = useProgressAnimation(track, isPlaying)
  
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
              return <img key={i} src={getImageUrl(img)} alt="" />
            }
            return <div key={i} className="composite-empty" />
          })}
        </div>
      )
    }
    
    if (!item.image) {
      return <div className="slide-img slide-placeholder" />
    }
    return <img src={getImageUrl(item.image)} alt={item.name} className="slide-img" />
  }

  return (
    <div className="app" onClick={handleAppClick}>
      {/* Toast notification */}
      {toast && (
        <div className={`toast ${toast.visible ? 'visible' : ''}`}>
          {toast.message}
        </div>
      )}
      
      {displayItems.length > 0 ? (
        <>
          {/* Carousel */}
          <div className="carousel-container">
            <div className="embla" ref={emblaRef}>
              <div className="embla__container">
                {displayItems.map((item, index) => {
                  const isPlayingPaused = isItemPlaying(item) && isPaused
                  const isInDeleteMode = deleteMode === item.id
                  return (
                  <div 
                    key={item.id} 
                    className={`embla__slide ${index === selectedIndex ? 'is-selected' : ''} ${item.isTemp ? 'is-temp' : ''} ${isPlayingPaused ? 'is-playing-paused' : ''} ${isInDeleteMode ? 'is-delete-mode' : ''}`}
                    onClick={() => !isInDeleteMode && onSlideClick(index)}
                    onPointerDown={() => handlePressStart(item)}
                    onPointerUp={handlePressEnd}
                    onPointerLeave={handlePressEnd}
                    onPointerCancel={handlePressEnd}
                  >
                    <div className="slide-content">
                      {renderCover(item)}
                      {isItemPlaying(item) && isBuffering && !track && (
                        <div className="buffering-overlay">
                          <div className="buffering-spinner" />
                        </div>
                      )}
                      {/* Save button for temp items */}
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
                              <rect x="5" y="10.5" width="14" height="3" rx="1.5"/>
                              <rect x="10.5" y="5" width="3" height="14" rx="1.5"/>
                            </svg>
                          )}
                        </button>
                      )}
                      {/* Delete button for catalog items in delete mode */}
                      {isInDeleteMode && (
                        <button 
                          className="cover-delete-btn"
                          onClick={(e) => {
                            e.stopPropagation()
                            deleteFromCatalog(item.id)
                          }}
                          disabled={deleting}
                          aria-label="Verwijderen uit catalogus"
                        >
                          {deleting ? (
                            <div className="save-spinner" />
                          ) : (
                            <svg viewBox="0 0 24 24" fill="currentColor">
                              <rect x="5" y="10.5" width="14" height="3" rx="1.5"/>
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
            <span className="time-current">{currentTime}</span>
            <div className="progress-bar">
              <div 
                className="progress-fill" 
                style={{ 
                  width: `${progress}%`,
                  transition: shouldAnimate ? 'width 1s linear' : 'none'
                }} 
              />
            </div>
            <span className="time-total">{totalTime}</span>
          </div>

          {/* Playback Controls */}
          <PlaybackControls 
            isPlaying={isPlaying}
            onPrev={prev}
            onToggle={togglePlayPause}
            onNext={next}
          />
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
              
              <PlaybackControls 
                isPlaying={isPlaying}
                onPrev={prev}
                onToggle={togglePlayPause}
                onNext={next}
                compact
              />
              
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
